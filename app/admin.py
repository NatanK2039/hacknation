"""Panel administracyjny: ustawienia, dziennik zdarzeń, statystyki, stan systemu, kopie zapasowe.
Dostęp chroniony hasłem administratora (ADMIN_PASSWORD w .env) – niezależnie od hasła dostępu do aplikacji."""
import contextvars
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from . import ai, db, settings

router = APIRouter(prefix="/api/admin")
STARTED = time.time()
audit_detail = contextvars.ContextVar("audit_detail", default=None)


def add_detail(text):
    """Dopisuje szczegół do wpisu dziennika bieżącego żądania (działa także z wątków puli FastAPI – lista jest współdzielona)."""
    holder = audit_detail.get()
    if holder is not None and text:
        holder.append(str(text))


# ---------------- autoryzacja administratora ----------------

def _token() -> str:
    pw = os.getenv("ADMIN_PASSWORD", "")
    return hmac.new(pw.encode(), b"udip-admin-v1", hashlib.sha256).hexdigest() if pw else ""


def require_admin(x_admin_token: str = Header(default="")):
    t = _token()
    if not t:
        raise HTTPException(503, "Panel administracyjny wyłączony – ustaw ADMIN_PASSWORD w pliku .env na serwerze")
    if not hmac.compare_digest(x_admin_token or "", t):
        raise HTTPException(401, "Wymagane logowanie administratora")
    return True


@router.post("/login")
def login(body: dict = Body(...)):
    pw = os.getenv("ADMIN_PASSWORD", "")
    if not pw:
        raise HTTPException(503, "Panel administracyjny wyłączony – ustaw ADMIN_PASSWORD w pliku .env na serwerze")
    time.sleep(0.4)  # spowolnienie prób zgadywania
    if not hmac.compare_digest(str(body.get("password", "")), pw):
        log("logowanie administratora – nieudane", "admin", None, None, actor=body.get("actor"))
        raise HTTPException(401, "Nieprawidłowe hasło administratora")
    log("logowanie administratora", "admin", None, None, actor=body.get("actor"))
    return {"token": _token()}


# ---------------- dziennik zdarzeń ----------------

