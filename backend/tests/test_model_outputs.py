from PIL import Image, ImageDraw
import pytest

from macbot.artifacts import plain_background_alpha


def test_plain_background_becomes_transparent_around_the_subject():
    picture = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(picture).ellipse((50, 50, 150, 150), fill="#b33a2a")
    alpha = plain_background_alpha(picture)
    assert alpha is not None
    assert alpha.getpixel((5, 5)) < 20
    assert alpha.getpixel((100, 100)) > 235


def test_busy_background_stays_opaque():
    picture = Image.new("RGB", (120, 120), "#2c4a6b")
    assert plain_background_alpha(picture) is None


def test_failed_ocr_preparation_does_not_leave_an_event_loop(monkeypatch, tmp_path):
    from macbot import assets, ocr

    def unavailable(directory, name):
        raise ValueError("offline")

    monkeypatch.setattr(assets, "prepare_asset", unavailable)
    reader = ocr.DocumentOCR(tmp_path)
    with pytest.raises(ValueError):
        with reader:
            pass
    assert reader.runner is None
