import re
import threading
from typing import Any
from uuid import NAMESPACE_URL, uuid5

EMBEDDING_MODEL = "MacBot/multilingual-e5-small-int8"
MODEL_REPO = "Xenova/multilingual-e5-small"


class RetrievalIndex:
    def __init__(self, directory, model_directory=None, engine="e5"):
        self.directory = directory
        self.model_directory = model_directory or directory / "models" / "embeddings"
        self.lock = threading.RLock()
        self.client = None
        self.embedding: Any = None
        self.tokenizer = None
        self.engine = engine
        self.gemma = None
        self.collection = "documents_e5_v1"

    def load(self):
        if self.client is not None:
            return
        if self.engine == "gemma2":
            from .assets import GemmaEmbeddings, prepare_asset
            from qdrant_client import QdrantClient, models
            from tokenizers import Tokenizer
            self.gemma = GemmaEmbeddings(self.directory)
            self.collection = self.gemma.collection
            path = prepare_asset(self.directory, "embedding")
            self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
            self.tokenizer.no_truncation()
            self.client = QdrantClient(path=str(self.directory / "qdrant"))
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE))
            return
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
        from huggingface_hub import hf_hub_download
        from qdrant_client import QdrantClient, models
        from tokenizers import Tokenizer

        if not any(item["model"] == EMBEDDING_MODEL for item in TextEmbedding.list_supported_models()):
            TextEmbedding.add_custom_model(
                model=EMBEDDING_MODEL,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=MODEL_REPO),
                dim=384,
                model_file="onnx/model_quantized.onnx",
                license="MIT",
            )
        model_dir = self.model_directory
        cached_weights = next(model_dir.glob("models--Xenova--multilingual-e5-small/snapshots/*/onnx/model_quantized.onnx"), None)
        self.embedding = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(model_dir), threads=2,
                                       specific_model_path=str(cached_weights.parent.parent) if cached_weights else None)
        tokenizer_file = next((model_dir / "tokenizer").glob("models--Xenova--multilingual-e5-small/snapshots/*/tokenizer.json"), None)
        if tokenizer_file is None:
            tokenizer_file = hf_hub_download(MODEL_REPO, "tokenizer.json", cache_dir=str(model_dir / "tokenizer"))
        tokenizer_file = str(tokenizer_file)
        self.tokenizer = Tokenizer.from_file(tokenizer_file)
        self.tokenizer.no_truncation()
        client = QdrantClient(path=str(self.directory / "qdrant"))
        if not client.collection_exists("documents_e5_v1"):
            client.create_collection(
                "documents_e5_v1",
                vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
            )
        self.client = client

    def chunks(self, text):
        assert self.tokenizer is not None
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        for start in range(0, len(encoded.ids), 272):
            offsets = encoded.offsets[start : start + 320]
            if not offsets:
                break
            left, right = offsets[0][0], offsets[-1][1]
            page_numbers = re.findall(r"(?:Page|Página) (\d+)\n", text[: left + 20])
            yield {
                "text": text[left:right],
                "offset": left,
                "page": int(page_numbers[-1]) if page_numbers else None,
            }

    def index(self, uid, name, text):
        from qdrant_client import models

        with self.lock:
            self.load()
            assert self.client is not None
            chunks = list(self.chunks(text))
            for start in range(0, len(chunks), 16):
                batch = chunks[start : start + 16]
                vectors = self.gemma.encode([chunk["text"] for chunk in batch]) if self.gemma else list(
                    self.embedding.embed(["passage: " + chunk["text"] for chunk in batch], batch_size=4)
                )
                self.client.upsert(
                    self.collection,
                    points=[
                        models.PointStruct(
                            id=str(uuid5(NAMESPACE_URL, f"{uid}:{start + i}")),
                            vector=vector.tolist(),
                            payload={**chunk, "upload_id": uid, "name": name, "chunk": start + i},
                        )
                        for i, (chunk, vector) in enumerate(zip(batch, vectors))
                    ],
                )
            return len(chunks)

    def search(self, query, uploads, limit=6):
        if not uploads:
            return []
        from qdrant_client import models

        with self.lock:
            self.load()
            assert self.client is not None
            vector = (self.gemma.encode([query], query=True)[0] if self.gemma else next(iter(self.embedding.embed(["query: " + query])))).tolist()
            hits = self.client.query_points(
                self.collection,
                query=vector,
                limit=limit,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="upload_id", match=models.MatchAny(any=uploads))]
                ),
            ).points
            return [{**(hit.payload or {}), "score": hit.score} for hit in hits]

    def index_media(self, uid, name, kind, path, text):
        from qdrant_client import models
        from PIL import Image
        from .media import audio_waveform, video_frames
        with self.lock:
            self.load()
            assert self.client is not None
            if self.gemma is None:
                return self.index(uid, name, text) if text else 0
            items = []
            if kind == "image":
                item = {"image": Image.open(path).convert("RGB")}
            elif kind == "audio":
                waveform = audio_waveform(path)
                items = [{"audio": {"array": waveform[start:start + 30 * 16000], "sampling_rate": 16000}}
                         for start in range(0, len(waveform), 30 * 16000)]
                if not items:
                    raise ValueError("This audio contains no decodable samples.")
                item = None
            else:
                frames, _ = video_frames(path)
                if not frames:
                    raise ValueError("No video frames could be decoded.")
                item = {"video": [image for _, image in frames]}
            if item is not None:
                items = [item]
            for index, value in enumerate(items):
                vector = self.gemma.encode([value], media=True)[0]
                self.client.upsert(self.collection, points=[models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"{uid}:media:{index}")), vector=vector.tolist(),
                    payload={"upload_id": uid, "name": name, "kind": kind, "chunk": index,
                             "text": text[:6000] or f"{kind.title()} attachment: {name}", "offset": 0,
                             "timestamp": index * 30 if kind == "audio" else None})])
            return len(items)

    def release_models(self):
        if self.gemma is not None:
            self.gemma.release()

    def delete_uploads(self, identifiers):
        if not identifiers or not (self.directory / "qdrant").exists():
            return
        from qdrant_client import QdrantClient, models
        with self.lock:
            client = self.client or QdrantClient(path=str(self.directory / "qdrant"))
            try:
                for collection in client.get_collections().collections:
                    for start in range(0, len(identifiers), 200):
                        client.delete(collection.name, points_selector=models.FilterSelector(filter=models.Filter(must=[
                            models.FieldCondition(key="upload_id", match=models.MatchAny(any=identifiers[start:start + 200]))])), wait=True)
            finally:
                if self.client is None:
                    client.close()

    def close(self):
        with self.lock:
            if self.client is not None:
                self.client.close()
                self.client = None
