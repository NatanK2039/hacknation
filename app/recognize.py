"""Rozpoznawanie dokumentów: warstwa tekstowa -> OCR Tesseract (z oceną pewności) -> AI-vision (skany słabej jakości, pismo odręczne).
Klasyfikacja pisma (wniosek / odpowiedź / kwerenda / doprecyzowanie), sygnatura BKS.IV.1431.N.RRRR, data i nr wpływu z pieczątek."""
import base64
import io
import json
import os
import re

from . import ai

OCR_LANG = os.getenv("OCR_LANG", "pol")
OCR_MIN_CONF = float(os.getenv("OCR_MIN_CONF", "78"))
# 1 = każdy skan (strona bez warstwy tekstowej) przechodzi przez AI-vision (lepsza treść + odczyt pieczątek); 0 = tylko gdy OCR ma niską pewność

SIG_RE = re.compile(r"BKS\.?\s?IV\.?\s?1431\.\s?(\d{1,4})\.\s?(\d{4})", re.I)
DATE_RE = r"(\d{1,2})[.\-/ ]\s?(\d{1,2})[.\-/ ]\s?(\d{4})"

TYPES = {"wniosek": "Wniosek", "odpowiedz": "Odpowiedź", "kwerenda": "Kwerenda / przedłużenie terminu",
         "doprecyzowanie": "Wezwanie do doprecyzowania", "decyzja": "Decyzja", "inne": "Inne pismo"}


# ---------------- OCR ----------------

def _ocr_timeout() -> int:
    from . import settings
    return settings.get_int("ocr_timeout", 25)


def _vision_for_scans() -> bool:
    from . import settings
    return str(settings.get("vision_for_scans", "1")) == "1"


def _tesseract(img) -> dict:
    import pytesseract
    d = pytesseract.image_to_data(img, lang=OCR_LANG, output_type=pytesseract.Output.DICT, config="--psm 3", timeout=_ocr_timeout())
    words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
    text = pytesseract.image_to_string(img, lang=OCR_LANG, config="--psm 3", timeout=_ocr_timeout())
    if not words:
        return {"text": text.strip(), "conf": 0.0, "words": 0, "real_words": 0}
    conf = sum(c for _, c in words) / len(words)
    real = sum(1 for w, c in words if c > 60 and re.fullmatch(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3,}[.,:;]?", w))
    return {"text": text.strip(), "conf": round(conf, 1), "words": len(words), "real_words": real}


def _img_b64(img, max_side=1700) -> str:
    im = img.convert("RGB")
    w, h = im.size
    s = max(w, h)
    if s > max_side:
        im = im.resize((int(w * max_side / s), int(h * max_side / s)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


VISION_PROMPT = """Jesteś systemem rozpoznawania pism wpływających do Urzędu Miasta Bydgoszczy (kancelaria). Na obrazie jest strona pisma:
skan wydruku, e-maila albo pismo odręczne (np. drukowanymi literami na papierze w kratkę). Zadania:
1. Przepisz DOSŁOWNIE całą treść pisma, linia po linii, w kolejności czytania (także nagłówek: nadawca, miejscowość i data, adresat).
   ZASADY WIERNOŚCI: zachowaj KAŻDE słowo z oryginału, w tym imiona, nazwiska i nazwy własne – nigdy nie zastępuj ich innymi słowami
   (np. nie zamieniaj nazwiska na „Pana”) i nie zgaduj nazw miejscowości. Wolno Ci jedynie: zamienić tekst pisany wielkimi literami na normalną pisownię (zdania i nazwy własne – imiona, nazwiska,
   miejscowości, instytucje – wielką literą) i dodać brakujące polskie znaki diakrytyczne. Słowo, którego nie jesteś pewien, zapisz w najbardziej prawdopodobnej formie i dodaj [?];
   fragment całkowicie nieczytelny oznacz [nieczytelne]. Fragmenty zamazane/zaczernione (anonimizacja) oznacz [zanonimizowano].
2. Pieczątki, naklejki z kodem kreskowym i adnotacje urzędowe NIE wchodzą do treści – opisz je osobno w polu stamps.
Zwróć WYŁĄCZNIE JSON:
{"text": "...", "handwritten": true/false, "quality": "dobra|średnia|słaba",
 "stamps": [{"kind": "pieczęć wpływu|naklejka kancelarii|podpis|inne", "text": "odczytana treść"}],
 "received_date": "YYYY-MM-DD lub null (data wpływu z pieczęci/naklejki urzędu)", "entry_number": "nr wpływu z pieczęci, np. 3468/2026, lub null",
 "signature": "sygnatura sprawy jeśli widoczna (np. BKS.IV.1431.16.2026) lub null", "notes": "krótkie uwagi o czytelności"}"""


def _vision(img) -> dict:
    import time
    t0 = time.time()
    r = ai.client().chat.completions.create(
        model=ai.vision_model(), temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _img_b64(img), "detail": "high"}}]}])
    ai.track(r, "AI-vision (skan)", ai.vision_model(), t0)
    try:
        return json.loads(r.choices[0].message.content or "{}")
    except Exception:
        return {"text": r.choices[0].message.content or ""}


