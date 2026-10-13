import gc
import threading

MODELS = {
    "embedding": ("google/embeddinggemma-2", "914f7f89142e33e77833254d9c9b90c3cef7303b"),
    "image": ("unsloth/ERNIE-Image-Turbo-GGUF", "a38270280633a5d318a9f51c18ef95eefb07c423"),
    "ocr": ("PaddlePaddle/PaddleOCR-VL-1.6-GGUF", "511b09642bb324401f15f97cc23bc67e8f0a291d"),
    "voice": ("rhasspy/piper-voices", "c10ece1aade47bb51c153c893d14e5bf8e5b7117"),
    "verifier": ("cross-encoder/nli-deberta-v3-small", "fa2804872c3b4bd748f38c0185cc85775361e735"),
    "whisper": ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
}
VOICE_PREFIX = "en/en_US/ljspeech/medium/"
VOICE_FILE = "en_US-ljspeech-medium.onnx"


def prepare_asset(directory, name):
    from .setup import download_asset
    return download_asset(directory, name)


class GemmaEmbeddings:
    dimension = 768
    collection = "embeddinggemma2_914f7f89_768_v1"

    def __init__(self, directory):
        self.directory = directory
        self.model = None
        self.encoders = None
        self.lock = threading.RLock()

    def load(self, media=False, modality="text"):
        with self.lock:
            if self.model is not None and self.encoders == modality:
                return
            self.release()
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(4)
            path = prepare_asset(self.directory, "embedding")
            options = {"vision_config": None, "audio_config": None} if not media else (
                {"vision_config": None} if modality == "audio" else {"audio_config": None})
            self.model = SentenceTransformer(str(path), device="cpu", config_kwargs=options,
                                            model_kwargs={"dtype": torch.float32}, local_files_only=True)
            self.encoders = modality

    def encode(self, values, query=False, media=False):
        with self.lock:
            modality = "audio" if media and any(isinstance(v, dict) and "audio" in v for v in values) else "vision" if media else "text"
            self.load(media=media, modality=modality)
            assert self.model is not None
            options = {"normalize_embeddings": True, "batch_size": 1, "show_progress_bar": False}
            if not media:
                options["prompt_name"] = "SearchQuery" if query else "Document"
            elif any(isinstance(value, dict) and "video" in value for value in values):
                options["processing_kwargs"] = {"video": {"do_sample_frames": False}}
            return self.model.encode(values, **options)

    def release(self):
        with self.lock:
            self.model = None
            self.encoders = None
            gc.collect()
