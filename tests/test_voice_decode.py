"""Voice decode: Telegram OGG/Opus → 16 kHz mono PCM with only the bot's runtime deps."""
from __future__ import annotations

import io
import math
import sys

import av
import pytest

from family_bot import voice_input as vx


def make_ogg_opus(seconds: float = 1.0) -> bytes:
    """A short 440 Hz tone encoded the way Telegram sends voice notes."""
    rate = 48000
    out = io.BytesIO()
    container = av.open(out, "w", format="ogg")
    stream = container.add_stream("libopus", rate=rate)
    stream.layout = "mono"
    total = int(rate * seconds)
    step = 960
    for start in range(0, total, step):
        samples = [int(8000 * math.sin(2 * math.pi * 440 * (start + i) / rate))
                   for i in range(step)]
        frame = av.AudioFrame(format="s16", layout="mono", samples=step)
        frame.rate = rate
        frame.pts = start
        frame.planes[0].update(b"".join(s.to_bytes(2, "little", signed=True) for s in samples))
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return out.getvalue()


def test_decode_works_without_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    # numpy is not a bot dependency (requirements.txt), so it is absent on Render.
    monkeypatch.setitem(sys.modules, "numpy", None)
    pcm = vx.decode_to_pcm16k_mono(make_ogg_opus(1.0))
    # 1 s at 16 kHz, 2 bytes per sample, give or take codec padding.
    assert abs(len(pcm) - 32000) <= 2000, len(pcm)
    assert len(pcm) % 2 == 0
