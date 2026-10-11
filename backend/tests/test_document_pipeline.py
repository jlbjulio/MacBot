import io
from types import SimpleNamespace

from PIL import Image
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from macbot.documents import extract
from macbot.retrieval import RetrievalIndex


def test_chunks_keep_pages_slides_and_timestamps_separate(tmp_path):
    index = RetrievalIndex(tmp_path)
    index.tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    index.tokenizer.pre_tokenizer = Whitespace()
    chunks = list(index.chunks("Page 1\nFirst result\n\nPage 2\nSecond result\n\nSlide 3\nThird result\n\nAudio segment 0\nOpening speech"))
    assert [c["page"] for c in chunks[:2]] == [1, 2]
    assert "Second" not in chunks[0]["text"]
    assert chunks[2]["slide"] == 3
    assert chunks[3]["timestamp"] == 0 and chunks[3]["text"].endswith("Opening speech")


def test_duplicate_vectors_are_copied_without_model_inference(tmp_path):
    from qdrant_client import QdrantClient, models
    index = RetrievalIndex(tmp_path)
    index.client = QdrantClient(":memory:")
    index.client.create_collection(index.collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    index.client.upsert(index.collection, points=[models.PointStruct(id=1, vector=[1.0, 0.0],
        payload={"upload_id": "original", "name": "original.pdf", "text": "Invoice 837.29", "page": 2, "chunk": 0})])
    assert index.copy_upload("original", "copy", "renamed.pdf") == 1
    points, _ = index.client.scroll(index.collection, scroll_filter=models.Filter(must=[
        models.FieldCondition(key="upload_id", match=models.MatchValue(value="copy"))]), with_vectors=True)
    assert points[0].payload["name"] == "renamed.pdf" and points[0].payload["page"] == 2
    assert points[0].payload["text"] == "Invoice 837.29" and points[0].vector == [1.0, 0.0]
    assert index.client.count(index.collection).count == 2
    index.close()


def test_office_images_and_notes_reach_document_text(tmp_path, monkeypatch):
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches
    from macbot import ocr
    class Reader:
        def __init__(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, image):
            return "Scanned invoice total 837.29"
    monkeypatch.setattr(ocr, "DocumentOCR", Reader)
    image = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(image, format="PNG")
    doc = Document()
    doc.add_paragraph("Native document heading")
    doc.add_picture(io.BytesIO(image.getvalue()))
    path = tmp_path / "invoice.docx"
    doc.save(path)
    text = extract(path.name, path.read_bytes(), tmp_path)[1]
    assert "Native document heading" in text and "837.29" in text
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(image.getvalue()), 0, 0, width=Inches(2))
    slide.notes_slide.notes_text_frame.text = "Speaker notes: payment is due Friday"
    path = tmp_path / "invoice.pptx"
    deck.save(path)
    text = extract(path.name, path.read_bytes(), tmp_path)[1]
    assert "Slide 1" in text and "837.29" in text and "payment is due Friday" in text


def test_audio_segments_keep_their_own_transcript(tmp_path, monkeypatch):
    import numpy as np
    from macbot import media
    index = RetrievalIndex(tmp_path)
    saved = []
    class Embeddings:
        def encode(self, *args, **kwargs):
            return [np.array([1.0, 0.0])]
    index.gemma = Embeddings()
    index.client = SimpleNamespace(upsert=lambda collection, points: saved.extend(points))
    monkeypatch.setattr(media, "audio_waveform", lambda _: np.zeros(60 * 16000, dtype=np.float32))
    segments = [{"start": 0, "end": 20, "text": "Opening remarks"}, {"start": 35, "end": 55, "text": "Closing balance 837.29"}]
    assert index.index_media("audio", "meeting.wav", "audio", tmp_path / "meeting.wav", "whole transcript", segments) == 2
    assert saved[0].payload["text"] == "Opening remarks"
    assert saved[1].payload["text"] == "Closing balance 837.29" and saved[1].payload["timestamp"] == 30
