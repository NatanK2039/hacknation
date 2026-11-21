"""Kontrola halucynacji w projekcie pisma: liczby i daty spoza odpowiedzi wydziałów, zdania bez źródła, miejsca do uzupełnienia."""
import re

NUM_RE = re.compile(r"\d[\d  .,]*\d|\d")
LEGAL_CTX = re.compile(r"(art\.|ust\.|pkt|poz\.|§|Dz\.\s?U\.|nr\s+\d+/\d{4}|ustawy z dnia|r\. o |BKS\.|WOR|1431)", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[\s ]", "", s).replace(",", ".").rstrip(".")


def _variants(n: str):
    v = {_norm(n)}
    x = _norm(n)
    if re.fullmatch(r"\d+\.00", x):
        v.add(x[:-3])
    v.add(x.replace(".", ""))
    return v


def _has(hay: str, v: str) -> bool:
    return re.search(r"(?<!\d)(?<!\d[.,])" + re.escape(v) + r"(?!\d)", hay) is not None


def verify(draft: str, responses: list, request_text: str = "") -> dict:
    src = " ".join(r.get("text", "") for r in responses)
    src_norm = _norm(src)
    req_norm = _norm(request_text or "")
    issues = []
    lines = draft.splitlines(keepends=True)
    # punkt odpowiedzi = linia "N." + kolejne linie aż do pustej / następnego punktu; źródło może stać na końcu punktu
    block_has_src, cur, blk = {}, [], []
    for idx, ln in enumerate(lines):
        st = ln.strip()
        if re.match(r"^\d+[.)]\s", st) or not st:
            if blk:
                for j in blk:
                    block_has_src[j] = any("[źr." in lines[k] for k in blk)
            blk = [idx] if st else []
        else:
            blk.append(idx)
    for j in blk:
        block_has_src[j] = any("[źr." in lines[k] for k in blk)
    pos = 0
    body_started = False
    for i, line in enumerate(lines):
        start = pos
        pos += len(line)
        low = line.lower()
        if re.search(r"^(szanown|w odpowiedzi|uprzejmie)", low.strip()):
            body_started = True
        if not body_started:  # nagłówek: data, znak, adresat
            continue
        if re.search(r"z poważaniem|^\s*\[\[podpis", low):
            break
        if re.match(r"\s*(szanown|w odpowiedzi|w związku|uprzejmie informuj\w*:?\s*$|informację przekazuj|pouczeni)", low):
            continue  # formuły wstępne / końcowe pisma
        # liczby
        for m in NUM_RE.finditer(line):
            tok = m.group(0).strip(" .,")
            if len(_norm(tok)) < 1:
                continue
            ctx = line[max(0, m.start() - 18):m.start()]
            if LEGAL_CTX.search(ctx) or LEGAL_CTX.search(line[m.start():m.end() + 6]):
                continue
            if re.fullmatch(r"\d", _norm(tok)) and re.match(r"\s*[.)]", line[m.end():m.end() + 2]):
                continue  # numeracja punktów "1."
            if re.search(r"\[źr\.\s*W$", line[max(0, m.start() - 6):m.start()]):
                continue
            if any(v and _has(src_norm, v) for v in _variants(tok)):
                continue
            in_req = any(v and _has(req_norm, v) for v in _variants(tok))
            s0 = start + m.start() + (len(m.group(0)) - len(m.group(0).lstrip(" .,")))
            issues.append({"start": s0, "end": s0 + len(tok), "kind": "liczba",
                           "level": "warn" if in_req else "error",
                           "text": tok, "msg": ("pochodzi z wniosku, nie z odpowiedzi wydziału" if in_req else
                                                "liczba/data nie występuje w odpowiedziach wydziałów – możliwa halucynacja")})
        # zdania merytoryczne bez źródła (punkty odpowiedzi)
        stripped = line.strip()
        if re.match(r"^(\d+[.)]|[-–•])\s+\S", stripped) or (len(stripped) > 60 and re.search(r"\d", stripped)):
            if "[źr." not in line and "[[" not in line and not block_has_src.get(i):
                lead = len(line) - len(line.lstrip())
                issues.append({"start": start + lead, "end": start + len(line.rstrip()), "kind": "źródło", "level": "warn",
                               "text": stripped[:80], "msg": "zdanie merytoryczne bez wskazania źródła [źr. W…]"})
    for m in re.finditer(r"\[\[(.*?)\]\]", draft):
        if m.group(1).strip().upper() == "PODPIS":
            continue
        issues.append({"start": m.start(), "end": m.end(), "kind": "uzupełnij", "level": "todo", "text": m.group(0),
                       "msg": "miejsce do uzupełnienia / decyzji urzędnika"})
    issues.sort(key=lambda x: (x["start"], x["end"]))
    # usuń nakładające się (zostaw węższe – liczby)
    out = []
    for it in issues:
        if it["kind"] == "źródło" and any(o["start"] < it["end"] and o["end"] > it["start"] and o["kind"] != "źródło" for o in issues):
            it = dict(it, overlap=True)
        out.append(it)
    counts = {"error": sum(1 for x in out if x["level"] == "error"), "warn": sum(1 for x in out if x["level"] == "warn"),
              "todo": sum(1 for x in out if x["level"] == "todo")}
    nums_checked = len([1 for _ in NUM_RE.finditer(draft)])
    return {"issues": out, "counts": counts, "numbers_in_draft": nums_checked,
            "ok": counts["error"] == 0}