ROUTES = [
    ("POST", r"^/api/requests/upload$", "wgranie wniosku", "wniosek"),
    ("PATCH", r"^/api/requests/(\d+)$", "zmiana danych / statusu wniosku", "wniosek"),
    ("DELETE", r"^/api/requests/(\d+)$", "usunięcie wniosku", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/responses$", "dodanie odpowiedzi wydziału", "wniosek"),
    ("DELETE", r"^/api/responses/(\d+)$", "usunięcie odpowiedzi wydziału", "odpowiedź"),
    ("POST", r"^/api/requests/(\d+)/draft$", "wygenerowanie projektu pisma (AI)", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/verify$", "kontrola faktów", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/anonymize/detect$", "wykrycie danych osobowych", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/anonymize/apply$", "zatwierdzenie anonimizacji", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/similar$", "wyszukanie podobnych spraw", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/departments$", "propozycja wydziałów (AI)", "wniosek"),
    ("GET", r"^/api/requests/(\d+)/docx$", "eksport pisma do DOCX", "wniosek"),
    ("GET", r"^/api/requests/(\d+)/txt$", "eksport pisma do TXT", "wniosek"),
    ("POST", r"^/api/requests/(\d+)/letters$", "przygotowanie pisma (kwerenda / doprecyzowanie / odmowa)", "wniosek"),
    ("PATCH", r"^/api/letters/(\d+)$", "edycja pisma", "pismo"),
    ("DELETE", r"^/api/letters/(\d+)$", "usunięcie pisma", "pismo"),
    ("GET", r"^/api/letters/(\d+)/docx$", "eksport pisma do DOCX", "pismo"),
    ("POST", r"^/api/letters/(\d+)/apply$", "zastosowanie nowego terminu z pisma", "pismo"),
    ("POST", r"^/api/recognize$", "rozpoznanie dokumentu", "dokument"),
    ("PATCH", r"^/api/documents/(\d+)$", "korekta rozpoznanego dokumentu", "dokument"),
    ("DELETE", r"^/api/documents/(\d+)$", "usunięcie dokumentu", "dokument"),
    ("POST", r"^/api/documents/(\d+)/action$", "akcja na dokumencie", "dokument"),
    ("POST", r"^/api/documents/import-archive$", "import archiwum", "archiwum"),
    ("POST", r"^/api/archive$", "dodanie sprawy do archiwum", "archiwum"),
    ("DELETE", r"^/api/archive/(\d+)$", "usunięcie sprawy z archiwum", "archiwum"),
    ("POST", r"^/api/knowledge$", "wgranie dokumentu do bazy wiedzy", "baza wiedzy"),
    ("DELETE", r"^/api/knowledge/(\d+)$", "usunięcie dokumentu z bazy wiedzy", "baza wiedzy"),
    ("POST", r"^/api/anonymize$", "anonimizacja dokumentu (analiza)", "dokument"),
    ("POST", r"^/api/anonymize/apply$", "anonimizacja dokumentu (zatwierdzenie)", "dokument"),
    ("POST", r"^/api/anonymize/docx$", "eksport zanonimizowanego dokumentu", "dokument"),
    ("PUT", r"^/api/admin/settings$", "zmiana ustawień", "ustawienia"),
    ("PUT", r"^/api/admin/departments$", "zmiana słownika komórek", "ustawienia"),
    ("POST", r"^/api/admin/departments/reset$", "przywrócenie słownika komórek z BIP", "ustawienia"),
    ("GET", r"^/api/admin/backup$", "pobranie kopii zapasowej", "system"),
    ("POST", r"^/api/admin/backup/create$", "utworzenie kopii zapasowej", "system"),
    ("POST", r"^/api/admin/restore$", "przywrócenie kopii zapasowej", "system"),
    ("POST", r"^/api/admin/reset-demo$", "reset do danych demonstracyjnych", "system"),
    ("POST", r"^/api/admin/test-ai$", "test połączenia z AI", "system"),
]


def now_local() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.getenv("APP_TZ", "Europe/Warsaw"))).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(action, object_type=None, object_id=None, details=None, actor=None, method=None, path=None, status=None, ip=None):
    try:
        with db.conn() as c:
            c.execute("INSERT INTO audit(at,actor,action,object_type,object_id,details,method,path,status,ip) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (now_local(), (actor or "—")[:60], action, object_type, None if object_id is None else str(object_id),
                       details if isinstance(details, (str, type(None))) else json.dumps(details, ensure_ascii=False),
                       method, path, status, ip))
    except Exception:
        pass


async def audit_middleware(request: Request, call_next):
    holder = []
    token = audit_detail.set(holder)
    resp = await call_next(request)
    try:
        path, method = request.url.path, request.method
        for m, rx, label, otype in ROUTES:
            if m == method:
                mm = re.match(rx, path)
                if mm:
                    from urllib.parse import unquote
                    actor = unquote(request.headers.get("x-user", "")) or "jury"
                    if resp.status_code >= 400:
                        label += " – BŁĄD"
                    log(label, otype, mm.group(1) if mm.groups() else None, "; ".join(holder) or None, actor, method, path,
                        resp.status_code, request.headers.get("x-forwarded-for", request.client.host if request.client else None))
                    break
    finally:
        audit_detail.reset(token)
    return resp


@router.get("/audit", dependencies=[Depends(require_admin)])
def audit_list(q: str = "", actor: str = "", object_type: str = "", limit: int = 300, format: str = "json"):
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if q:
        sql += " AND (action LIKE ? OR details LIKE ? OR object_id = ?)"; args += [f"%{q}%", f"%{q}%", q]
    if actor:
        sql += " AND actor = ?"; args.append(actor)
    if object_type:
        sql += " AND object_type = ?"; args.append(object_type)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(min(int(limit), 5000))
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
        actors = [r[0] for r in c.execute("SELECT DISTINCT actor FROM audit ORDER BY actor").fetchall()]
    if format == "csv":
        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "czas", "użytkownik", "zdarzenie", "obiekt", "id obiektu", "szczegóły", "status HTTP", "IP"])
        for r in rows:
            w.writerow([r["id"], r["at"], r["actor"], r["action"], r["object_type"], r["object_id"], r["details"], r["status"], r["ip"]])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="dziennik_zdarzen_{date.today()}.csv"'})
    return {"items": rows, "actors": actors}


