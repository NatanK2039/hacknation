import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor


def build_docx(text: str, header: str = "URZĄD MIASTA BYDGOSZCZY") -> bytes:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    h = doc.add_paragraph()
    r = h.add_run(header)
    r.bold = True
    r.font.size = Pt(12)
    doc.add_paragraph("ul. Jezuicka 1, 85-102 Bydgoszcz").runs[0].font.size = Pt(9)
    doc.add_paragraph()
    for line in text.splitlines():
        p = doc.add_paragraph()
        if line.strip().startswith("Bydgoszcz,"):
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        # znaczniki [[...]] wyróżnione na czerwono – do uzupełnienia przez urzędnika
        for part in re.split(r"(\[\[.*?\]\])", line):
            if not part:
                continue
            run = p.add_run(part)
            if part.startswith("[["):
                run.bold = True
                run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    doc.add_paragraph()
    f = doc.add_paragraph("PROJEKT wygenerowany przez asystenta AI – wymaga weryfikacji merytorycznej przez urzędnika.")
    f.runs[0].font.size = Pt(8)
    f.runs[0].italic = True
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
