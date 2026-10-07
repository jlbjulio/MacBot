from pathlib import Path


def render_deck(value, path, images=None):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    from .artifacts import Deck
    deck = Deck.model_validate(value)
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.33), Inches(7.5)
    presentation.core_properties.title = deck.title
    presentation.core_properties.author = "MacBot"
    theme = deck.theme
    images = images or {}

    def shape(slide, x, y, w, h, color, kind=MSO_SHAPE.RECTANGLE):
        item = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        item.fill.solid()
        item.fill.fore_color.rgb = RGBColor.from_string(color)
        item.line.fill.background()
        return item

    def text(slide, content, x, y, w, h, size=22, bold=False, color=None):
        frame = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = Inches(0.02)
        frame.margin_top = frame.margin_bottom = 0
        for number, line in enumerate(content.split("\n")):
            paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
            paragraph.text = line
            paragraph.font.name = theme.font
            paragraph.font.size = Pt(size)
            paragraph.font.bold = bold
            paragraph.font.color.rgb = RGBColor.from_string(color or theme.foreground)
            paragraph.space_after = Pt(12)
        return frame

    for number, data in enumerate(deck.slides, 1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(theme.background)
        layout = "image" if data.image_id else "cover" if number == 1 and data.layout == "bullets" else data.layout
        shape(slide, 0, 0, 0.18, 7.5, theme.accent)
        text(slide, deck.title.upper()[:75], 0.75, 0.38, 10.8, 0.35, size=10, color=theme.foreground)
        text(slide, f"{number:02d}", 11.85, 0.35, 0.7, 0.4, size=14, bold=True, color=theme.accent)
        shape(slide, 0.75, 6.82, 11.8, 0.025, theme.accent)
        text(slide, "MacBot  /  " + deck.title[:70], 0.75, 6.96, 11, 0.25, size=9)
        if layout == "cover":
            shape(slide, 9.6, 1.4, 2.5, 2.5, theme.accent, MSO_SHAPE.OVAL)
            shape(slide, 10.4, 3.8, 1.35, 1.35, theme.foreground, MSO_SHAPE.OVAL)
            text(slide, data.title, 0.82, 1.8, 8.4, 2.3, size=44 if len(data.title) < 65 else 34, bold=True)
            text(slide, data.subtitle or deck.subtitle or "\n".join(data.bullets[:3]), 0.85, 4.5, 8.1, 1.55, size=21)
        else:
            text(slide, data.title, 0.8, 1.0, 11.6, 1.05, size=32 if len(data.title) < 85 else 26, bold=True)
            if data.subtitle:
                text(slide, data.subtitle, 0.83, 2.08, 11.5, 0.55, size=16)
            top = 2.85 if data.subtitle else 2.55
            if layout == "image":
                text(slide, "\n".join(data.bullets[:5]), 0.9, top, 7.5, 3.55, size=21 if sum(map(len, data.bullets)) < 350 else 16)
            elif layout == "metric":
                text(slide, data.metric_value or "01", 0.85, top, 5.3, 1.6, size=78, bold=True, color=theme.accent)
                text(slide, data.metric_label or (data.bullets[0] if data.bullets else "Key takeaway"), 0.9, top + 1.75, 5.4, 1.2, size=22)
                text(slide, "\n".join(data.bullets[1:5]), 7.0, top + 0.1, 5.1, 3.4, size=20)
            elif layout == "quote":
                text(slide, "“", 0.78, top - 0.25, 1.0, 1.0, size=76, color=theme.accent)
                text(slide, "\n".join(data.bullets[:3]), 1.75, top + 0.05, 9.8, 3.45, size=30 if sum(map(len, data.bullets[:3])) < 180 else 23)
            elif layout == "timeline":
                entries = [entry.model_dump() for entry in data.columns] or [{"heading": f"{i + 1:02d}", "text": item} for i, item in enumerate(data.bullets[:4])]
                count = max(1, len(entries))
                shape(slide, 1.0, top + 0.65, 11.0, 0.055, theme.accent)
                for index, entry in enumerate(entries[:4]):
                    left = 0.95 + index * (11.5 / count)
                    shape(slide, left + 0.04, top + 0.46, 0.4, 0.4, theme.accent, MSO_SHAPE.OVAL)
                    text(slide, entry.get("heading", f"{index + 1:02d}"), left, top + 1.25, 10.4 / count, 0.9, size=22, bold=True)
                    text(slide, entry.get("text", ""), left, top + 2.2, 10.4 / count, 1.5, size=16)
            elif layout in ("split", "comparison") or data.columns:
                entries = [entry.model_dump() for entry in data.columns] or [{"heading": "Explore", "text": "\n".join(data.bullets[:len(data.bullets) // 2 or 1])},
                                           {"heading": "Put it into practice", "text": "\n".join(data.bullets[len(data.bullets) // 2 or 1:])}]
                count = min(3, len(entries))
                for index, entry in enumerate(entries[:count]):
                    left, width = 0.85 + index * (11.7 / count), 10.9 / count
                    shape(slide, left, top, width, 0.075, theme.accent)
                    text(slide, entry.get("heading", ""), left + 0.08, top + 0.35, width - 0.15, 0.85, size=23, bold=True)
                    text(slide, entry.get("text", ""), left + 0.08, top + 1.45, width - 0.2, 2.4, size=18 if count < 3 else 16)
            else:
                for index, item in enumerate(data.bullets[:6]):
                    left = 0.9 + (index % 2) * 6.0
                    y = top + (index // 2) * 1.22
                    text(slide, f"{index + 1:02d}", left, y, 0.65, 0.6, size=24, bold=True, color=theme.accent)
                    text(slide, item, left + 0.82, y, 4.62, 1.03, size=18 if len(item) < 110 else 15)
        if data.image_id:
            image = images.get(data.image_id)
            if image is None or not Path(image).is_file():
                raise ValueError("A slide references an image that was not attached to this request.")
            from PIL import Image, ImageOps
            import io
            with Image.open(image) as original:
                cropped = ImageOps.fit(original.convert("RGB"), (720, 720))
                buffer = io.BytesIO()
                cropped.save(buffer, format="PNG")
                buffer.seek(0)
                slide.shapes.add_picture(buffer, Inches(9.25), Inches(1.6), width=Inches(3.0), height=Inches(3.0))
        notes = data.notes
        if data.bullets:
            notes += "\n\nFull slide content:\n" + "\n".join(data.bullets)
        if notes:
            notes_frame = slide.notes_slide.notes_text_frame
            assert notes_frame is not None
            notes_frame.text = notes.strip()
    presentation.save(path)
    assert len(Presentation(path).slides) == len(deck.slides)