# ---------------- ustawienia ----------------

@router.get("/settings", dependencies=[Depends(require_admin)])
def settings_get():
    return {"values": settings.all_settings(), "schema": settings.SCHEMA, "defaults": settings.defaults(),
            "api_key_set": bool(os.getenv("OPENAI_API_KEY")),
            "api_key_hint": ("…" + os.getenv("OPENAI_API_KEY", "")[-4:]) if os.getenv("OPENAI_API_KEY") else None}


@router.put("/settings", dependencies=[Depends(require_admin)])
def settings_put(body: dict = Body(...)):
    try:
        changed = settings.set_many(body.get("values", {}))
    except ValueError as e:
        raise HTTPException(400, str(e))
    add_detail(json.dumps(changed, ensure_ascii=False) if changed else "bez zmian")
    return {"changed": changed, **settings_get()}


# ---------------- słowniki: komórki organizacyjne ----------------

@router.get("/departments", dependencies=[Depends(require_admin)])
def departments_get():
    from .main import depts, DEPTS_FILE
    with db.conn() as c:
        custom = c.execute("SELECT 1 FROM settings WHERE key='departments_json'").fetchone() is not None
    src = json.load(open(DEPTS_FILE, encoding="utf-8")).get("source", "")
    return {"items": depts(), "custom": custom, "source": src}


@router.put("/departments", dependencies=[Depends(require_admin)])
def departments_put(body: dict = Body(...)):
    items, seen, out = body.get("items") or [], set(), []
    for i, d in enumerate(items, 1):
        code = str(d.get("code", "")).strip()
        name = str(d.get("name", "")).strip()
        if not code and not name:
            continue
        if not re.fullmatch(r"[\wĄĆĘŁŃÓŚŹŻąćęłńóśźż.\-]{1,16}", code):
            raise HTTPException(400, f"Wiersz {i}: nieprawidłowy symbol „{code}” (litery, cyfry, kropka, myślnik; maks. 16 znaków)")
        if code.upper() in seen:
            raise HTTPException(400, f"Wiersz {i}: symbol „{code}” występuje więcej niż raz")
        if not name:
            raise HTTPException(400, f"Wiersz {i}: brak nazwy komórki")
        url = str(d.get("url", "")).strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, f"Wiersz {i}: adres strony musi zaczynać się od http:// lub https://")
        seen.add(code.upper())
        out.append({"code": code, "name": name, "scope": str(d.get("scope", "")).strip(), "url": url, **({"external": True} if d.get("external") else {})})
    if not out:
        raise HTTPException(400, "Lista komórek nie może być pusta")
    with db.conn() as c:
        old = c.execute("SELECT value FROM settings WHERE key='departments_json'").fetchone()
        c.execute("INSERT INTO settings(key,value) VALUES('departments_json',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(out, ensure_ascii=False),))
    from .main import depts
    before = {d["code"] for d in (json.loads(old["value"]) if old else [])} or None
    after = {d["code"] for d in out}
    add_detail(f"{len(out)} komórek" + (f"; dodano: {', '.join(sorted(after - before))}" if before and after - before else "")
               + (f"; usunięto: {', '.join(sorted(before - after))}" if before and before - after else ""))
    return departments_get()


@router.post("/departments/reset", dependencies=[Depends(require_admin)])
def departments_reset():
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='departments_json'")
    add_detail("przywrócono listę z BIP")
    return departments_get()


# ---------------- stan systemu ----------------

def _dir_size(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


@router.get("/system", dependencies=[Depends(require_admin)])
def system():
    dbp = os.path.abspath(db.DB_PATH)
    prev = os.path.join(os.path.dirname(dbp), "previews")
    try:
        tv = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=5).stdout.splitlines()[0]
        langs = [x.strip() for x in subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5).stdout.splitlines()[1:] if x.strip()]
    except Exception as e:
        tv, langs = f"niedostępny ({e})", []
    du = shutil.disk_usage(os.path.dirname(dbp))
    with db.conn() as c:
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("requests", "responses", "documents", "archive", "knowledge", "audit", "ai_usage")}
        last_ai = c.execute("SELECT at, model, purpose, seconds FROM ai_usage ORDER BY id DESC LIMIT 1").fetchone()
    backups = _list_backups()
    return {
        "app": {"uptime_s": int(time.time() - STARTED), "started": datetime.fromtimestamp(STARTED).isoformat(timespec="seconds"),
                "version": os.getenv("APP_VERSION", "prototyp HackNation")},
        "ai": {"enabled": ai.enabled(), "provider": ai.provider_label(), "model": ai.model(), "vision_model": ai.vision_model(),
               "last_call": dict(last_ai) if last_ai else None},
        "ocr": {"tesseract": tv, "languages": langs, "timeout_s": settings.get_int("ocr_timeout", 25),
                "omp_thread_limit": os.getenv("OMP_THREAD_LIMIT")},
        "storage": {"db_bytes": os.path.getsize(dbp) if os.path.exists(dbp) else 0, "previews_bytes": _dir_size(prev) if os.path.isdir(prev) else 0,
                    "disk_free_bytes": du.free, "disk_total_bytes": du.total},
        "counts": counts, "backups": backups,
        "security": {"admin_password_set": bool(os.getenv("ADMIN_PASSWORD")), "api_key_set": bool(os.getenv("OPENAI_API_KEY")),
                     "auto_send": False},
    }


