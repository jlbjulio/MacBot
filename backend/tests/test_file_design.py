from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from typing import Any, cast

from macbot.artifacts import prepare_sheet, write_sheet
from macbot.artifacts import Deck
import pytest


def test_currency_strings_become_real_chart_values(tmp_path):
    value = {"title": "Budget", "columns": ["Item", "Cost"],
             "rows": [["Coffee", "$5.00"], ["Books", "$12.00"]],
             "charts": [{"value_columns": [2]}]}
    output = tmp_path / "budget.xlsx"
    write_sheet(prepare_sheet(value, "Use currency formatting and a bar chart"), output)
    book = load_workbook(output)
    page = cast(Worksheet, book.active)
    assert page["B2"].value == 5
    assert page["B3"].value == 12
    assert "$" in page["B2"].number_format
    assert len(cast(Any, page)._charts) == 1
    book.close()


def test_empty_content_slides_are_rejected():
    with pytest.raises(ValueError, match="meaningful"):
        Deck.model_validate({"title": "Workshop", "slides": [{"title": "Comparison", "layout": "comparison", "bullets": []}]})


def test_markdown_model_body_becomes_formatted_document(tmp_path):
    from docx import Document
    from pypdf import PdfReader
    from macbot.design import write_document

    value = {"title": "Workshop", "sections": [
        {"heading": "Title Page", "text": "Prepared for the community"},
        {"heading": "Objectives", "text": "* **Skills:** Learn together.\n* Share music."},
        {"heading": "Timeline", "text": "| Phase | Activity |\n| :--- | ---: |\n| 1 | **Planning** |\n| 2 | Workshop |"},
    ]}
    word, pdf = tmp_path / "workshop.docx", tmp_path / "workshop.pdf"
    write_document(value, word, "docx")
    write_document(value, pdf, "pdf")
    doc = Document(word)
    assert len(doc.tables) == 1
    assert doc.tables[0].cell(1, 1).text == "Planning"
    assert any(p.style is not None and p.style.name == "List Bullet" and p.text.startswith("Skills:")
               and p.runs[0].bold for p in doc.paragraphs)
    assert "Title Page" not in [p.text for p in doc.paragraphs]
    assert any(p.text == "Prepared for the community" for p in doc.paragraphs)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
    assert "Planning" in text and "**" not in text and "|" not in text


def test_requested_timeline_is_a_native_table_when_model_returns_bullets(tmp_path):
    from docx import Document
    from docx.styles.style import ParagraphStyle
    from macbot.design import prepare_document, requested_theme, write_document

    plan = {"title": "Workshop", "sections": [
        {"heading": "Objectives", "text": "Learn together."},
        {"heading": "Implementation Timeline", "text": "Project phases.",
         "bullets": ["Month 1: Recruitment", "Month 2: Workshop"]},
    ]}
    formatted = prepare_document(plan, "Create a proposal with a small timeline table.")
    formatted["theme"] = requested_theme(formatted["theme"], "Use a purple and cream theme with Georgia.")
    path = tmp_path / "workshop.docx"
    write_document(formatted, path, "docx")
    document = Document(path)
    assert len(document.tables) == 1
    assert document.tables[0].cell(1, 0).text == "Month 1"
    assert document.tables[0].cell(2, 1).text == "Workshop"
    assert cast(ParagraphStyle, document.styles["Normal"]).font.name == "Georgia"
    assert "674183" in document.tables[0]._tbl.xml
    assert "Month 1: Recruitment" not in [p.text for p in document.paragraphs]
    assert prepare_document(plan, "Create a proposal without tables.")["sections"][1]["table"] is None
