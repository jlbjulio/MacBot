import base64
import io

from PIL import Image
import pytest

from macbot.inference import completion_body


def test_thinking_budgets_and_schema_remain_separate_from_answer_budget():
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    for level, budget in (("low", 128), ("medium", 256), ("high", 512)):
        request = completion_body({"reasoning": level, "max_tokens": 100, "json_mode": schema,
                                   "messages": [{"role": "user", "content": "Create a title"}]})
        assert request["reasoning_budget_tokens"] == budget
        assert request["max_tokens"] == 100 + budget
        assert request["response_format"]["json_schema"]["schema"] == schema
    with pytest.raises(ValueError, match="Low, Medium or High"):
        completion_body({"reasoning": "off"})


def test_images_are_bounded_and_translated_to_embedded_media_only():
    picture = io.BytesIO()
    Image.new("RGB", (900, 600), "purple").save(picture, format="PNG")
    encoded = base64.b64encode(picture.getvalue()).decode()
    request = completion_body({"messages": [{"role": "user", "content": "Describe this", "images": [encoded]}]})
    media = request["messages"][0]["content"][1]["image_url"]["url"]
    assert media.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(media.split(",", 1)[1]))) as resized:
        assert resized.width == 768 and resized.height == 512
    with pytest.raises(ValueError):
        completion_body({"messages": [{"role": "user", "content": "", "images": ["https://example.com/image.png"]}]})
