"""Validated design choices shared by generated documents and presentations."""
import re
from html import escape
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, model_validator


class Theme(BaseModel):
    background: str = Field(default="F5F1E8", pattern=r"^[0-9A-Fa-f]{6}$")
    foreground: str = Field(default="203430", pattern=r"^[0-9A-Fa-f]{6}$")
    accent: str = Field(default="B46B47", pattern=r"^[0-9A-Fa-f]{6}$")
    font: Literal["Calibri", "Arial", "Georgia", "Aptos", "Verdana"] = "Calibri"

    @model_validator(mode="after")
    def readable(self):
        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            values = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055)**2.4 for c in channels]
            return sum(c * weight for c, weight in zip(values, (0.2126, 0.7152, 0.0722)))
        first, second = sorted((luminance(self.background), luminance(self.foreground)))
        if (second + 0.05) / (first + 0.05) < 4.5:
            self.foreground = "151515" if luminance(self.background) > 0.179 else "FAFAFA"
        return self


class Table(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str | int | float | None]] = Field(max_length=40)


class Section(BaseModel):
    heading: str = Field(max_length=160)
    text: str = Field(default="", max_length=3500)
    bullets: list[str] = Field(default_factory=list, max_length=12)
    table: Table | None = None


class DocumentSpec(BaseModel):
    title: str = Field(max_length=160)
    subtitle: str = Field(default="", max_length=250)
    theme: Theme = Field(default_factory=Theme)
    format: Literal["docx", "pdf", "both"] = "docx"
    paper: Literal["A4", "LETTER"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    cover: bool = True
    sections: list[Section] = Field(min_length=1, max_length=20)


def shade(cell, color):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    element = OxmlElement("w:shd")
    element.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(element)


def write_document(value, path, fmt):
    from docx import Document
    from docx.styles.style import ParagraphStyle as WordParagraphStyle
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    spec = DocumentSpec.model_validate(value)
    if fmt == "docx":
        doc = Document()
        page = doc.sections[0]
        width, height = (8.27, 11.69) if spec.paper == "A4" else (8.5, 11)
        if spec.orientation == "landscape":
            width, height = height, width
        page.page_width, page.page_height = Inches(width), Inches(height)
        page.top_margin = page.bottom_margin = Inches(0.85)
        page.left_margin = page.right_margin = Inches(0.9)
        normal = cast(WordParagraphStyle, doc.styles["Normal"])
        normal.font.name, normal.font.size = spec.theme.font, Pt(11)
        normal.font.color.rgb = RGBColor.from_string("203430")
        heading_color = Theme(background="FFFFFF", foreground=spec.theme.accent).foreground
        normal.paragraph_format.space_after = Pt(9)
        normal.paragraph_format.line_spacing = 1.15
        for heading in ("Title", "Heading 1", "Heading 2"):
            style = cast(WordParagraphStyle, doc.styles[heading])
            style.font.name = spec.theme.font
            style.font.color.rgb = RGBColor.from_string(heading_color)
        doc.core_properties.title, doc.core_properties.author = spec.title, "MacBot"
        doc.add_heading(spec.title, 0)
        if spec.subtitle:
            doc.add_paragraph(spec.subtitle, "Subtitle")
        if spec.cover:
            doc.add_paragraph("CREATED FOR YOUR NEXT IDEA").runs[0].font.size = Pt(9)
            if len(spec.sections) > 2:
                doc.add_heading("Inside", 2)
                for section in spec.sections:
                    doc.add_paragraph(section.heading, "List Bullet")
            doc.add_page_break()
        page.header.paragraphs[0].text = spec.title[:90]
        footer_paragraph = page.footer.paragraphs[0]
        footer_paragraph.text = "MacBot  ·  "
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        footer_paragraph._p.append(field)
        for section in spec.sections:
            doc.add_heading(section.heading, 1)
            for paragraph in section.text.split("\n\n"):
                if paragraph.strip():
                    doc.add_paragraph(paragraph.strip())
            for bullet in section.bullets:
                doc.add_paragraph(bullet, "List Bullet")
            if section.table:
                table = doc.add_table(rows=1, cols=len(section.table.columns))
                table.style = "Table Grid"
                for cell, heading in zip(table.rows[0].cells, section.table.columns):
                    cell.text = heading
                    shade(cell, spec.theme.accent)
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
                        run.font.color.rgb = RGBColor.from_string(Theme(background=spec.theme.accent, foreground="FFFFFF").foreground)
                for number, row in enumerate(section.table.rows):
                    if len(row) != len(section.table.columns):
                        raise ValueError("Document table rows must match their columns.")
                    for cell, item in zip(table.add_row().cells, row):
                        cell.text = str(item) if item is not None else ""
                        if number % 2 == 0:
                            shade(cell, spec.theme.background)
                            for run in cell.paragraphs[0].runs:
                                run.font.color.rgb = RGBColor.from_string(spec.theme.foreground)
                doc.add_paragraph()
        doc.save(path)
        Document(path)
        return
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table as PDFTable, TableStyle, Flowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font = "Helvetica"
    fonts = {"Calibri": "calibri.ttf", "Arial": "arial.ttf", "Georgia": "georgia.ttf", "Aptos": "aptos.ttf", "Verdana": "verdana.ttf"}
    installed_font = Path("C:/Windows/Fonts") / fonts[spec.theme.font]
    if not installed_font.exists():
        installed_font = Path("C:/Windows/Fonts/arial.ttf")
    if installed_font.exists():
        font = "MacBotText" + installed_font.stem
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, str(installed_font)))
    page_size = A4 if spec.paper == "A4" else LETTER
    if spec.orientation == "landscape":
        page_size = landscape(page_size)
    accent = colors.HexColor("#" + spec.theme.accent)
    heading_color = colors.HexColor("#" + Theme(background="FFFFFF", foreground=spec.theme.accent).foreground)
    body = ParagraphStyle("Body", fontName=font, fontSize=10.5, leading=16, spaceAfter=10, textColor=colors.HexColor("#203430"))
    title = ParagraphStyle("Title", parent=body, fontSize=30, leading=36, textColor=heading_color, spaceAfter=20)
    heading = ParagraphStyle("Heading", parent=body, fontSize=18, leading=24, textColor=heading_color, spaceBefore=17, spaceAfter=12)
    elements: list[Flowable] = [Paragraph(escape(spec.title), title)]
    if spec.subtitle:
        elements.append(Paragraph(escape(spec.subtitle), body))
    if spec.cover:
        elements += [Spacer(1, 32), Paragraph("CREATED FOR YOUR NEXT IDEA", body)]
        if len(spec.sections) > 2:
            elements += [Paragraph("Inside", heading), *[Paragraph(escape(s.heading), body) for s in spec.sections]]
        elements.append(PageBreak())
    for section in spec.sections:
        elements.append(Paragraph(escape(section.heading), heading))
        elements += [Paragraph(escape(p.strip()), body) for p in section.text.split("\n\n") if p.strip()]
        elements += [Paragraph("• " + escape(b), body) for b in section.bullets]
        if section.table:
            tinted = ParagraphStyle("TintedCell", parent=body, textColor=colors.HexColor("#" + spec.theme.foreground))
            rows = [[Paragraph(escape(str(c)), tinted) for c in section.table.columns]]
            for number, row in enumerate(section.table.rows):
                if len(row) != len(section.table.columns):
                    raise ValueError("Document table rows must match their columns.")
                rows.append([Paragraph(escape(str(c) if c is not None else ""), tinted if number % 2 else body) for c in row])
            table = PDFTable(rows, colWidths=[(page_size[0] - 110) / len(section.table.columns)] * len(section.table.columns), repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + spec.theme.background)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + spec.theme.background)]),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, accent), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 7)]))
            elements += [table, Spacer(1, 12)]
    def footer(canvas, document):
        canvas.setStrokeColor(accent)
        canvas.line(55, 42, page_size[0] - 55, 42)
        canvas.setFont(font, 8)
        canvas.drawString(55, 28, spec.title[:65])
        canvas.drawRightString(page_size[0] - 55, 28, f"MacBot · {document.page}")
    SimpleDocTemplate(str(path), pagesize=page_size, rightMargin=55, leftMargin=55, topMargin=58, bottomMargin=60,
                      title=spec.title, author="MacBot").build(elements, onFirstPage=footer, onLaterPages=footer)
    from pypdf import PdfReader
    if not PdfReader(path).pages:
        raise ValueError("The generated document is empty.")


