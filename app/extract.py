"""Odczyt tekstu z PDF / DOCX / TXT. Skany PDF -> OCR (Tesseract, pol)."""
import io
import os


def _ocr_pdf(data: bytes) -> str:
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        pages = convert_from_bytes(data, dpi=200, last_page=40)
        return "\n\n".join(pytesseract.image_to_string(p, lang=os.getenv("OCR_LANG", "pol")) for p in pages)
    except Exception as e:  # brak tesseracta / popplera
        return f"[OCR niedostępny: {e}]"


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > 40:
                raise ValueError(f"dokument ma {len(pdf.pages)} stron (limit 40)")
            text = "\n\n".join((p.extract_text() or "") for p in pdf.pages)
        if len(text.strip()) < 40:  # skan bez warstwy tekstowej
            text = _ocr_pdf(data)
        return text.strip()
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts).strip()
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(io.BytesIO(data)), lang=os.getenv("OCR_LANG", "pol")).strip()
        except Exception as e:
            return f"[OCR niedostępny: {e}]"
    return data.decode("utf-8", errors="ignore").strip()
