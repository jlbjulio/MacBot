import io
import zipfile
from pathlib import Path


def extract(name, content, directory=None):
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
        return "image", ocr_image(normalized_image(content), directory)
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
            return "document", scan_pdf(content, directory=directory)[:200000]
        blank = [i for i, text in enumerate(pages) if not text.strip()]
        if blank:
            from .media import scan_pdf
            scanned = scan_pdf(content, blank, directory=directory)
            import re
            for match in re.finditer(r"Page (\d+)\n(.*?)(?=\n\nPage \d+\n|\Z)", scanned, flags=re.S):
                pages[int(match[1]) - 1] = match[2]
        from contextlib import ExitStack
        from .ocr import DocumentOCR
        from .store import data_directory
        with ExitStack() as stack:
            ocr = None
            image_count = 0
            for number, page in enumerate(reader.pages):
                if number in blank:
                    continue
                for picture in page.images:
                    image_count += 1
                    if image_count > 30:
                        raise ValueError("PDFs are limited to 30 embedded images for scanning.")
                    if ocr is None:
                        ocr = stack.enter_context(DocumentOCR(directory or data_directory()))
                    extracted = ocr.read(picture.image)
                    if extracted and " ".join(extracted.split()) not in " ".join(pages[number].split()):
                        pages[number] += "\n" + extracted
        text = "\n\n".join(f"Page {i + 1}\n{text}" for i, text in enumerate(pages))
        return "document", text[:200000]
    if suffix == ".pptx":
        from pptx import Presentation
        from pptx.shapes.autoshape import Shape
        from pptx.shapes.graphfrm import GraphicFrame
        from pptx.shapes.picture import Picture
        from pptx.shapes.group import GroupShape
        slides = Presentation(io.BytesIO(content)).slides
        if len(slides) > 100:
            raise ValueError("Presentations are limited to 100 slides.")
        parts = []
        from contextlib import ExitStack
        from .ocr import DocumentOCR
        from .media import normalized_image
        from .store import data_directory
        with ExitStack() as stack:
            ocr = None
            pictures = 0
            def read_shapes(shapes):
                nonlocal ocr, pictures
                for shape in shapes:
                    if isinstance(shape, GroupShape):
                        read_shapes(shape.shapes)
                    elif isinstance(shape, Shape) and shape.has_text_frame:
                        parts.append(shape.text)
                    elif isinstance(shape, GraphicFrame) and shape.has_table:
                        parts.extend(" | ".join(cell.text for cell in row.cells) for row in shape.table.rows)
                    elif isinstance(shape, GraphicFrame) and shape.has_chart:
                        chart = shape.chart
                        if chart.has_title and chart.chart_title.has_text_frame:
                            parts.append(chart.chart_title.text_frame.text)
                        parts.extend(str(c.label) for c in chart.plots[0].categories)
                        parts.extend(f"{s.name}: " + " | ".join(str(v) for v in s.values) for s in chart.series)
                    elif isinstance(shape, Picture):
                        pictures += 1
                        if pictures > 30:
                            raise ValueError("Presentations are limited to 30 images for scanning.")
                        if ocr is None:
                            ocr = stack.enter_context(DocumentOCR(directory or data_directory()))
                        parts.append(ocr.read(normalized_image(shape.image.blob)))
            for i, slide in enumerate(slides, 1):
                parts.append(f"Slide {i}")
                read_shapes(slide.shapes)
                if slide.has_notes_slide:
                    parts.append(slide.notes_slide.notes_text_frame.text)
        return "document", "\n".join(parts)[:200000]
    if suffix == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        parts.extend(
            " | ".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows
        )
        images = [r.target_part for r in doc.part.rels.values() if not r.is_external and r.reltype.endswith("/image")]
        if len(images) > 30:
            raise ValueError("Documents are limited to 30 images for scanning.")
        if images:
            from .ocr import DocumentOCR
            from .media import normalized_image
            from .store import data_directory
            with DocumentOCR(directory or data_directory()) as ocr:
                parts.extend(ocr.read(normalized_image(image.blob)) for image in images)
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
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            images = [name for name in archive.namelist() if name.startswith("xl/media/")
                      and Path(name).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tiff")]
            if len(images) > 30:
                raise ValueError("Workbooks are limited to 30 images for scanning.")
            if images:
                from .ocr import DocumentOCR
                from .media import normalized_image
                from .store import data_directory
                with DocumentOCR(directory or data_directory()) as ocr:
                    for image in images:
                        parts.extend(("Workbook image", ocr.read(normalized_image(archive.read(image)))))
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