@router.post("/test-ai", dependencies=[Depends(require_admin)])
def test_ai():
    if not ai.enabled():
        raise HTTPException(400, "AI wyłączone – brak klucza API lub adresu lokalnego serwera")
    t0 = time.time()
    try:
        out = ai.chat_text("Odpowiadasz jednym słowem.", "Napisz: OK", temperature=0, purpose="test połączenia")
    except Exception as e:
        add_detail(f"błąd: {e}")
        raise HTTPException(502, f"Błąd połączenia z modelem {ai.model()}: {e}")
    dt = round(time.time() - t0, 2)
    add_detail(f"{ai.model()} · {dt} s")
    return {"ok": True, "model": ai.model(), "provider": ai.provider_label(), "seconds": dt, "answer": out[:40]}


# ---------------- statystyki ----------------

@router.get("/stats", dependencies=[Depends(require_admin)])
def stats(year: int = None):
    year = year or date.today().year
    with db.conn() as c:
        reqs = [db.row(r) for r in c.execute("SELECT * FROM requests WHERE year=?", (year,)).fetchall()]
        usage = [dict(r) for r in c.execute("""SELECT model, purpose, COUNT(*) n, SUM(prompt_tokens) pt, SUM(completion_tokens) ct,
                                               ROUND(AVG(seconds),1) avg_s FROM ai_usage GROUP BY model, purpose ORDER BY n DESC""").fetchall()]
        docs = c.execute("SELECT COUNT(*), SUM(handwritten) FROM documents").fetchone()
        methods = {}
        for (m,) in c.execute("SELECT methods FROM documents").fetchall():
            for x in json.loads(m or "[]"):
                methods[x] = methods.get(x, 0) + 1
    today = date.today()
    by_status, by_month, deps, types = {}, {}, {}, {}
    answered, on_time, days_to_answer = 0, 0, []
    overdue_open = 0
    for r in reqs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        m = (r.get("received_date") or "")[:7]
        by_month[m] = by_month.get(m, 0) + 1
        types[r.get("requester_type") or "inny"] = types.get(r.get("requester_type") or "inny", 0) + 1
        d = r.get("departments")
        if isinstance(d, dict) and d.get("departments"):
            code = d["departments"][0].get("code")
            deps[code] = deps.get(code, 0) + 1
        if r["status"] == "wyslana" and r.get("answered_date") and r.get("received_date"):
            answered += 1
            ad, rd, dl = date.fromisoformat(r["answered_date"]), date.fromisoformat(r["received_date"]), date.fromisoformat(r["deadline"])
            days_to_answer.append((ad - rd).days)
            on_time += ad <= dl
        elif r["status"] != "wyslana" and r.get("deadline") and date.fromisoformat(r["deadline"]) < today:
            overdue_open += 1
    tokens_in = sum(u["pt"] or 0 for u in usage)
    tokens_out = sum(u["ct"] or 0 for u in usage)
    return {
        "year": year, "total": len(reqs), "by_status": by_status, "by_month": dict(sorted(by_month.items())),
        "requester_types": types, "top_departments": dict(sorted(deps.items(), key=lambda x: -x[1])[:8]),
        "answered": answered, "on_time": on_time, "on_time_pct": round(100 * on_time / answered, 1) if answered else None,
        "avg_days_to_answer": round(sum(days_to_answer) / len(days_to_answer), 1) if days_to_answer else None,
        "overdue_open": overdue_open,
        "documents": {"total": docs[0] or 0, "handwritten": docs[1] or 0, "methods": methods},
        "ai_usage": usage, "ai_tokens": {"input": tokens_in, "output": tokens_out, "calls": sum(u["n"] for u in usage)},
    }


