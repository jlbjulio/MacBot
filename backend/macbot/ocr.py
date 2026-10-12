import asyncio
import base64
import io

from .runtime import LlamaRuntime, MODEL_NAME


class DocumentOCR:
    """Reuse one native OCR model for the pages of a single attachment."""

    def __init__(self, directory):
        self.runtime = LlamaRuntime(directory, purpose="ocr")
        self.runner = asyncio.Runner()
        self.cache = {}

    def __enter__(self):
        from .assets import prepare_asset
        prepare_asset(self.runtime.directory, "ocr")
        return self

    def read(self, image):
        import hashlib
        if image.width * image.height > 25_000_000:
            raise ValueError("Images are limited to 25 megapixels.")
        image = image.convert("RGB")
        if all(low == high for low, high in image.getextrema()):
            return ""
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.cache:
            return self.cache[digest]
        async def recognize():
            messages = [{"role": "user", "content": "Convert this page to docling.",
                         "images": [base64.b64encode(data).decode("ascii")]}]
            return "".join([part async for part in self.runtime.stream(MODEL_NAME, messages)])
        text = self.runner.run(recognize()).strip()
        if len(text) > 100_000:
            raise ValueError("The scanned page exceeds the text limit.")
        if "<doctag>" not in text or "</doctag>" not in text:
            raise ValueError("The document reader returned incomplete content. Try a clearer scan or a smaller page.")
        from docling_core.types.doc import DoclingDocument
        from docling_core.types.doc.document import DocTagsDocument
        document = DoclingDocument.load_from_doctags(DocTagsDocument.from_doctags_and_image_pairs([text], [image]))
        text = document.export_to_markdown().replace("<!-- image -->", "").strip()
        self.cache[digest] = text
        return text

    def __exit__(self, *args):
        try:
            self.runner.run(self.runtime.unload())
        finally:
            self.runner.close()
