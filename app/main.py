import json
import os
import re
from datetime import date, datetime

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, admin, anonymize, auth, db, letters, seed, settings
from .verify import verify as verify_draft
from .deadlines import compute_deadline, is_working_day
from datetime import timedelta
from .docx_export import build_docx
from .extract import extract_text as _extract_basic
from . import recognize as rec

def extract_text(filename, data):
    """Wspólny odczyt: warstwa tekstowa -> OCR -> AI-vision (pismo odręczne, słabe skany)."""
    try:
        return rec.recognize(filename, data, keep_images=False)["text"]
    except Exception:
        return _extract_basic(filename, data)


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPTS_FILE = os.path.join(BASE, "data", "departments.json")


def depts() -> list:
    """Lista komórek: wersja edytowana w panelu administracyjnym (baza) albo domyślna z BIP (plik)."""
    try:
        with db.conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key='departments_json'").fetchone()
        if row and row["value"]:
            return json.loads(row["value"])
    except Exception:
        pass
    return json.load(open(DEPTS_FILE, encoding="utf-8"))["departments"]
STATUSES = {"nowy": "a) Brak działań", "w_wydziale": "b) Zapytania wysłane do wydziałów", "kwerenda": "c) Kwerenda / przedłużenie",
            "przygotowana": "d) Odpowiedź przygotowana", "wyslana": "e) Odpowiedź wysłana"}

app = FastAPI(title="Asystent UDIP – UM Bydgoszcz", docs_url=None, redoc_url=None, openapi_url=None)

MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED_EXT = (".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic")


async def read_upload(f) -> bytes:
    """Wczytuje wgrany plik z limitem rozmiaru i listą dozwolonych typów."""
    name = (f.filename or "").lower()
    data = await f.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Plik jest za duży (limit 25 MB)")
    is_img = data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
    if not (name.endswith(ALLOWED_EXT) or (is_img and "." not in name)):
        raise HTTPException(415, "Niedozwolony typ pliku. Dozwolone: PDF, DOCX, TXT oraz obrazy (JPG, PNG, TIFF, WEBP, HEIC)")
    return data
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(auth.admin_router)
app.middleware("http")(admin.audit_middleware)
app.middleware("http")(auth.auth_middleware)  # dodane jako ostatnie = wykonywane jako pierwsze
PREV_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "previews")


@app.on_event("startup")
def startup():
    db.init()
    auth.bootstrap()
    if os.getenv("SEED_DEMO", "1") == "1":
        seed.run()
    try:  # automatyczna kopia zapasowa raz dziennie przy starcie + rotacja (30 ostatnich)
        today = date.today().strftime("%Y%m%d")
        if not any(b["name"].startswith(f"udip_{today}") and "auto" in b["name"] for b in admin._list_backups()):
            admin.make_backup("auto")
        bd = admin._backup_dir()
        for f in sorted(os.listdir(bd))[:-30]:
            os.remove(os.path.join(bd, f))
    except Exception:
        pass


# ---------------- pomocnicze ----------------

STOP = set("oraz jakie jaka jaki który która które proszę wnoszę informację informacji udostępnienie podstawie ustawy dostępie "
           "publicznej publiczne miasta miasto urząd urzędu bydgoszczy bydgoszcz roku latach wraz także również przez".split())


