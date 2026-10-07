from macbot.retrieval import RetrievalIndex


def test_qdrant_filters_out_other_conversations(tmp_path):
    from qdrant_client import QdrantClient, models

    class Embedding:
        def embed(self, texts):
            import numpy as np

            yield np.array([1.0, 0.0])

    index = RetrievalIndex(tmp_path)
    index.client = QdrantClient(":memory:")
    index.embedding = Embedding()
    index.client.create_collection(
        "documents_e5_v1", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE)
    )
    index.client.upsert(
        "documents_e5_v1",
        points=[
            models.PointStruct(id=1, vector=[1.0, 0.0], payload={"upload_id": "allowed", "text": "visible"}),
            models.PointStruct(
                id=2, vector=[1.0, 0.0], payload={"upload_id": "other-chat", "text": "private"}
            ),
        ],
    )
    result = index.search("question", ["allowed"])
    assert [item["text"] for item in result] == ["visible"]
    index.close()


def test_chunking_preserves_token_boundaries_and_full_tail(tmp_path):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    index = RetrievalIndex(tmp_path)
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "dato": 1, "final": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    index.tokenizer = tokenizer
    text = " ".join(["dato"] * 700 + ["final"])
    chunks = list(index.chunks(text))
    assert all(len(tokenizer.encode(chunk["text"], add_special_tokens=False).ids) <= 320 for chunk in chunks)
    assert chunks[-1]["text"].endswith("final")
