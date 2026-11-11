"""Tiny procedural rasteriser (numpy) shared by the synthetic datasets.

Shapes are drawn with a soft (1 px) edge so the images are not trivially
aliased. Everything works on float32 HxWx3 images in [0, 1].
"""
from __future__ import annotations

import numpy as np


def smooth_noise(rng: np.random.Generator, h: int, w: int, cells: int = 6) -> np.ndarray:
    """Low-frequency colour noise (bilinear upsampled random grid) + fine grain."""
    g = rng.random((cells + 1, cells + 1, 3)).astype(np.float32)
    ys = np.linspace(0, cells, h, dtype=np.float32)
    xs = np.linspace(0, cells, w, dtype=np.float32)
    y0 = np.minimum(ys.astype(int), cells - 1)
    x0 = np.minimum(xs.astype(int), cells - 1)
    fy = (ys - y0)[:, None, None]
    fx = (xs - x0)[None, :, None]
    a = g[y0][:, x0]
    b = g[y0][:, x0 + 1]
    c = g[y0 + 1][:, x0]
    d = g[y0 + 1][:, x0 + 1]
    img = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
    img = 0.25 + 0.5 * img
    img += rng.normal(0, 0.04, (h, w, 3)).astype(np.float32)
    return img


def _window(img_shape, cx, cy, rx, ry):
    h, w = img_shape[:2]
    r = max(rx, ry) + 2
    x0, x1 = int(max(0, np.floor(cx - r))), int(min(w, np.ceil(cx + r) + 1))
    y0, y1 = int(max(0, np.floor(cy - r))), int(min(h, np.ceil(cy + r) + 1))
    return x0, x1, y0, y1


def blend(img, mask, color, x0, y0):
    if mask.size == 0:
        return
    m = mask[..., None]
    sub = img[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]]
    sub *= 1 - m
    sub += m * np.asarray(color, np.float32)


def ellipse(img, cx, cy, rx, ry, color, angle=0.0, alpha=1.0):
    rx, ry = max(rx, 0.5), max(ry, 0.5)
    x0, x1, y0, y1 = _window(img.shape, cx, cy, rx, ry)
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dx, dy = xx + 0.5 - cx, yy + 0.5 - cy
    ca, sa = np.cos(angle), np.sin(angle)
    u, v = (dx * ca + dy * sa) / rx, (-dx * sa + dy * ca) / ry
    d = (np.sqrt(u * u + v * v) - 1.0) * min(rx, ry)
    mask = np.clip(0.5 - d, 0, 1) * alpha
    blend(img, mask, color, x0, y0)


def rect(img, x0f, y0f, x1f, y1f, color, alpha=1.0):
    h, w = img.shape[:2]
    x0, x1 = int(max(0, np.floor(x0f))), int(min(w, np.ceil(x1f)))
    y0, y1 = int(max(0, np.floor(y0f))), int(min(h, np.ceil(y1f)))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32) + 0.5
    mx = np.clip(np.minimum(xx - x0f, x1f - xx) + 0.5, 0, 1)
    my = np.clip(np.minimum(yy - y0f, y1f - yy) + 0.5, 0, 1)
    blend(img, mx * my * alpha, color, x0, y0)


def finish(img: np.ndarray, rng: np.random.Generator, ir_prob: float = 0.2) -> np.ndarray:
    """Global photometric augmentation; with prob ``ir_prob`` simulate a monochrome IR frame."""
    gain = rng.uniform(0.6, 1.3)
    bias = rng.uniform(-0.1, 0.1)
    img = img * gain + bias
    if rng.random() < ir_prob:
        img = np.repeat(img.mean(axis=2, keepdims=True), 3, axis=2)
    img = img + rng.normal(0, rng.uniform(0.0, 0.05), img.shape).astype(np.float32)
    return np.clip(img, 0, 1).astype(np.float32)


def to_uint8(img: np.ndarray) -> np.ndarray:
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