def fts_query(text: str) -> str:
    words = re.findall(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{4,}", text.lower())
    seen, toks = set(), []
    for w in words:
        if w in STOP:
            continue
        stem = w[:6]  # prosta "stemizacja" przez prefiks – fleksja polska
        if stem not in seen:
            seen.add(stem)
            toks.append(f'"{stem}"*')
        if len(toks) >= 25:
            break
    return " OR ".join(toks)


def find_similar(text: str, limit: int = 5, use_ai: bool = True) -> list:
    q = fts_query(text)
    if not q:
        return []
    with db.conn() as c:
        rows = c.execute("""SELECT a.*, bm25(archive_fts, 3.0, 1.0, 0.5) AS rank FROM archive_fts JOIN archive a ON a.id = archive_fts.rowid
                            WHERE archive_fts MATCH ? ORDER BY rank LIMIT 12""", (q,)).fetchall()
    cands = [dict(r) for r in rows]
    year_ago = date.today().replace(year=date.today().year - 1).isoformat()
    for cnd in cands:
        cnd["last_year"] = cnd["received_date"] >= year_ago
    if use_ai and ai.enabled() and cands:
        try:
            ranked = ai.rerank_similar(text, cands)
            byid = {cnd["id"]: cnd for cnd in cands}
            out = []
            for it in ranked:
                cnd = byid.get(int(it.get("id", -1)))
                if cnd:
                    cnd["score"] = it.get("score")
                    cnd["why"] = it.get("why")
                    out.append(cnd)
            if out:
                return out[:limit]
        except Exception:
            pass
    for i, cnd in enumerate(cands):
        cnd["score"] = max(30, 90 - i * 10)
        cnd["why"] = "dopasowanie słów kluczowych"
    return cands[:limit]


def get_req(rid: int) -> dict:
    with db.conn() as c:
        r = db.row(c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone())
        if not r:
            raise HTTPException(404, "Brak wniosku")
        r["responses"] = [dict(x) for x in c.execute("SELECT * FROM responses WHERE request_id=? ORDER BY id", (rid,)).fetchall()]
        r["letters"] = [dict(x) for x in c.execute("SELECT * FROM letters WHERE request_id=? ORDER BY id DESC", (rid,)).fetchall()]
    if r.get("draft"):
        try:
            r["verification"] = verify_draft(r["draft"], r["responses"], r.get("content") or "")
        except Exception as e:
            r["verification"] = {"error": str(e)}
    return decorate(r)


def _wd_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if is_working_day(d):
            n += 1
    return n


def _minus_working_days(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if is_working_day(d):
            n -= 1
    return d


def knowledge_context(limit: int = 14000) -> str:
    with db.conn() as c:
        rows = c.execute("SELECT title, kind, text FROM knowledge ORDER BY CASE kind WHEN 'zarządzenie' THEN 0 WHEN 'instrukcja' THEN 1 WHEN 'ustawa' THEN 2 ELSE 3 END, id").fetchall()
    out, used = [], 0
    for r in rows:
        if r["title"].startswith("Dane podlegające anonimizacji"):
            continue  # lista używana w module anonimizacji
        chunk = f"### {r['title']} ({r['kind']})\n{r['text']}"
        if used + len(chunk) > limit:
            chunk = chunk[:max(0, limit - used)]
        if chunk:
            out.append(chunk); used += len(chunk)
        if used >= limit:
            break
    return "\n\n".join(out)


def decorate(r: dict) -> dict:
    today = date.today()
    if r.get("deadline"):
        dl = date.fromisoformat(r["deadline"])
        r["days_left"] = (dl - today).days
        done = r.get("status") == "wyslana"
        if done:
            r["urgency"] = "done"
        elif r["days_left"] < 0:
            r["urgency"] = "overdue"
        elif r["days_left"] == 0:
            r["urgency"] = "today"
        elif _wd_between(today, dl) == 1:
            r["urgency"] = "tomorrow"
        elif r["days_left"] <= 3:
            r["urgency"] = "soon"
        else:
            r["urgency"] = "ok"
        r["dept_deadline"] = _minus_working_days(dl, settings.get_int("dept_lead_days", 3)).isoformat()
        if r.get("received_date"):
            from .deadlines import add_months, next_working_day
            r["max_deadline"] = next_working_day(add_months(date.fromisoformat(r["received_date"]), 2)).isoformat()
        r["days_since"] = (today - date.fromisoformat(r["received_date"])).days if r.get("received_date") else None
    r["status_label"] = STATUSES.get(r.get("status"), r.get("status"))
    return r


def update_req(rid: int, fields: dict, note: str = None):
    if note:
        admin.add_detail(note)
    if not fields and not note:
        return
    with db.conn() as c:
        if note or "status" in fields:
            hist = db.row(c.execute("SELECT history FROM requests WHERE id=?", (rid,)).fetchone())["history"] or []
            hist.append({"at": datetime.now().isoformat(timespec="minutes"), "status": fields.get("status"), "note": note or ""})
            fields["history"] = hist
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = [db.dumps(v) if k in db.JSON_COLS else v for k, v in fields.items()]
        c.execute(f"UPDATE requests SET {sets} WHERE id=?", (*vals, rid))


# ---------------- API ----------------

@app.get("/api/config")
def config():
    return {"ai": ai.enabled(), "model": ai.model() if ai.enabled() else None,
            "provider": ai.provider_label(), "admin_enabled": True,
            "dept_lead_days": settings.get_int("dept_lead_days", 3),
            "statuses": STATUSES, "today": date.today().isoformat()}


@app.get("/api/departments")
def departments():
    return depts()


@app.get("/api/requests")
def list_requests(year: int = None):
    with db.conn() as c:
        if year:
            rows = c.execute("SELECT * FROM requests WHERE year=? ORDER BY received_date ASC, id ASC", (year,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM requests ORDER BY received_date ASC, id ASC").fetchall()
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM requests ORDER BY year DESC").fetchall()]
    items = [decorate(db.row(r)) for r in rows]
    u = auth.current_user.get()
    if u and u["role"] == "wydzial":
        with db.conn() as c:
            resp_by = {}
            for rr in c.execute("SELECT request_id, department FROM responses").fetchall():
                resp_by.setdefault(rr["request_id"], []).append({"department": rr["department"]})
        items = [it for it in items if auth.visible_to_dept({**it, "responses": resp_by.get(it["id"], [])}, u["dept_code"])]
    for it in items:
        it.pop("content", None); it.pop("draft", None); it.pop("final_text", None)
    return {"items": items, "years": years}


def _check_dept_access(r: dict):
    u = auth.current_user.get()
    if u and u["role"] == "wydzial" and not auth.visible_to_dept(r, u["dept_code"]):
        raise HTTPException(403, "Ta sprawa nie jest przypisana do Twojej komórki")


@app.get("/api/requests/{rid}")
def request_detail(rid: int):
    r = get_req(rid)
    _check_dept_access(r)
    u = auth.current_user.get()
    if u and u["role"] == "wydzial":  # wydział nie widzi projektu pisma, pism procesowych ani odpowiedzi innych komórek
        for k in ("draft", "final_text", "anon_spans", "verification", "letters", "similar"):
            r.pop(k, None)
        r["responses"] = [x for x in r["responses"] if x.get("department") == u["dept_code"]]
    return r


@app.post("/api/requests/upload")
async def upload_request(file: UploadFile = File(...)):
    import time
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    data = await read_upload(file)
    fname = file.filename or "zdjecie.jpg"
    try:
        r = rec.recognize(fname, data)
    except Exception as e:
        raise HTTPException(400, f"Nie udało się odczytać pliku: {e}")
    imgs = r.pop("_images", [])
    text = r["text"]
    if not text.strip():
        raise HTTPException(400, "Nie udało się odczytać tekstu z pliku")
    t_rec = time.time() - t0
    fields, dep, similar = {}, {}, []
    with ThreadPoolExecutor(3) as ex:  # równolegle: ekstrakcja danych, wydziały, podobne sprawy
        f1 = ex.submit(ai.extract_request_fields, text) if ai.enabled() else None
        f2 = ex.submit(ai.suggest_departments, text, "", depts()) if ai.enabled() else None
        f3 = ex.submit(find_similar, text)
        try:
            fields = f1.result() if f1 else {}
        except Exception as e:
            fields = {"summary": f"(AI niedostępne: {e})"}
        try:
            dep = f2.result() if f2 else {}
        except Exception as e:
            dep = {"departments": [], "error": str(e)}
        try:
            similar = f3.result()
        except Exception:
            similar = []
    today = date.today()
    rdd, rd_source = today, "dzień rejestracji"
    if r.get("received_date"):  # pieczęć wpływu kancelarii – najbardziej wiarygodna
        try:
            d = date.fromisoformat(r["received_date"])
            if d <= today:
                rdd, rd_source = d, "pieczęć wpływu"
        except Exception:
            pass
    if rd_source == "dzień rejestracji" and fields.get("received_date"):
        try:
            d = date.fromisoformat(fields["received_date"])
            if d <= today and (today - d).days <= 60:
                rdd, rd_source = d, "data pisma"
        except Exception:
            pass
    dl = compute_deadline(rdd)
    total = round(time.time() - t0, 1)
    recog = {"methods": r["methods"], "handwritten": r["handwritten"], "conf": r["conf"], "entry_number": r.get("entry_number"),
             "stamp_date": r.get("received_date"), "received_source": rd_source, "seconds": total, "seconds_recognition": round(t_rec, 1),
             "stamps": [st for p in r["pages"] for st in (p.get("stamps") or [])], "pages": len(r["pages"]), "signature": r.get("signature")}
    os.makedirs(PREV_DIR, exist_ok=True)
    with db.conn() as c:
        cur = c.execute("""INSERT INTO documents(filename,doc_type,signature,received_date,entry_number,handwritten,conf,methods,pages,text,n_pages,status)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (fname, "wniosek", r.get("signature"), r.get("received_date"), r.get("entry_number"), int(r["handwritten"]), r["conf"],
                         db.dumps(r["methods"]), db.dumps(r["pages"]), text, len(r["pages"]), "wniosek utworzony"))
        doc_id = cur.lastrowid
        lp = (c.execute("SELECT COALESCE(MAX(lp),0) FROM requests WHERE year=?", (rdd.year,)).fetchone()[0] or 0) + 1
        number = r.get("signature") or fields.get("number") or f"{settings.get('case_prefix')}{lp}.{rdd.year}"
        note = f"wpłynął plik {fname}" + (f" · rozpoznano pismo odręczne (AI-vision)" if r["handwritten"] else "") + \
               (f" · pieczęć wpływu {rdd.strftime('%d.%m.%Y')}" + (f", nr {r.get('entry_number')}" if r.get("entry_number") else "") if rd_source == "pieczęć wpływu" else "")
        cur = c.execute("""INSERT INTO requests(year,lp,number,received_date,deadline,status,requester,requester_type,delivery,subject,summary,
                           questions,content,filename,departments,similar,history,recognition,doc_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rdd.year, lp, number, rdd.isoformat(), dl.isoformat(), "nowy",
                         fields.get("requester"), fields.get("requester_type"), fields.get("delivery"),
                         fields.get("subject") or fname, fields.get("summary"), db.dumps(fields.get("questions") or []),
                         text, fname, db.dumps(dep), db.dumps(similar),
                         db.dumps([{"at": datetime.now().isoformat(timespec="minutes"), "status": "nowy", "note": note}]), db.dumps(recog), doc_id))
        rid = cur.lastrowid
        c.execute("UPDATE documents SET request_id=? WHERE id=?", (rid, doc_id))
    admin.add_detail(f"{number} · {fields.get('subject') or fname} · {', '.join(r['methods'])}{' · pismo odręczne' if r['handwritten'] else ''} · {total} s")
    for i, im in enumerate(imgs[:10]):
        try:
            im2 = im.convert("RGB"); im2.thumbnail((1100, 1500)); im2.save(os.path.join(PREV_DIR, f"{doc_id}-{i+1}.jpg"), quality=80)
        except Exception:
            pass
    return get_req(rid)


@app.patch("/api/requests/{rid}")
def patch_request(rid: int, body: dict = Body(...)):
    allowed = {"number", "received_date", "extended_days", "status", "initials", "requester", "subject", "summary", "notes",
               "chosen_departments", "answered_date", "draft", "lp"}
    fields = {k: v for k, v in body.items() if k in allowed}
    cur = get_req(rid)
    if "received_date" in fields or "extended_days" in fields:
        rd = date.fromisoformat(fields.get("received_date") or cur["received_date"])
        ext = int(fields.get("extended_days", cur.get("extended_days") or 0) or 0)
        fields["deadline"] = compute_deadline(rd, ext).isoformat()
        fields["year"] = rd.year
        if ext and fields.get("status") is None and cur["status"] in ("nowy", "w_wydziale"):
            fields["status"] = "kwerenda"
    if fields.get("status") == "wyslana" and not fields.get("answered_date"):
        fields["answered_date"] = date.today().isoformat()
    note = None
    if "status" in fields and fields["status"] != cur["status"]:
        note = f"status: {STATUSES.get(fields['status'])}"
    update_req(rid, fields, note)
    return get_req(rid)


@app.delete("/api/requests/{rid}")
def delete_request(rid: int):
    try:
        r0 = get_req(rid); admin.add_detail(f"{r0.get('number')} · {r0.get('subject')}")
    except HTTPException:
        pass
    with db.conn() as c:
        c.execute("DELETE FROM responses WHERE request_id=?", (rid,))
        c.execute("DELETE FROM requests WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/requests/{rid}/responses")
async def add_response(rid: int, department: str = Form(""), text: str = Form(""), file: UploadFile = File(None)):
    _check_dept_access(get_req(rid))
    u = auth.current_user.get()
    if u and u["role"] == "wydzial":
        department = u["dept_code"]  # wydział może dodać odpowiedź tylko w swoim imieniu
    fname = None
    if file is not None and file.filename:
        data = await read_upload(file)
        text = (text + "\n" if text else "") + extract_text(file.filename, data)
        fname = file.filename
    if not text.strip():
        raise HTTPException(400, "Pusta odpowiedź")
    with db.conn() as c:
        c.execute("INSERT INTO responses(request_id,department,filename,text) VALUES(?,?,?,?)", (rid, department or "wydział", fname, text))
    cur = get_req(rid)
    if cur["status"] in ("nowy",):
        update_req(rid, {"status": "w_wydziale"}, f"wpłynęła odpowiedź: {department}")
    else:
        update_req(rid, {}, f"wpłynęła odpowiedź: {department}")
    return request_detail(rid)


@app.delete("/api/responses/{resp_id}")
def delete_response(resp_id: int):
    u = auth.current_user.get()
    if u and u["role"] == "wydzial":
        with db.conn() as c:
            rr = c.execute("SELECT department FROM responses WHERE id=?", (resp_id,)).fetchone()
        if not rr or rr["department"] != u["dept_code"]:
            raise HTTPException(403, "Możesz usunąć tylko odpowiedź swojej komórki")
    with db.conn() as c:
        c.execute("DELETE FROM responses WHERE id=?", (resp_id,))
    return {"ok": True}


@app.post("/api/requests/{rid}/similar")
def refresh_similar(rid: int):
    r = get_req(rid)
    sim = find_similar(r.get("content") or r.get("subject") or "")
    update_req(rid, {"similar": sim})
    return get_req(rid)


@app.post("/api/requests/{rid}/departments")
def refresh_departments(rid: int):
    r = get_req(rid)
    if not ai.enabled():
        raise HTTPException(400, "AI wyłączone – brak klucza API")
    dep = ai.suggest_departments(r.get("content") or "", r.get("summary") or "", depts())
    update_req(rid, {"departments": dep})
    return get_req(rid)


@app.post("/api/requests/{rid}/draft")
def make_draft(rid: int):
    r = get_req(rid)
    if not ai.enabled():
        raise HTTPException(400, "AI wyłączone – brak klucza API")
    if not r["responses"]:
        raise HTTPException(400, "Najpierw wgraj odpowiedź z wydziału")
    sim = r.get("similar") or []
    draft = ai.draft_letter(r, r["responses"], sim if isinstance(sim, list) else [], date.today().strftime("%d.%m.%Y"), knowledge_context())
    spans = anonymize.detect(draft)
    update_req(rid, {"draft": draft, "anon_spans": spans, "final_text": None}, "wygenerowano projekt odpowiedzi")
    return get_req(rid)


@app.post("/api/requests/{rid}/verify")
def verify_endpoint(rid: int, body: dict = Body(default={})):
    r = get_req(rid)
    return verify_draft(body.get("text") or r.get("draft") or "", r["responses"], r.get("content") or "")


@app.post("/api/requests/{rid}/anonymize/detect")
def detect_pii(rid: int, body: dict = Body(default={})):
    r = get_req(rid)
    text = body.get("text") or r.get("draft") or ""
    spans = anonymize.detect(text)
    update_req(rid, {"draft": text, "anon_spans": spans})
    return get_req(rid)


@app.post("/api/requests/{rid}/anonymize/apply")
def apply_anon(rid: int, body: dict = Body(...)):
    r = get_req(rid)
    text = body.get("text") or r.get("draft") or ""
    final = anonymize.apply(text, body.get("spans", []))
    fields = {"final_text": final}
    if r["status"] in ("nowy", "w_wydziale", "kwerenda"):
        fields["status"] = "przygotowana"
    update_req(rid, fields, f"zaakceptowano anonimizację ({len(body.get('spans', []))} fragm.)")
    return get_req(rid)


@app.get("/api/requests/{rid}/docx")
def export_docx(rid: int, variant: str = "final"):
    r = get_req(rid)
    text = (r.get("final_text") if variant == "final" else None) or r.get("draft") or ""
    if not text:
        raise HTTPException(400, "Brak treści odpowiedzi")
    name = re.sub(r"[^\w.-]+", "_", f"odpowiedz_{r.get('number') or rid}") + ".docx"
    return Response(build_docx(text), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/requests/{rid}/txt")
def export_txt(rid: int):
    r = get_req(rid)
    text = r.get("final_text") or r.get("draft") or ""
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="odpowiedz_{rid}.txt"'})


# --------- anonimizacja niezależna (test jury: plik z danymi osobowymi) ---------

@app.post("/api/anonymize")
async def anon_any(file: UploadFile = File(None), text: str = Form("")):
    if file is not None and file.filename:
        text = extract_text(file.filename, await read_upload(file))
    return {"text": text, "spans": anonymize.detect(text)}


@app.post("/api/anonymize/apply")
def anon_any_apply(body: dict = Body(...)):
    return {"text": anonymize.apply(body.get("text", ""), body.get("spans", []))}


@app.post("/api/anonymize/docx")
def anon_any_docx(body: dict = Body(...)):
    return Response(build_docx(body.get("text", ""), header="DOKUMENT ZANONIMIZOWANY"),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="zanonimizowany.docx"'})


# ---------------- baza wiedzy / archiwum ----------------

@app.get("/api/archive")
def archive_list(q: str = ""):
    with db.conn() as c:
        if q.strip():
            fq = fts_query(q)
            rows = c.execute("""SELECT a.* FROM archive_fts JOIN archive a ON a.id=archive_fts.rowid WHERE archive_fts MATCH ?
                                ORDER BY bm25(archive_fts) LIMIT 50""", (fq,)).fetchall() if fq else []
        else:
            rows = c.execute("SELECT * FROM archive ORDER BY received_date DESC LIMIT 200").fetchall()
        kn = [dict(r) for r in c.execute("SELECT id,title,kind,length(text) AS size,created_at FROM knowledge ORDER BY id DESC").fetchall()]
    return {"items": [dict(r) for r in rows], "knowledge": kn}


@app.post("/api/archive")
async def archive_add(subject: str = Form(""), received_date: str = Form(""), department: str = Form(""),
                      request_text: str = Form(""), answer_text: str = Form(""),
                      request_file: UploadFile = File(None), answer_file: UploadFile = File(None)):
    if request_file is not None and request_file.filename:
        request_text = extract_text(request_file.filename, await read_upload(request_file))
    if answer_file is not None and answer_file.filename:
        answer_text = extract_text(answer_file.filename, await read_upload(answer_file))
    if not (request_text or answer_text):
        raise HTTPException(400, "Podaj treść wniosku lub odpowiedzi")
    if not subject and ai.enabled():
        try:
            subject = ai.extract_request_fields(request_text or answer_text).get("subject") or ""
        except Exception:
            pass
    with db.conn() as c:
        c.execute("INSERT INTO archive(received_date,subject,department,request_text,answer_text) VALUES(?,?,?,?,?)",
                  (received_date or date.today().isoformat(), subject or (request_text[:60] + "…"), department, request_text, answer_text))
    return {"ok": True}


@app.delete("/api/archive/{aid}")
def archive_del(aid: int):
    with db.conn() as c:
        c.execute("DELETE FROM archive WHERE id=?", (aid,))
    return {"ok": True}


@app.post("/api/knowledge")
async def knowledge_add(file: UploadFile = File(...), kind: str = Form("ustawa")):
    text = extract_text(file.filename, await read_upload(file))
    with db.conn() as c:
        c.execute("INSERT INTO knowledge(title,kind,text) VALUES(?,?,?)", (file.filename, kind, text))
    return {"ok": True, "chars": len(text)}


@app.get("/api/knowledge/{kid}")
def knowledge_get(kid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone()
    if not r:
        raise HTTPException(404)
    return dict(r)


@app.delete("/api/knowledge/{kid}")
def knowledge_del(kid: int):
    with db.conn() as c:
        c.execute("DELETE FROM knowledge WHERE id=?", (kid,))
    return {"ok": True}


@app.get("/api/anonymization-categories")
def anon_categories():
    return [{"code": c, "label": l} for c, l in ai.PII_CATEGORIES]


@app.post("/api/similar")
def similar_free(body: dict = Body(...)):
    return find_similar(body.get("text", ""))


# ---------------- pisma procesowe (kwerenda, doprecyzowanie, odmowa) ----------------

@app.post("/api/requests/{rid}/letters")
def letter_create(rid: int, body: dict = Body(...)):
    r = get_req(rid)
    kind = body.get("kind")
    nd = body.get("new_date") or None
    if nd:
        try:
            ndd = date.fromisoformat(nd)
        except ValueError:
            raise HTTPException(400, "Nieprawidłowa data")
        if r.get("max_deadline") and ndd > date.fromisoformat(r["max_deadline"]):
            raise HTTPException(400, f"Nowy termin nie może przekroczyć 2 miesięcy od wpływu wniosku (najpóźniej {r['max_deadline']}, art. 13 ust. 2 UDIP)")
    dept = body.get("dept") or ""
    dname = next((d["name"] for d in depts() if d["code"] == dept), dept)
    try:
        L = letters.build(r, kind, notes=body.get("notes", ""), new_date=nd, ground=body.get("ground"), dept=dname or None, days=int(body.get("days") or 7))
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db.conn() as c:
        c.execute("INSERT INTO letters(request_id,kind,title,text,new_date) VALUES(?,?,?,?,?)", (rid, kind, L["title"], L["text"], nd))
    update_req(rid, {}, f"przygotowano pismo: {L['title']}")
    return get_req(rid)


@app.patch("/api/letters/{lid}")
def letter_update(lid: int, body: dict = Body(...)):
    with db.conn() as c:
        c.execute("UPDATE letters SET text=? WHERE id=?", (body.get("text", ""), lid))
    return {"ok": True}


@app.delete("/api/letters/{lid}")
def letter_delete(lid: int):
    with db.conn() as c:
        c.execute("DELETE FROM letters WHERE id=?", (lid,))
    return {"ok": True}


def _letter(lid: int) -> dict:
    with db.conn() as c:
        L = c.execute("SELECT * FROM letters WHERE id=?", (lid,)).fetchone()
    if not L:
        raise HTTPException(404, "Brak pisma")
    return dict(L)


@app.get("/api/letters/{lid}/docx")
def letter_docx(lid: int):
    L = _letter(lid)
    r = get_req(L["request_id"])
    name = re.sub(r"[^\w.-]+", "_", f"{L['kind']}_{r.get('number') or lid}") + ".docx"
    return Response(build_docx(L["text"]), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/letters/{lid}/apply")
def letter_apply(lid: int):
    """Przenosi nowy termin z pisma do kalendarza (przedłużenie art. 13 ust. 2) i ustawia status."""
    L = _letter(lid)
    r = get_req(L["request_id"])
    if not L.get("new_date"):
        raise HTTPException(400, "Pismo nie wskazuje nowego terminu")
    rd = date.fromisoformat(r["received_date"])
    ext = (date.fromisoformat(L["new_date"]) - (rd + timedelta(days=14))).days
    if ext < 0:
        raise HTTPException(400, "Nowy termin jest wcześniejszy niż termin ustawowy")
    fields = {"extended_days": ext, "deadline": compute_deadline(rd, ext).isoformat()}
    if r["status"] in ("nowy", "w_wydziale"):
        fields["status"] = "kwerenda"
    update_req(r["id"], fields, f"zastosowano nowy termin z pisma ({L['title']}): {L['new_date']}")
    with db.conn() as c:
        c.execute("UPDATE letters SET applied=1 WHERE id=?", (lid,))
    return get_req(r["id"])


# ---------------- rozpoznawanie dokumentów ----------------


def _doc(did: int) -> dict:
    with db.conn() as c:
        d = db.row(c.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone())
    if not d:
        raise HTTPException(404, "Brak dokumentu")
    d["doc_type_label"] = rec.TYPES.get(d["doc_type"], d["doc_type"])
    with db.conn() as c:
        if d.get("signature"):
            m = c.execute("SELECT id, number, subject, status FROM requests WHERE number=?", (d["signature"],)).fetchone()
            d["matched_request"] = dict(m) if m else None
    return d


@app.post("/api/recognize")
async def recognize_docs(files: list[UploadFile] = File(...), force_vision: bool = Form(False)):
    out = []
    os.makedirs(PREV_DIR, exist_ok=True)
    for f in files:
        data = await read_upload(f)
        try:
            r = rec.recognize(f.filename, data, force_vision=force_vision)
        except Exception as e:
            out.append({"filename": f.filename, "error": str(e)})
            continue
        imgs = r.pop("_images", [])
        with db.conn() as c:
            cur = c.execute("""INSERT INTO documents(filename,doc_type,signature,received_date,entry_number,handwritten,conf,methods,pages,text,n_pages)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (f.filename, r["doc_type"], r["signature"], r["received_date"], r["entry_number"], int(r["handwritten"]),
                             r["conf"], db.dumps(r["methods"]), db.dumps(r["pages"]), r["text"], len(r["pages"])))
            did = cur.lastrowid
        for i, im in enumerate(imgs[:10]):
            try:
                im2 = im.convert("RGB")
                im2.thumbnail((1100, 1500))
                im2.save(os.path.join(PREV_DIR, f"{did}-{i+1}.jpg"), quality=80)
            except Exception:
                pass
        out.append(_doc(did))
    return out


@app.get("/api/documents")
def documents_list():
    with db.conn() as c:
        rows = c.execute("SELECT id FROM documents ORDER BY id DESC LIMIT 300").fetchall()
    items = [_doc(r["id"]) for r in rows]
    for it in items:
        it["text_preview"] = (it.get("text") or "")[:200]
        it.pop("text", None)
        for p in it.get("pages") or []:
            p.pop("text", None)
    return items


@app.get("/api/documents/{did}")
def document_get(did: int):
    d = _doc(did)
    d["previews"] = [n for n in range(1, (d.get("n_pages") or 1) + 1) if os.path.exists(os.path.join(PREV_DIR, f"{did}-{n}.jpg"))]
    return d


@app.get("/api/documents/{did}/page/{n}")
def document_page(did: int, n: int):
    u = auth.current_user.get()
    if u and u["role"] == "wydzial":  # komórka widzi tylko pisma ze swoich spraw
        with db.conn() as c:
            rows = c.execute("SELECT id FROM requests WHERE doc_id=? UNION SELECT request_id FROM documents WHERE id=? AND request_id IS NOT NULL", (did, did)).fetchall()
        if not any(auth.visible_to_dept(get_req(r[0]), u["dept_code"]) for r in rows):
            raise HTTPException(403, "To pismo nie należy do sprawy Twojej komórki")
    fp = os.path.join(PREV_DIR, f"{did}-{n}.jpg")
    if not os.path.exists(fp):
        raise HTTPException(404)
    return FileResponse(fp, media_type="image/jpeg")


@app.patch("/api/documents/{did}")
def document_patch(did: int, body: dict = Body(...)):
    fields = {k: v for k, v in body.items() if k in ("doc_type", "signature", "received_date", "text", "entry_number")}
    if fields:
        with db.conn() as c:
            c.execute(f"UPDATE documents SET {', '.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), did))
    return document_get(did)


@app.delete("/api/documents/{did}")
def document_del(did: int):
    with db.conn() as c:
        c.execute("DELETE FROM documents WHERE id=?", (did,))
    return {"ok": True}


def _set_doc(did, **kw):
    with db.conn() as c:
        c.execute(f"UPDATE documents SET {', '.join(k+'=?' for k in kw)} WHERE id=?", (*kw.values(), did))


def _create_request_from_text(text: str, filename: str, number: str = None, received: str = None) -> int:
    fields = {}
    if ai.enabled():
        try:
            fields = ai.extract_request_fields(text)
        except Exception:
            fields = {}
    rd = received or fields.get("received_date") or date.today().isoformat()
    try:
        rdd = date.fromisoformat(rd)
    except Exception:
        rdd = date.today()
    if rdd > date.today():
        rdd = date.today()
    dep = {}
    if ai.enabled():
        try:
            dep = ai.suggest_departments(text, fields.get("summary", ""), depts())
        except Exception as e:
            dep = {"departments": [], "error": str(e)}
    similar = find_similar(text)
    with db.conn() as c:
        lp = (c.execute("SELECT COALESCE(MAX(lp),0) FROM requests WHERE year=?", (rdd.year,)).fetchone()[0] or 0) + 1
        cur = c.execute("""INSERT INTO requests(year,lp,number,received_date,deadline,status,requester,requester_type,delivery,subject,summary,
                           questions,content,filename,departments,similar,history) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rdd.year, lp, number or fields.get("number") or f"{settings.get('case_prefix')}{lp}.{rdd.year}", rdd.isoformat(),
                         compute_deadline(rdd).isoformat(), "nowy", fields.get("requester"), fields.get("requester_type"), fields.get("delivery"),
                         fields.get("subject") or filename, fields.get("summary"), db.dumps(fields.get("questions") or []), text, filename,
                         db.dumps(dep), db.dumps(similar),
                         db.dumps([{"at": datetime.now().isoformat(timespec="minutes"), "status": "nowy", "note": f"utworzono z rozpoznanego dokumentu {filename}"}])))
        return cur.lastrowid


@app.post("/api/documents/{did}/action")
def document_action(did: int, body: dict = Body(...)):
    d = _doc(did)
    act = body.get("action")
    rid = body.get("request_id") or (d.get("matched_request") or {}).get("id")
    if act == "create_request":
        rid = _create_request_from_text(d["text"], d["filename"], d.get("signature"), d.get("received_date"))
        _set_doc(did, request_id=rid, status="wniosek utworzony")
        return {"ok": True, "request_id": rid, "message": "Utworzono wniosek w kalendarzu"}
    if not rid:
        raise HTTPException(400, "Wskaż sprawę (brak wniosku o tej sygnaturze)")
    if act == "add_response":
        with db.conn() as c:
            c.execute("INSERT INTO responses(request_id,department,filename,text) VALUES(?,?,?,?)",
                      (rid, body.get("department") or "pismo", d["filename"], d["text"]))
        update_req(rid, {}, f"dołączono pismo: {d['filename']}")
        _set_doc(did, request_id=rid, status="dołączono do sprawy")
        return {"ok": True, "request_id": rid, "message": "Dołączono jako odpowiedź / materiał do sprawy"}
    if act == "apply_kwerenda":
        days = int(body.get("days") or 30)
        patch_request(rid, {"extended_days": days, "status": "kwerenda"})
        _set_doc(did, request_id=rid, status="kwerenda zastosowana")
        return {"ok": True, "request_id": rid, "message": f"Przedłużono termin (+{days} dni), status: kwerenda"}
    if act == "mark_final":
        update_req(rid, {"final_text": d["text"], "status": "wyslana", "answered_date": d.get("received_date") or date.today().isoformat()},
                   f"odpowiedź udzielona – pismo {d['filename']}")
        _set_doc(did, request_id=rid, status="odpowiedź zarejestrowana")
        return {"ok": True, "request_id": rid, "message": "Zarejestrowano odpowiedź, status: wysłana"}
    if act == "doprecyzowanie":
        update_req(rid, {"notes": "Wezwano do doprecyzowania wniosku – bieg terminu do czasu doprecyzowania"}, f"wezwanie do doprecyzowania: {d['filename']}")
        _set_doc(did, request_id=rid, status="dołączono do sprawy")
        return {"ok": True, "request_id": rid, "message": "Odnotowano wezwanie do doprecyzowania w historii sprawy"}
    raise HTTPException(400, "Nieznana akcja")


_MONTHS = {"stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
           "września": 9, "października": 10, "listopada": 11, "grudnia": 12}


def _doc_date(text: str):
    m = re.search(r"Bydgoszcz,?\s*(?:dnia\s+)?(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", text or "", re.I)
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"Bydgoszcz,?\s*(?:dnia\s*)?(\d{1,2})[.](\d{1,2})[.](\d{4})", text or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None


@app.post("/api/documents/import-archive")
def documents_import_archive():
    """Paruje wnioski z odpowiedziami po sygnaturze i zapisuje do archiwum (wniosek po anonimizacji regułami)."""
    with db.conn() as c:
        docs = [db.row(r) for r in c.execute("SELECT * FROM documents WHERE signature IS NOT NULL").fetchall()]
        existing = {r[0] for r in c.execute("SELECT number FROM archive WHERE number IS NOT NULL").fetchall()}
    by = {}
    for d in docs:
        by.setdefault(d["signature"], {}).setdefault(d["doc_type"], []).append(d)
    added, skipped = [], []
    for sig, g in by.items():
        if sig in existing:
            skipped.append(sig); continue
        if "wniosek" not in g or "odpowiedz" not in g:
            skipped.append(sig); continue
        wn, od = g["wniosek"][0], g["odpowiedz"][0]
        wn_text = anonymize.apply(wn["text"], anonymize.detect(wn["text"], use_llm=False))
        extra = "".join(f"\n\n[{rec.TYPES[t]}]\n{x[0]['text']}" for t, x in g.items() if t in ("kwerenda", "doprecyzowanie"))
        subject = ""
        if ai.enabled():
            try:
                subject = ai.extract_request_fields(wn_text).get("subject") or ""
            except Exception:
                pass
        rd = wn.get("received_date") or _doc_date(od["text"]) or _doc_date(wn["text"]) or f"{sig.split('.')[-1]}-01-01"
        with db.conn() as c:
            c.execute("INSERT INTO archive(received_date,number,subject,department,request_text,answer_text) VALUES(?,?,?,?,?,?)",
                      (rd, sig, subject or wn_text[:70], "BKS", wn_text, od["text"] + extra))
        added.append(sig)
    return {"added": added, "skipped": skipped}


# ---------------- frontend ----------------
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))
