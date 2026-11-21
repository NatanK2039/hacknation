"""Konta użytkowników, role i sesje.
Role: admin (wszystko + panel administracyjny), aip (praca na wnioskach), wydzial (tylko sprawy swojej komórki, dodawanie odpowiedzi),
podglad (tylko odczyt). Hasła: PBKDF2-SHA256 (240 000 iteracji, sól). Sesja: losowy token w ciasteczku HttpOnly, w bazie tylko skrót."""
import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db

ROLES = {"admin": "Administrator", "aip": "Urzędnik AIP", "wydzial": "Wydział", "podglad": "Podgląd"}
COOKIE = "udip_session"
SESSION_H = int(os.getenv("SESSION_HOURS", "12"))
current_user = contextvars.ContextVar("current_user", default=None)
router = APIRouter(prefix="/api/auth")
_fail = {}  # (ip, username) -> [timestamps]
SESSION_MAX_H = 24  # twardy limit życia sesji, niezależnie od aktywności


# ---------------- hasła i sesje ----------------

def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 240_000).hex()
    return f"pbkdf2_sha256$240000${salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it)).hex(), h)
    except Exception:
        return False


def validate_pw(pw: str):
    if len(pw or "") < 10:
        raise HTTPException(400, "Hasło musi mieć co najmniej 10 znaków")
    if not (re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", pw) and re.search(r"\d", pw)):
        raise HTTPException(400, "Hasło musi zawierać litery i cyfry")


def _tok_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def create_session(user_id: int, ip: str = None, ua: str = None) -> str:
    t = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(hours=SESSION_H)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.execute("INSERT INTO sessions(token_hash,user_id,expires,ip,ua) VALUES(?,?,?,?,?)", (_tok_hash(t), user_id, exp, ip, (ua or "")[:200]))
        c.execute("DELETE FROM sessions WHERE expires < ?", (datetime.now().isoformat(timespec="seconds"),))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), user_id))
    return t


def user_from_token(t: str):
    if not t:
        return None
    with db.conn() as c:
        r = c.execute("""SELECT u.*, s.expires, s.token_hash FROM sessions s JOIN users u ON u.id=s.user_id
                         WHERE s.token_hash=?""", (_tok_hash(t),)).fetchone()
        if not r or not r["active"] or r["expires"] < datetime.now().isoformat(timespec="seconds"):
            return None
        born = c.execute("SELECT created_at FROM sessions WHERE token_hash=?", (r["token_hash"],)).fetchone()[0]
        if born and born < (datetime.now() - timedelta(hours=SESSION_MAX_H)).strftime("%Y-%m-%d %H:%M:%S"):
            c.execute("DELETE FROM sessions WHERE token_hash=?", (r["token_hash"],))
            return None
        # przesuwane wygaśnięcie sesji
        c.execute("UPDATE sessions SET expires=? WHERE token_hash=?",
                  ((datetime.now() + timedelta(hours=SESSION_H)).isoformat(timespec="seconds"), r["token_hash"]))
    return public(dict(r))


def public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "username", "full_name", "initials", "role", "dept_code", "active", "must_change", "last_login", "created_at")} | \
           {"role_label": ROLES.get(u.get("role"), u.get("role"))}


def actor_label(u) -> str:
    if not u:
        return "—"
    return u.get("username") + (f" ({u['initials']})" if u.get("initials") else "")


# ---------------- konta startowe ----------------

def bootstrap():
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        seeds = []
        if os.getenv("ADMIN_PASSWORD"):
            seeds.append(("admin", "Administrator systemu", "ADM", "admin", None, os.getenv("ADMIN_PASSWORD"), 0))
        if os.getenv("JURY_PASSWORD"):
            seeds.append(("jury", "Jury HackNation", "JRY", "aip", None, os.getenv("JURY_PASSWORD"), 0))
        if os.getenv("DEMO_WYDZIAL_PASSWORD"):
            seeds.append(("wydzial.we", "Wydział Edukacji i Sportu (demo)", "WE", "wydzial", "WE", os.getenv("DEMO_WYDZIAL_PASSWORD"), 0))
        for u, fn, ini, role, dept, pw, mc in seeds:
            c.execute("INSERT INTO users(username,full_name,initials,role,dept_code,password_hash,must_change) VALUES(?,?,?,?,?,?,?)",
                      (u, fn, ini, role, dept, hash_pw(pw), mc))


# ---------------- uprawnienia ----------------

PUBLIC = [("POST", r"^/api/auth/login$"), ("GET", r"^/api/config$")]
WYDZIAL_OK = [
    ("GET", r"^/api/(departments|anonymization-categories|requests)$"),
    ("GET", r"^/api/requests/\d+$"),
    ("POST", r"^/api/requests/\d+/responses$"),
    ("DELETE", r"^/api/responses/\d+$"),
    ("GET", r"^/api/documents/\d+/page/\d+$"),
]