# ---------------- kopie zapasowe ----------------

def _backup_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _list_backups():
    d = _backup_dir()
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith(".db"):
            out.append({"name": f, "bytes": os.path.getsize(os.path.join(d, f))})
    return out[:30]


def make_backup(tag="manual") -> str:
    name = f"udip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.db"
    dst = os.path.join(_backup_dir(), name)
    src = sqlite3.connect(db.DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)  # spójna kopia także podczas pracy aplikacji
    src.close(); out.close()
    return dst


@router.post("/backup/create", dependencies=[Depends(require_admin)])
def backup_create():
    p = make_backup("reczna")
    add_detail(os.path.basename(p))
    return {"ok": True, "name": os.path.basename(p), "backups": _list_backups()}


@router.get("/backup", dependencies=[Depends(require_admin)])
def backup_download(name: str = ""):
    if name:
        if not re.fullmatch(r"udip_[\w-]+\.db", name):
            raise HTTPException(400, "Nieprawidłowa nazwa kopii")
        p = os.path.join(_backup_dir(), name)
        if not os.path.exists(p):
            raise HTTPException(404, "Brak kopii")
    else:
        p = make_backup("pobrana")
    add_detail(os.path.basename(p))
    return FileResponse(p, media_type="application/octet-stream", filename=os.path.basename(p))


REQUIRED_TABLES = {"requests", "responses", "archive", "knowledge"}


@router.post("/restore", dependencies=[Depends(require_admin)])
async def restore(file: UploadFile = File(None), name: str = ""):
    if file is not None and file.filename:
        data = await file.read()
        if not data.startswith(b"SQLite format 3"):
            raise HTTPException(400, "To nie jest plik bazy SQLite")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db"); tmp.write(data); tmp.close()
        src = tmp.name
    elif name and re.fullmatch(r"udip_[\w-]+\.db", name):
        src = os.path.join(_backup_dir(), name)
        if not os.path.exists(src):
            raise HTTPException(404, "Brak kopii")
    else:
        raise HTTPException(400, "Wskaż plik kopii lub kopię z listy")
    try:
        t = sqlite3.connect(src)
        tables = {r[0] for r in t.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        t.execute("PRAGMA integrity_check").fetchone()
        t.close()
    except Exception as e:
        raise HTTPException(400, f"Uszkodzony plik bazy: {e}")
    if not REQUIRED_TABLES <= tables:
        raise HTTPException(400, f"Plik nie jest kopią Asystenta UDIP (brak tabel: {', '.join(REQUIRED_TABLES - tables)})")
    safety = make_backup("przed_przywroceniem")
    s = sqlite3.connect(src); d = sqlite3.connect(db.DB_PATH)
    with d:
        s.backup(d)
    s.close(); d.close()
    db.init()  # dołóż ewentualnie brakujące tabele/kolumny z nowszej wersji
    settings._cache["t"] = 0
    add_detail(f"źródło: {file.filename if file is not None and file.filename else name}; kopia bezpieczeństwa: {os.path.basename(safety)}")
    return {"ok": True, "safety_backup": os.path.basename(safety)}


@router.post("/reset-demo", dependencies=[Depends(require_admin)])
def reset_demo(body: dict = Body(default={})):
    if body.get("confirm") != "RESET":
        raise HTTPException(400, "Potwierdź wpisując RESET")
    safety = make_backup("przed_resetem")
    from . import seed
    with db.conn() as c:
        for t in ("responses", "requests", "documents", "archive", "knowledge"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('responses','requests','documents','archive','knowledge')")
    seed.run()
    add_detail(f"kopia bezpieczeństwa: {os.path.basename(safety)}")
    return {"ok": True, "safety_backup": os.path.basename(safety)}
