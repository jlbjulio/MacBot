import io
import zipfile
from pathlib import Path


def extract(name, content):
    suffix = Path(name).suffix.lower()
    if suffix in (".docx", ".xlsx", ".pptx"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 50_000_000:
                raise ValueError("The expanded document exceeds the 50 MB limit.")
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
        if not content.startswith(signatures):
            raise ValueError("This image has an invalid format.")
        from .media import normalized_image, ocr_image
        return "image", ocr_image(normalized_image(content))
    if suffix in (".txt", ".md", ".csv", ".json", ".jsonl", ".py", ".ts", ".js"):
        return "document", content.decode("utf-8-sig", errors="replace")[:200000]
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if len(reader.pages) > 300:
            raise ValueError("PDFs are limited to 300 pages.")
        pages = [page.extract_text() or "" for page in reader.pages]
        if not any(text.strip() for text in pages):
            from .media import scan_pdf
            return "document", scan_pdf(content)[:200000]
        blank = [i for i, text in enumerate(pages) if not text.strip()]
        if blank:
            from .media import scan_pdf
            scanned = scan_pdf(content, blank)
            import re
            for match in re.finditer(r"Page (\d+)\n(.*?)(?=\n\nPage \d+\n|\Z)", scanned, flags=re.S):
                pages[int(match[1]) - 1] = match[2]
        text = "\n\n".join(f"Page {i + 1}\n{text}" for i, text in enumerate(pages))
        return "document", text[:200000]
    if suffix == ".pptx":
        from pptx import Presentation
        from pptx.shapes.autoshape import Shape
        from pptx.shapes.graphfrm import GraphicFrame
        slides = Presentation(io.BytesIO(content)).slides
        if len(slides) > 100:
            raise ValueError("Presentations are limited to 100 slides.")
        parts = []
        for i, slide in enumerate(slides, 1):
            parts.append(f"Slide {i}")
            for shape in slide.shapes:
                if isinstance(shape, Shape) and shape.has_text_frame:
                    parts.append(shape.text)
                elif isinstance(shape, GraphicFrame) and shape.has_table:
                    parts.extend(" | ".join(cell.text for cell in row.cells) for row in shape.table.rows)
        return "document", "\n".join(parts)[:200000]
    if suffix == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        parts.extend(
            " | ".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows
        )
        return "document", "\n".join(parts)[:200000]
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        parts = []
        try:
            for sheet in workbook:
                parts.append(f"Sheet: {sheet.title}")
                for i, row in enumerate(sheet.iter_rows(values_only=True)):
                    if i >= 1000:
                        parts.append("[Sheet truncated to 1,000 rows]")
                        break
                    parts.append(" | ".join(str(cell) if cell is not None else "" for cell in row[:50]))
        finally:
            workbook.close()
        return "document", "\n".join(parts)[:200000]
    raise ValueError("Unsupported file type. Use a PDF, document, spreadsheet, text, image, audio, or video file.")


def export_document(text, path, fmt):
    if fmt in ("docx", "pdf"):
        from .design import document_from_markdown, write_document
        write_document(document_from_markdown(text), path, fmt)
        return
    if fmt in ("md", "txt"):
        path.write_text(text, encoding="utf-8")
    else:
        raise ValueError("Unsupported export format.")
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("The generated file could not be verified.")
