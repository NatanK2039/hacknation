"""Anonimizacja: reguły (regex + sumy kontrolne) + LLM. Zwraca listę propozycji ze spanami.
Urzędnik akceptuje (szare -> czarne) lub dodaje własne fragmenty; dopiero wtedy tekst jest zamieniany."""
import re

from . import ai


def _pesel_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{11}", s):
        return False
    w = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    return (10 - sum(int(s[i]) * w[i] for i in range(10)) % 10) % 10 == int(s[10])


def _nip_ok(s: str) -> bool:
    d = re.sub(r"\D", "", s)
    if len(d) != 10:
        return False
    w = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    c = sum(int(d[i]) * w[i] for i in range(9)) % 11
    return c != 10 and c == int(d[9])


def _iban_ok(s: str) -> bool:
    d = re.sub(r"\s", "", s).upper()
    if not d.startswith("PL"):
        d = "PL" + d
    if len(d) != 28:
        return False
    r = d[4:] + d[:4]
    num = "".join(str(int(ch, 36)) for ch in r)
    return int(num) % 97 == 1


RULES = [
    ("PESEL", r"(?<!\d)\d{11}(?!\d)", None),
    ("RACHUNEK", r"(?<![\d])(?:PL\s?)?\d{2}(?:\s?\d{4}){6}(?!\d)", _iban_ok),
    ("NIP", r"(?<!\d)\d{3}-?\d{3}-?\d{2}-?\d{2}(?!\d)|(?<!\d)\d{3}-\d{2}-\d{2}-\d{3}(?!\d)", None),
    ("DOKUMENT", r"\b[A-Z]{3}\s?\d{6}\b", None),  # dowód osobisty
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", None),
    ("OSOBA", r"\b(?:Pan|Pani|Pana|Panu|Panią|Panem|p\.)\s(?P<v>[A-ZŁŚŻŹĆ][a-ząćęłńóśźż]+(?:\s[A-ZŁŚŻŹĆ][a-ząćęłńóśźż]+(?:-[A-ZŁŚŻŹĆ][a-ząćęłńóśźż]+)?)?)", None),
    ("TELEFON", r"(?<!\d)(?:\+48[\s-]?)?(?:\d{3}[\s-]?\d{3}[\s-]?\d{3}|\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2})(?!\d)", None),
    ("ADRES", r"\b(?:ul\.|al\.|os\.|pl\.)\s?[A-ZŁŚŻŹĆ][\wąęółśżźćń.\- ]{1,40}?\s\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?(?:,?\s\d{2}-\d{3}\s[A-ZŁŚŻŹĆ][\wąęółśżźćń]+)?", None),
    ("POJAZD", r"\b(?:C[BTWAILGNRDSZ]|[A-Z]{2,3})\s?[0-9][A-Z0-9]{3,4}\b", None),
    ("KW", r"\b[A-Z]{2}\d[A-Z]/\d{8}/\d\b", None),
    ("DATA_UR", r"(?:ur\.|urodzon[ya]\s(?:dnia\s)?)\s?\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}(?:\s?r\.)?(?:\s?w\s[A-ZŁŚŻŹĆ][\wąęółśżźćń]+)?", None),
    ("DZIALKA", r"\bdziałk[aię]\s(?:ewid\.\s|ewidencyjn[aej]+\s)?(?:nr\s)?\d+(?:/\d+)?", None),
]

# Kategorie opisowe z listy UMB – słowa kluczowe; zaznaczany jest fragment od słowa kluczowego do końca frazy (maks. ~8 słów)
_TAIL = r"[^.;,\n]{0,70}"
KEYWORD_RULES = [
    ("ZDROWIE", r"\b(?:zwolnieni\w*\s+lekarski\w*|chorob\w+|chory|chora|operacj\w+|leczeni\w+|hospitalizacj\w+|szpital\w*|niepełnosprawn\w+|diagnoz\w+|nowotw\w+|depresj\w+|uzależnieni\w+|nałog\w*|alkoholizm\w*|terapi\w+|ciąż\w+)"),
    ("RODZINA", r"\b(?:rozw[oó]d\w*|separacj\w+|wdow\w+|samotnie\s+wychow\w+|(?:ma|posiada|wychowuje)\s+\w*\s?(?:dzieci|dziecko|syna|córkę)|stan\s+cywilny)"),
    ("POMOC_SPOL", r"\b(?:zasiłk\w+|zasiłek|MOPS|pomoc\w*\s+społeczn\w+|świadczeni\w+\s+(?:socjaln|z\s+pomocy)\w*)"),
    ("KARALNOSC", r"\b(?:skazan\w+|wyrok\w*|karan\w+|karalnoś\w+|postępowani\w+\s+karn\w+|zarzut\w*)"),
    ("MAJATEK", r"\b(?:zadłużeni\w+|zaległoś\w+\s+(?:podatkow|czynszow)\w*|dochod\w+\s+(?:wynosi|w\s+wysokości)|komornik\w*|egzekucj\w+\s+komornicz\w+)"),
]

# Adresy i e-maile urzędu nie są danymi osobowymi do ukrycia
WHITELIST = [r"@um\.bydgoszcz\.pl", r"Jezuicka\s1", r"bip\.um\.bydgoszcz\.pl", r"52\s?585\s?89\s?13"]


def _whitelisted(s: str) -> bool:
    return any(re.search(w, s, re.I) for w in WHITELIST)