def _page_needs_vision(o: dict) -> bool:
    if o["words"] < 12:
        return True
    if o["conf"] < OCR_MIN_CONF:
        return True
    return o["real_words"] < max(8, 0.45 * o["words"])


def _process_image(img, n: int, force_vision: bool = False) -> dict:
    page = {"n": n, "method": "ocr", "conf": None, "handwritten": False, "stamps": [], "notes": ""}
    o = {"text": "", "conf": 0.0, "words": 0, "real_words": 0}
    try:
        o = _tesseract(img)
    except Exception as e:
        page["notes"] = f"Tesseract niedostępny: {e}"
    page.update({"text": o["text"], "conf": o["conf"], "ocr_words": o["words"]})
    low = _page_needs_vision(o)
    page["ocr_low_conf"] = low
    if (force_vision or low or _vision_for_scans()) and ai.enabled():
        try:
            v = _vision(img)
            page.update({"method": "ai-vision", "text": (v.get("text") or "").strip(), "handwritten": bool(v.get("handwritten")),
                         "stamps": v.get("stamps") or [], "quality": v.get("quality"), "notes": v.get("notes") or "",
                         "received_date": v.get("received_date"), "entry_number": v.get("entry_number"), "signature": v.get("signature"),
                         "ocr_conf_before": o["conf"], "ocr_text": o["text"][:4000],
                         "escalation": "pismo odręczne / niska pewność OCR" if low else "skan – odczyt treści i pieczątek"})
            page["conf"] = {"dobra": 92, "średnia": 75, "słaba": 55}.get(v.get("quality"), 80)
        except Exception as e:
            page["notes"] = (page["notes"] + f" | AI-vision błąd: {e}").strip(" |")
    elif _page_needs_vision(o):
        page["notes"] = "Niska pewność OCR – włącz AI, aby użyć rozpoznawania wizyjnego"
    return page


# ---------------- główna funkcja ----------------

