import asyncio
import base64
import io

from .runtime import LlamaRuntime, MODEL_NAME

# PaddleOCR-VL's vision encoder resizes larger inputs to this budget; sending more only costs time.
MAX_PIXELS = 1_003_520


class DocumentOCR:
    """Reuse one native OCR model for the pages of a single attachment."""

    def __init__(self, directory):
        self.runtime = LlamaRuntime(directory, purpose="ocr")
        self.runner = None
        self.cache = {}

    def __enter__(self):
        from .assets import prepare_asset
        prepare_asset(self.runtime.directory, "ocr")
        # Created only after preparation succeeds, so a failed download never leaks an event loop.
        self.runner = asyncio.Runner()
        return self

    def read(self, image):
        import hashlib
        from PIL import Image
        if image.width * image.height > 25_000_000:
            raise ValueError("Images are limited to 25 megapixels.")
        image = image.convert("RGB")
        if all(low == high for low, high in image.getextrema()):
            return ""
        if image.width * image.height > MAX_PIXELS:
            scale = (MAX_PIXELS / (image.width * image.height)) ** 0.5
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                                 Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.cache:
            return self.cache[digest]
        async def recognize():
            messages = [{"role": "user", "content": "OCR:", "images": [base64.b64encode(data).decode("ascii")]}]
            return "".join([part async for part in self.runtime.stream(MODEL_NAME, messages)])
        assert self.runner is not None, "Use DocumentOCR as a context manager."
        text = self.runner.run(recognize()).strip()
        if len(text) > 100_000:
            raise ValueError("The scanned page exceeds the text limit.")
        self.cache[digest] = text
        return text

    def __exit__(self, *args):
        if self.runner is None:
            return
        try:
            self.runner.run(self.runtime.unload())
        finally:
            self.runner.close()
            self.runner = None
