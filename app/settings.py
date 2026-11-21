"""Ustawienia edytowalne w panelu administracyjnym (nadpisują zmienne środowiskowe). Klucz API zostaje wyłącznie w .env."""
import os
import time

from . import db

_cache = {"t": 0, "v": {}}


def defaults() -> dict:
    return {
        "provider": "local" if os.getenv("OPENAI_BASE_URL") else "openai",
        "local_base_url": os.getenv("OPENAI_BASE_URL", "http://ollama:11434/v1"),
        "llm_model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "vision_model": os.getenv("VISION_MODEL", "gpt-4.1"),
        "llm_timeout": os.getenv("LLM_TIMEOUT", "45"),
        "ocr_timeout": os.getenv("OCR_TIMEOUT", "25"),
        "vision_for_scans": os.getenv("VISION_FOR_SCANS", "1"),
        "dept_lead_days": os.getenv("DEPT_LEAD_DAYS", "3"),
        "case_prefix": os.getenv("CASE_PREFIX", "BKS.IV.1431."),
    }


SCHEMA = {
    "provider": {"label": "Dostawca modeli AI", "type": "select", "options": ["openai", "local"],
                 "help": "openai = API OpenAI (wymaga klucza w .env); local = serwer zgodny z OpenAI w Urzędzie (Ollama/vLLM), bez Internetu"},
    "local_base_url": {"label": "Adres lokalnego serwera AI", "type": "text", "help": "np. http://ollama:11434/v1"},
    "llm_model": {"label": "Model tekstowy", "type": "text", "help": "ekstrakcja danych, wydziały, projekty pism; np. gpt-4o-mini, gpt-4.1, bielik-11b"},
    "vision_model": {"label": "Model wizyjny (skany, pismo odręczne)", "type": "text", "help": "np. gpt-4.1, qwen2.5vl"},
    "llm_timeout": {"label": "Limit czasu zapytania AI (s)", "type": "number", "min": 10, "max": 300},
    "ocr_timeout": {"label": "Limit czasu OCR na stronę (s)", "type": "number", "min": 5, "max": 120},
    "vision_for_scans": {"label": "Każdy skan przez AI-vision", "type": "select", "options": ["1", "0"],
                         "help": "1 = lepsza treść i odczyt pieczątek; 0 = AI-vision tylko przy niskiej pewności OCR"},
    "dept_lead_days": {"label": "Termin dla wydziału (dni robocze przed terminem ustawowym)", "type": "number", "min": 0, "max": 10},
    "case_prefix": {"label": "Prefiks sygnatury nowych spraw", "type": "text", "help": "np. BKS.IV.1431."},
}


def all_settings() -> dict:
    if time.time() - _cache["t"] < 3:
        return _cache["v"]
    v = defaults()
    try:
        with db.conn() as c:
            for r in c.execute("SELECT key, value FROM settings").fetchall():
                if r["key"] in v:
                    v[r["key"]] = r["value"]
    except Exception:
        pass
    _cache.update(t=time.time(), v=v)
    return v


def get(key: str, default=None):
    return all_settings().get(key, default)


def get_int(key: str, default: int) -> int:
    try:
        return int(float(get(key, default)))
    except Exception:
        return default


def set_many(values: dict) -> dict:
    changed = {}
    cur = all_settings()
    for k, v in values.items():
        if k not in SCHEMA:
            continue
        v = str(v).strip()
        meta = SCHEMA[k]
        if meta["type"] == "number":
            n = float(v)
            if n < meta["min"] or n > meta["max"]:
                raise ValueError(f"{meta['label']}: wartość spoza zakresu {meta['min']}–{meta['max']}")
            v = str(int(n))
        if meta["type"] == "select" and v not in meta["options"]:
            raise ValueError(f"{meta['label']}: niedozwolona wartość")
        if k == "local_base_url" and v and not v.startswith(("http://", "https://")):
            raise ValueError("Adres serwera AI musi zaczynać się od http:// lub https://")
        if not v and k not in ("local_base_url",):
            raise ValueError(f"{meta['label']}: pole nie może być puste")
        if cur.get(k) != v:
            changed[k] = {"from": cur.get(k), "to": v}
    with db.conn() as c:
        for k, ch in changed.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, ch["to"]))
    _cache["t"] = 0
    return changed