def document_from_markdown(text):
    sections, title, current = [], "Your MacBot conversation", {"heading": "Response", "text": "", "bullets": []}
    for line in text.splitlines():
        if line.startswith("# ") and not sections and not current["text"]:
            title = line[2:]
        elif re.match(r"^#{1,3}\s", line):
            if current["text"].strip() or current["bullets"]:
                sections.append(current)
            current = {"heading": re.sub(r"^#+\s", "", line), "text": "", "bullets": []}
        elif re.match(r"^\s*[-*]\s", line):
            current["bullets"].append(re.sub(r"^\s*[-*]\s", "", line))
        else:
            current["text"] += line + "\n"
    if current["text"].strip() or current["bullets"]:
        sections.append(current)
    expanded = []
    for section in sections:
        content = section["text"]
        for start in range(0, max(1, len(content)), 3500):
            expanded.append({"heading": section["heading"][:160] if not start else section["heading"][:145] + " (continued)",
                             "text": content[start:start + 3500], "bullets": section["bullets"][:12] if not start else []})
        for start in range(12, len(section["bullets"]), 12):
            expanded.append({"heading": section["heading"][:145] + " (continued)", "text": "", "bullets": section["bullets"][start:start + 12]})
    if len(expanded) > 20:
        raise ValueError("This response is too long for a styled export. Save it as Markdown or text.")
    return {"title": title[:160], "cover": False, "sections": expanded or [{"heading": "Response", "text": text[:3500]}]}
