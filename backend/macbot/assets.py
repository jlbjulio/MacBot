import gc
import threading

MODELS = {
    "embedding": ("google/embeddinggemma-2", "914f7f89142e33e77833254d9c9b90c3cef7303b"),
    "image": ("segmind/tiny-sd", "cad0bd7495fa6c4bcca01b19a723dc91627fe84f"),
    "voice": ("rhasspy/piper-voices", "c10ece1aade47bb51c153c893d14e5bf8e5b7117"),
    "verifier": ("cross-encoder/nli-deberta-v3-small", "fa2804872c3b4bd748f38c0185cc85775361e735"),
    "persona": ("Qwen/Qwen3-0.6B", "c1899de289a04d12100db370d81485cdf75e47ca"),
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

    def load(self, media=False):
        with self.lock:
            if self.model is not None and self.encoders == media:
                return
            self.release()
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(4)
            path = prepare_asset(self.directory, "embedding")
            options = {} if media else {"vision_config": None, "audio_config": None}
            self.model = SentenceTransformer(str(path), device="cpu", config_kwargs=options,
                                            model_kwargs={"dtype": torch.float32}, local_files_only=True)
            self.encoders = media

    def encode(self, values, query=False, media=False):
        with self.lock:
            self.load(media=media)
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
