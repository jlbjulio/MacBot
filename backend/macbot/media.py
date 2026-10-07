import io
from typing import Any, cast

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
_ocr = None


def ocr_image(image):
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR
        _ocr = RapidOCR()
    result = _ocr(image)
    texts, scores = getattr(result, "txts", None), getattr(result, "scores", None)
    if texts is None or scores is None:
        return ""
    return "\n".join(text for text, score in zip(texts, scores) if score >= 0.5)


def audio_waveform(path, maximum=600):
    import av
    import numpy as np

    parts, samples = [], 0
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        if container.duration and container.duration / av.time_base > maximum:
            raise ValueError(f"Audio is limited to {maximum // 60} minutes.")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                data = converted.to_ndarray().reshape(-1)
                samples += len(data)
                if samples > maximum * 16000:
                    raise ValueError("The audio exceeds the duration limit.")
                parts.append(data)
        for converted in resampler.resample(None):
            parts.append(converted.to_ndarray().reshape(-1))
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, dtype=np.float32)


def video_frames(path, maximum=300, count=12):
    import av

    with av.open(str(path)) as container:
        if not container.streams.video:
            return [], 0
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None and stream.time_base is not None else (container.duration or 0) / av.time_base
        if duration > maximum:
            raise ValueError("Video is limited to five minutes.")
        frames, next_time = [], 0.0
        interval = max(duration / count, 0.5)
        for frame in container.decode(video=0):
            timestamp = float(frame.time or 0)
            if timestamp > maximum:
                raise ValueError("The video exceeds the duration limit.")
            if timestamp < next_time:
                continue
            image = frame.to_image().convert("RGB")
            image.thumbnail((512, 512))
            frames.append((timestamp, image))
            next_time = timestamp + interval
            if len(frames) >= count:
                break
    return frames, duration


def scan_pdf(content, indices=None):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(content)
    try:
        selected = list(range(len(document))) if indices is None else indices
        if len(selected) > 30:
            raise ValueError("Scanned PDFs are limited to 30 pages.")
        pages = []
        for index in selected:
            page = document[index]
            # PDFium accepts fractional scales; its bundled type stub declares an integer.
            bitmap = cast(Any, page).render(scale=1.5)
            try:
                text = ocr_image(bitmap.to_pil())
                pages.append(f"Page {index + 1}\n{text}")
            finally:
                bitmap.close()
                page.close()
        if indices is None and not any(page.split("\n", 1)[1].strip() for page in pages):
            raise ValueError("No readable text was found in this scan.")
        return "\n\n".join(pages)
    finally:
        document.close()


def normalized_image(content):
    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError("Images are limited to 25 megapixels.")
        return image.convert("RGB").copy()
