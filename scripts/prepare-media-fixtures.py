from pathlib import Path

import av
import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[1]
output = root / "logs/media-smoke"
output.mkdir(exist_ok=True)
with av.open(str(output / "sample.mp4"), mode="w") as container:
    stream = container.add_stream("libx264", rate=1)
    stream.width, stream.height, stream.pix_fmt = 512, 256, "yuv420p"
    for colour in ["blue", "green", "red"]:
        frame = av.VideoFrame.from_ndarray(np.asarray(Image.new("RGB", (512, 256), colour)), format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
print("Video fixture ready.")