def detect(text: str, use_llm: bool = True) -> list:
    found = []
    for cat, pat, check in RULES:
        for m in re.finditer(pat, text):
            g = "v" if "v" in m.re.groupindex else 0
            s = m.group(g).strip().rstrip(".,;")
            st = m.start(g)
            if check and not check(s):
                continue
            if cat == "POJAZD" and not re.search(r"\d", s[2:]):
                continue
            if cat == "POJAZD" and re.fullmatch(r"[A-Z]{2,3}\s?\d{4,5}", s) and not re.search(r"(rejestr|pojazd|samoch|nr rej)", text[max(0, m.start()-60):m.start()], re.I):
                continue  # ograniczenie fałszywych trafień
            if _whitelisted(s):
                continue
            reason = "wzorzec " + cat
            if cat == "PESEL":
                reason = "PESEL – suma kontrolna poprawna" if _pesel_ok(s) else "11 cyfr – prawdopodobnie PESEL (suma kontrolna niepoprawna)"
            elif cat == "NIP":
                reason = "NIP – suma kontrolna poprawna" if _nip_ok(s) else "format NIP"
            elif cat == "RACHUNEK":
                reason = "IBAN – suma kontrolna poprawna" if _iban_ok(s) else "format numeru rachunku"
            found.append({"start": st, "end": st + len(s), "text": s, "category": cat, "source": "reguła", "reason": reason})
    for cat, kw in KEYWORD_RULES:
        for m in re.finditer(kw + _TAIL, text, re.I):
            frag = m.group(0).rstrip(" ,")
            found.append({"start": m.start(), "end": m.start() + len(frag), "text": frag, "category": cat, "source": "reguła",
                          "reason": "słowo kluczowe – kategoria opisowa z listy UMB (sprawdź kontekst)"})
    if use_llm and ai.enabled():
        try:
            for it in ai.detect_pii_llm(text):
                frag = (it.get("text") or "").strip()
                if len(frag) < 3 or _whitelisted(frag):
                    continue
                for m in re.finditer(re.escape(frag), text):
                    found.append({"start": m.start(), "end": m.end(), "text": frag, "category": it.get("category", "INNE"),
                                  "source": "AI", "reason": it.get("reason", "")})
        except Exception as e:
            found.append({"start": 0, "end": 0, "text": "", "category": "BŁĄD", "source": "AI", "reason": str(e)})
    markers = [(m.start(), m.end()) for m in re.finditer(r"\[\[.*?\]\]|\[źr\.[^\]]*\]", text)]
    found = [f for f in found if not any(f["start"] < e and f["end"] > s for s, e in markers)]
    return _merge(found)


def _merge(items: list) -> list:
    items = [i for i in items if i["end"] > i["start"]]
    items.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    out = []
    for it in items:
        if out and it["start"] < out[-1]["end"]:
            if it["end"] > out[-1]["end"]:
                out[-1]["end"] = it["end"]
            if it["source"] not in out[-1]["source"]:
                out[-1]["source"] += "+" + it["source"]
            continue
        out.append(dict(it))
    for i, it in enumerate(out):
        it["id"] = i
    return out


LABELS = {"OSOBA": "[DANE OSOBOWE]", "ADRES": "[ADRES]", "KOD_ADRES": "[ADRES]", "PESEL": "[PESEL]", "NIP": "[NIP]",
          "RACHUNEK": "[NR RACHUNKU]", "DOKUMENT": "[NR DOKUMENTU]", "EMAIL": "[E-MAIL]", "TELEFON": "[TELEFON]",
          "POJAZD": "[NR REJ.]", "KW": "[NR KW]", "ZDROWIE": "[DANE O ZDROWIU]", "DATA_UR": "[DATA URODZENIA]",
          "KONTAKT": "[DANE KONTAKTOWE]", "PODPIS": "[PODPIS]", "WIZERUNEK": "[WIZERUNEK]", "RODZINA": "[DANE O SYTUACJI RODZINNEJ]",
          "MAJATEK": "[DANE O SYTUACJI MAJĄTKOWEJ]", "POMOC_SPOL": "[DANE CHRONIONE]", "POGLADY": "[DANE CHRONIONE]",
          "PRZYNALEZNOSC": "[DANE CHRONIONE]", "POCHODZENIE": "[DANE CHRONIONE]", "ORIENTACJA": "[DANE CHRONIONE]",
          "KARALNOSC": "[DANE CHRONIONE]", "DZIALKA": "[NR DZIAŁKI]", "T_PRZEDSIEB": "[TAJEMNICA PRZEDSIĘBIORSTWA]",
          "T_SKARBOWA": "[TAJEMNICA SKARBOWA]", "T_BANKOWA": "[TAJEMNICA BANKOWA]", "T_ZAWODOWA": "[TAJEMNICA USTAWOWO CHRONIONA]",
          "NIEJAWNE": "[INFORMACJA NIEJAWNA]"}


def apply(text: str, spans: list) -> str:
    """spans: [{start,end,category}] – zaakceptowane przez urzędnika."""
    spans = sorted([s for s in spans if s["end"] > s["start"]], key=lambda s: s["start"], reverse=True)
    last = len(text) + 1
    for s in spans:
        if s["end"] > last:
            continue
        text = text[:s["start"]] + LABELS.get(s.get("category"), "[ZANONIMIZOWANO]") + text[s["end"]:]
        last = s["start"]
    return text