def recognize(filename: str, data: bytes, force_vision: bool = False, keep_images: bool = True) -> dict:
    name = (filename or "").lower()
    pages, images = [], []
    if name.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            layer = [(p.extract_text() or "") for p in pdf.pages]
        need_raster = force_vision or any(len(re.sub(r"\s", "", t)) < 80 for t in layer) or keep_images
        rendered = []
        if need_raster:
            try:
                from pdf2image import convert_from_bytes
                rendered = convert_from_bytes(data, dpi=200)
            except Exception:
                rendered = []
        for i, t in enumerate(layer):
            img = rendered[i] if i < len(rendered) else None
            if img is not None:
                images.append(img)
            if len(re.sub(r"\s", "", t)) >= 80 and not force_vision:
                pages.append({"n": i + 1, "method": "warstwa tekstowa", "conf": 99, "text": t.strip(), "handwritten": False, "stamps": []})
            elif img is not None:
                pages.append(_process_image(img, i + 1, force_vision))
            else:
                pages.append({"n": i + 1, "method": "brak", "conf": 0, "text": t, "notes": "nie udało się wyrenderować strony"})
    elif name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic")) or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")  # zdjęcia z telefonu – orientacja z EXIF
        if max(img.size) > 2400:
            img.thumbnail((2400, 2400))
        images.append(img)
        pages.append(_process_image(img, 1, force_vision))
    elif name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        pages.append({"n": 1, "method": "dokument Word", "conf": 100, "text": "\n".join(parts).strip(), "handwritten": False, "stamps": []})
    else:
        pages.append({"n": 1, "method": "tekst", "conf": 100, "text": data.decode("utf-8", errors="ignore").strip(), "handwritten": False, "stamps": []})

    text = "\n\n".join(p.get("text", "") for p in pages).strip()
    meta = classify(filename, text, pages)
    methods = sorted({p["method"] for p in pages})
    confs = [p["conf"] for p in pages if isinstance(p.get("conf"), (int, float))]
    return {"filename": filename, "text": text, "pages": pages, "methods": methods,
            "conf": round(sum(confs) / len(confs), 1) if confs else None,
            "handwritten": any(p.get("handwritten") for p in pages), **meta, "_images": images}


def classify(filename: str, text: str, pages: list) -> dict:
    fn = filename or ""
    low = (fn + " " + text[:3000]).lower()
    # sygnatura: nazwa pliku > treść > pieczątka (vision)
    sig = None
    for src in [fn, text] + [p.get("signature") or "" for p in pages]:
        m = SIG_RE.search(src or "")
        if m:
            sig = f"BKS.IV.1431.{int(m.group(1))}.{m.group(2)}"
            break
    # typ pisma: nazwa pliku (konwencja kancelarii) > treść
    f = fn.lower()
    if re.search(r"doprecyz", f):
        t = "doprecyzowanie"
    elif re.search(r"kwerend|przedłuż", f):
        t = "kwerenda"
    elif re.search(r"odpowied", f):
        t = "odpowiedz"
    elif re.search(r"decyzj", f):
        t = "decyzja"
    elif re.search(r"wniosek", f):
        t = "wniosek"
    elif re.search(r"doprecyzow|wezwani\w+ do (?:sprecyz|doprecyz|uzupeł)|sprecyzowani", low):
        t = "doprecyzowanie"
    elif re.search(r"kwerend|przedłuż\w* termin|art\.\s?13 ust\.\s?2|nie jest możliwe udostępnienie informacji w terminie", low):
        t = "kwerenda"
    elif re.search(r"doprecyzow|wezwani\w+ do (?:sprecyz|doprecyz|uzupeł)|sprecyzowani", low):
        t = "doprecyzowanie"
    elif re.search(r"odpowied[zź]_|odpowiedź\b|^odpowied", fn.lower()) or re.search(r"w odpowiedzi na (?:wystosowany |złożony )?(?:przez pan\w* )?wniosek|uprzejmie informuj", low):
        t = "odpowiedz"
    elif re.search(r"\bdecyzj\w+\b.{0,40}odmow", low):
        t = "decyzja"
    elif re.search(r"wniosek|wnosz[eę]|w trybie (?:dostępu|ustawy)|proszę o (?:udostępnienie|przesłanie|informacj)|prośb\w+ o udziel", low):
        t = "wniosek"
    else:
        t = "inne"
    # data wpływu i nr wpływu: pieczątki z vision > regex
    rd, entry = None, None
    for p in pages:
        rd = rd or p.get("received_date")
        entry = entry or p.get("entry_number")
    if not rd:
        m = re.search(r"(?:data\s+)?wpływu[:\s]*" + DATE_RE, text, re.I) or re.search(r"wpływ\w*\s+dnia\s*" + DATE_RE, text, re.I)
        if m:
            rd = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if not entry:
        m = re.search(r"nr\s+wpływu[:\s]*([0-9]{1,6}/[0-9]{4})", text, re.I)
        entry = m.group(1) if m else None
    return {"doc_type": t, "doc_type_label": TYPES[t], "signature": sig, "received_date": rd, "entry_number": entry}
