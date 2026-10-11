import re
import threading
from typing import Any
from uuid import NAMESPACE_URL, uuid5

class RetrievalIndex:
    def __init__(self, directory, model_directory=None, engine="gemma2"):
        if engine != "gemma2":
            raise ValueError("MacBot uses its prepared EmbeddingGemma 2 index.")
        self.directory = directory
        self.model_directory = model_directory or directory / "models" / "embeddings"
        self.lock = threading.RLock()
        self.client = None
        self.gemma: Any = None
        self.tokenizer = None
        self.engine = engine
        self.collection = "embeddinggemma2_914f7f89_768_v1"

    def load(self):
        if self.client is not None:
            return
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

    def chunks(self, text):
        assert self.tokenizer is not None
        markers = list(re.finditer(r"(?m)^(Page|Página|Slide|Audio segment) (\d+)\n|^Sheet: ([^\n]+)\n", text))
        boundaries = sorted({0, len(text), *(m.start() for m in markers)})
        for left_boundary, right_boundary in zip(boundaries, boundaries[1:]):
            section = text[left_boundary:right_boundary]
            marker = next((m for m in reversed(markers) if m.start() <= left_boundary), None)
            encoded = self.tokenizer.encode(section, add_special_tokens=False)
            for start in range(0, len(encoded.ids), 272):
                offsets = encoded.offsets[start:start + 320]
                if not offsets:
                    break
                left, right = left_boundary + offsets[0][0], left_boundary + offsets[-1][1]
                kind = marker[1] if marker else None
                number = int(marker[2]) if marker and marker[2] else None
                yield {"text": text[left:right], "offset": left,
                       "page": number if kind in ("Page", "Página") else None,
                       "slide": number if kind == "Slide" else None,
                       "timestamp": number if kind == "Audio segment" else None,
                       "sheet": marker[3] if marker else None}

    def index(self, uid, name, text):
        from qdrant_client import models

        with self.lock:
            self.load()
            assert self.client is not None
            chunks = list(self.chunks(text))
            for start in range(0, len(chunks), 16):
                batch = chunks[start : start + 16]
                assert self.gemma is not None
                vectors = self.gemma.encode([chunk["text"] for chunk in batch])
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
            assert self.gemma is not None
            vector = self.gemma.encode([query], query=True)[0].tolist()
            hits = self.client.query_points(
                self.collection,
                query=vector,
                limit=limit,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="upload_id", match=models.MatchAny(any=uploads))]
                ),
            ).points
            return [{**(hit.payload or {}), "score": hit.score} for hit in hits]

    def index_media(self, uid, name, kind, path, text, segments=()):
        from qdrant_client import models
        from PIL import Image
        from .media import audio_waveform, video_frames
        with self.lock:
            self.load()
            assert self.client is not None
            assert self.gemma is not None
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
                transcript = " ".join(s["text"] for s in segments
                                      if s["start"] < (index + 1) * 30 and s["end"] > index * 30)
                excerpt = transcript if kind == "audio" and segments else text
                self.client.upsert(self.collection, points=[models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"{uid}:media:{index}")), vector=vector.tolist(),
                    payload={"upload_id": uid, "name": name, "kind": kind, "chunk": index,
                             "text": excerpt[:6000] or f"{kind.title()} attachment: {name}", "offset": 0,
                             "timestamp": index * 30 if kind == "audio" else None})])
            return len(items)

    def release_models(self):
        if self.gemma is not None:
            self.gemma.release()

    def copy_upload(self, source, uid, name):
        from qdrant_client import models
        with self.lock:
            self.load()
            assert self.client is not None
            offset, count = None, 0
            while True:
                points, offset = self.client.scroll(self.collection, limit=64, offset=offset, with_vectors=True,
                    scroll_filter=models.Filter(must=[models.FieldCondition(key="upload_id", match=models.MatchValue(value=source))]))
                copies = []
                for point in points:
                    payload = {**(point.payload or {}), "upload_id": uid, "name": name}
                    prefix = "media:" if payload.get("kind") in ("image", "audio", "video") else ""
                    copies.append(models.PointStruct(id=str(uuid5(NAMESPACE_URL, f"{uid}:{prefix}{payload['chunk']}")),
                                                    vector=point.vector, payload=payload))
                if copies:
                    self.client.upsert(self.collection, points=copies)
                count += len(copies)
                if offset is None:
                    return count

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
