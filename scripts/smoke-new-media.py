import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from macbot.artifacts import draw_image, synthesize
from macbot.assets import GemmaEmbeddings
from macbot.media import audio_waveform, ocr_image


def main():
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    output = ROOT / "logs/media-smoke"
    output.mkdir(exist_ok=True)
    data = ROOT / "backend/data"
    metrics = {}
    start = time.perf_counter()
    synthesize("MacBot is ready. Your creative workspace runs on this device.", output / "voice.wav", data)
    metrics["tts_seconds"] = round(time.perf_counter() - start, 3)
    picture = Image.new("RGB", (512, 256), "white")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
    ImageDraw.Draw(picture).text((24, 75), "MACBOT PROJECT 42", fill="black", font=font)
    picture.save(output / "ocr.png")
    start = time.perf_counter()
    metrics["ocr"] = ocr_image(picture)
    metrics["ocr_seconds"] = round(time.perf_counter() - start, 3)
    embedding = GemmaEmbeddings(data)
    values = [
        ("image", {"image": picture}),
        ("audio", {"audio": {"array": audio_waveform(output / "voice.wav"), "sampling_rate": 16000}}),
        ("video", {"video": [picture, Image.new("RGB", (512, 256), "blue")]}),
    ]
    for name, value in values:
        start = time.perf_counter()
        vector = embedding.encode([value], media=True)
        metrics[name] = {"shape": list(vector.shape), "finite": bool(np.isfinite(vector).all()),
                         "norm": float(np.linalg.norm(vector[0])), "seconds": round(time.perf_counter() - start, 3)}
        print(json.dumps({name: metrics[name]}), flush=True)
    embedding.release()
    start = time.perf_counter()
    draw_image("A small orange cat resting beside a vinyl record, warm illustration, clean composition", output / "generated.png", data)
    metrics["image_generation_seconds"] = round(time.perf_counter() - start, 3)
    metrics["image_size"] = list(Image.open(output / "generated.png").size)
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics), flush=True)

if __name__ == "__main__":
    main()
