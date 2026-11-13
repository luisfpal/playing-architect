"""Exposure gate for build/r5_shots/room_*.png: mean luminance in 0.25..0.75 and at most 2 % clipped pixels (>= 0.98).
Usage: uv run --no-project --python 3.12 --with pillow --with numpy python tools/luminance.py [glob]  Exit 1 on any failure."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[1] / "build" / "r5_shots"
bad = 0
for f in sorted(root.glob(sys.argv[1] if len(sys.argv) > 1 else "room_*.png")):
    a = np.asarray(Image.open(f).convert("L"), dtype=float)[60:540, 260:] / 255  # skip the HUD corners
    mean, clip = a.mean(), (a >= 0.98).mean()
    ok = 0.25 <= mean <= 0.75 and clip <= 0.02
    bad += not ok
    print(f"[{'ok' if ok else 'FAIL'}] {f.name} mean {mean:.2f} clipped {clip * 100:.1f} %")
sys.exit(1 if bad else 0)
