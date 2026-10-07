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