def allowed(user: dict, method: str, path: str) -> bool:
    role = user["role"]
    if path.startswith("/api/auth/"):
        return True
    if path.startswith("/api/admin/"):
        return role == "admin"
    if role in ("admin", "aip"):
        return True
    if role == "podglad":
        return method in ("GET", "HEAD")
    if role == "wydzial":
        return any(m == method and re.match(rx, path) for m, rx in WYDZIAL_OK)
    return False


async def auth_middleware(request: Request, call_next):
    path, method = request.url.path, request.method
    if not path.startswith("/api/") or any(m == method and re.match(rx, path) for m, rx in PUBLIC):
        token = current_user.set(None)
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)
    user = user_from_token(request.cookies.get(COOKIE))
    if not user:
        return JSONResponse({"detail": "Wymagane zalogowanie"}, status_code=401)
    if user.get("must_change") and not path.startswith("/api/auth/"):
        return JSONResponse({"detail": "Najpierw zmień hasło tymczasowe"}, status_code=403)
    if not allowed(user, method, path):
        from .admin import log
        log("odmowa dostępu (brak uprawnień)", "system", None, f"{method} {path}", actor=actor_label(user), method=method, path=path, status=403)
        return JSONResponse({"detail": f"Brak uprawnień dla roli „{user['role_label']}”"}, status_code=403)
    token = current_user.set(user)
    try:
        return await call_next(request)
    finally:
        current_user.reset(token)


def visible_to_dept(req: dict, dept: str) -> bool:
    """Sprawa należy do komórki, gdy komórka jest wybrana albo zaproponowana przez AI, albo już wgrała odpowiedź."""
    if not dept:
        return False
    chosen = req.get("chosen_departments") or []
    if isinstance(chosen, list) and dept in chosen:
        return True
    d = req.get("departments")
    if isinstance(d, dict) and any((x or {}).get("code") == dept for x in d.get("departments") or []):
        return True
    return any((r.get("department") or "") == dept for r in req.get("responses") or [])


# ---------------- endpointy logowania ----------------

def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()


_DUMMY_HASH = hash_pw(secrets.token_urlsafe(12))


@router.post("/login")
def login(request: Request, body: dict = Body(...)):
    from .admin import log
    username = str(body.get("username", "")).strip().lower()
    pw = str(body.get("password", ""))
    key = (_ip(request), username)
    now = time.time()
    _fail[key] = [t for t in _fail.get(key, []) if now - t < 300]
    if len(_fail[key]) >= 5:
        log("logowanie zablokowane (5 nieudanych prób)", "konto", None, username, actor=username, status=429, ip=key[0])
        raise HTTPException(429, "Zbyt wiele nieudanych prób. Spróbuj ponownie za 5 minut.")
    with db.conn() as c:
        u = c.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()
    time.sleep(0.3)
    ok = check_pw(pw, u["password_hash"] if u else _DUMMY_HASH)  # zawsze liczymy skrót – stały czas odpowiedzi
    if not u or not u["active"] or not ok:
        _fail[key].append(now)
        log("logowanie – nieudane", "konto", None, username, actor=username, status=401, ip=key[0])
        raise HTTPException(401, "Nieprawidłowy login lub hasło")
    _fail.pop(key, None)
    t = create_session(u["id"], key[0], request.headers.get("user-agent"))
    log("logowanie", "konto", u["id"], None, actor=actor_label(dict(u)), status=200, ip=key[0])
    resp = JSONResponse({"user": public(dict(u))})
    resp.set_cookie(COOKIE, t, max_age=SESSION_H * 3600, httponly=True, samesite="lax",
                    secure=os.getenv("COOKIE_SECURE", "1") == "1", path="/")
    return resp


@router.post("/logout")
def logout(request: Request):
    from .admin import log
    t = request.cookies.get(COOKIE)
    u = current_user.get()
    if t:
        with db.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_tok_hash(t),))
    log("wylogowanie", "konto", u and u["id"], None, actor=actor_label(u))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/me")
def me():
    return {"user": current_user.get(), "roles": ROLES}


@router.post("/password")
def change_password(request: Request, body: dict = Body(...)):
    from .admin import log
    u = current_user.get()
    with db.conn() as c:
        row = c.execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if not check_pw(str(body.get("old", "")), row["password_hash"]):
        raise HTTPException(400, "Obecne hasło jest nieprawidłowe")
    new = str(body.get("new", ""))
    validate_pw(new)
    if new == body.get("old"):
        raise HTTPException(400, "Nowe hasło musi różnić się od obecnego")
    with db.conn() as c:
        c.execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?", (hash_pw(new), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], _tok_hash(request.cookies.get(COOKIE) or "")))
    log("zmiana własnego hasła", "konto", u["id"], None, actor=actor_label(u))
    return {"ok": True}


