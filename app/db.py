import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "udip.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER, lp INTEGER, number TEXT,
  received_date TEXT, deadline TEXT, extended_days INTEGER DEFAULT 0,
  status TEXT DEFAULT 'nowy', initials TEXT, requester TEXT, requester_type TEXT, delivery TEXT,
  subject TEXT, summary TEXT, questions TEXT, content TEXT, filename TEXT,
  departments TEXT, chosen_departments TEXT, similar TEXT,
  draft TEXT, anon_spans TEXT, final_text TEXT,
  answered_date TEXT, notes TEXT, history TEXT DEFAULT '[]',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, department TEXT, filename TEXT, text TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT, received_date TEXT, number TEXT, subject TEXT, department TEXT,
  request_text TEXT, answer_text TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(subject, request_text, answer_text, content='archive', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON archive BEGIN
  INSERT INTO archive_fts(rowid, subject, request_text, answer_text) VALUES (new.id, new.subject, new.request_text, new.answer_text);
END;
CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON archive BEGIN
  INSERT INTO archive_fts(archive_fts, rowid, subject, request_text, answer_text) VALUES('delete', old.id, old.subject, old.request_text, old.answer_text);
END;
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, doc_type TEXT, signature TEXT, received_date TEXT, entry_number TEXT,
  handwritten INTEGER DEFAULT 0, conf REAL, methods TEXT, pages TEXT, text TEXT, n_pages INTEGER, request_id INTEGER, status TEXT DEFAULT 'nowy',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, kind TEXT, text TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

JSON_COLS = {"recognition", "methods", "pages", "questions", "departments", "chosen_departments", "similar", "anon_spans", "history"}


@contextmanager
def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for col in ("recognition TEXT", "doc_id INTEGER"):
            try:
                c.execute(f"ALTER TABLE requests ADD COLUMN {col}")
            except Exception:
                pass


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def dumps(v):
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