# ---------------- zarządzanie użytkownikami (administrator) ----------------

def _require_admin_user():
    u = current_user.get()
    if not u or u["role"] != "admin":
        raise HTTPException(403, "Tylko administrator")
    return u


admin_router = APIRouter(prefix="/api/admin/users", dependencies=[Depends(_require_admin_user)])


def _clean(body: dict, creating: bool) -> dict:
    out = {}
    if creating or "username" in body:
        un = str(body.get("username", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{3,40}", un):
            raise HTTPException(400, "Login: 3–40 znaków, małe litery, cyfry, kropka, myślnik, podkreślnik")
        out["username"] = un
    for k in ("full_name", "initials"):
        if k in body:
            out[k] = str(body.get(k) or "").strip()[:80 if k == "full_name" else 6]
    if creating or "role" in body:
        if body.get("role") not in ROLES:
            raise HTTPException(400, "Nieprawidłowa rola")
        out["role"] = body["role"]
    if "dept_code" in body or creating:
        out["dept_code"] = (str(body.get("dept_code") or "").strip() or None)
    if out.get("role") == "wydzial" and not out.get("dept_code"):
        raise HTTPException(400, "Konto wydziału musi mieć przypisaną komórkę")
    if "active" in body:
        out["active"] = 1 if body["active"] else 0
    if "must_change" in body:
        out["must_change"] = 1 if body["must_change"] else 0
    return out


@admin_router.get("")
def users_list():
    with db.conn() as c:
        rows = [public(dict(r)) for r in c.execute("SELECT * FROM users ORDER BY role, username").fetchall()]
        sess = {r[0]: r[1] for r in c.execute("SELECT user_id, COUNT(*) FROM sessions WHERE expires > ? GROUP BY user_id",
                                                (datetime.now().isoformat(timespec="seconds"),)).fetchall()}
    for r in rows:
        r["sessions"] = sess.get(r["id"], 0)
    return {"items": rows, "roles": ROLES}


@admin_router.post("")
def users_create(body: dict = Body(...)):
    from .admin import add_detail
    d = _clean(body, True)
    pw = str(body.get("password", "")) or secrets.token_urlsafe(9)
    validate_pw(pw) if body.get("password") else None
    with db.conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (d["username"],)).fetchone():
            raise HTTPException(400, "Taki login już istnieje")
        c.execute("INSERT INTO users(username,full_name,initials,role,dept_code,password_hash,must_change) VALUES(?,?,?,?,?,?,?)",
                  (d["username"], d.get("full_name"), d.get("initials"), d["role"], d.get("dept_code"), hash_pw(pw),
                   0 if body.get("must_change") is False else 1))
    add_detail(f"{d['username']} · {ROLES[d['role']]}" + (f" · {d['dept_code']}" if d.get("dept_code") else ""))
    return {"ok": True, "temp_password": None if body.get("password") else pw, **users_list()}


@admin_router.patch("/{uid}")
def users_update(uid: int, body: dict = Body(...)):
    from .admin import add_detail
    me_ = current_user.get()
    d = _clean(body, False)
    if uid == me_["id"] and (d.get("role", "admin") != "admin" or d.get("active") == 0):
        raise HTTPException(400, "Nie możesz odebrać sobie roli administratora ani zablokować własnego konta")
    with db.conn() as c:
        old = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not old:
            raise HTTPException(404, "Brak użytkownika")
        if d.get("role") and d["role"] != "admin" and old["role"] == "admin" and \
                c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
            raise HTTPException(400, "Musi pozostać co najmniej jeden aktywny administrator")
        if d:
            c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in d)} WHERE id=?", (*d.values(), uid))
        if d.get("active") == 0:
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    changes = {k: v for k, v in d.items() if old[k] != v}
    add_detail(f"{old['username']}: " + json.dumps(changes, ensure_ascii=False))
    return users_list()


@admin_router.post("/{uid}/reset-password")
def users_reset_pw(uid: int, body: dict = Body(default={})):
    from .admin import add_detail
    pw = str(body.get("password") or "") or secrets.token_urlsafe(9)
    if body.get("password"):
        validate_pw(pw)
    with db.conn() as c:
        u = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "Brak użytkownika")
        c.execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (hash_pw(pw), uid))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    add_detail(f"{u['username']} – hasło tymczasowe, wymagana zmiana przy logowaniu")
    return {"ok": True, "temp_password": pw}


@admin_router.delete("/{uid}")
def users_delete(uid: int):
    from .admin import add_detail
    if uid == current_user.get()["id"]:
        raise HTTPException(400, "Nie możesz usunąć własnego konta")
    with db.conn() as c:
        u = c.execute("SELECT username, role FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "Brak użytkownika")
        if u["role"] == "admin" and c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
            raise HTTPException(400, "Nie można usunąć ostatniego administratora")
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    add_detail(u["username"])
    return users_list()
