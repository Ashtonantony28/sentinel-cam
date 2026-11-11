"""Procedurally rendered "people" on noise backgrounds, with hard negatives.

A person is a head + torso + arms + legs stick-figure with clothing colours and a
pose jitter; boxes are the tight bounds of the drawn parts. Distractors that
must NOT be detected: cars (wide boxes with wheels), trees (blob on a trunk —
roughly person-shaped, the hard negative), cats (low ellipses), random blobs.
Deterministic per (seed, index), so train/val/test splits are just seed ranges.
"""
from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import Dataset

from .render import ellipse, finish, rect, smooth_noise, to_uint8


def _rand_color(rng):
    return rng.uniform(0.0, 1.0, 3).astype(np.float32)


def draw_person(img, rng, cx, top, h):
    """Draw a person of height h with head-top at ``top``; return tight box."""
    w_sh = h * rng.uniform(0.22, 0.30)       # shoulder width
    head_r = h * rng.uniform(0.065, 0.085)
    skin = np.array([rng.uniform(0.35, 0.95), rng.uniform(0.25, 0.75), rng.uniform(0.15, 0.6)], np.float32)
    shirt, pants = _rand_color(rng), _rand_color(rng)
    head_cy = top + head_r
    neck = head_cy + head_r
    torso_b = neck + h * 0.38
    lean = rng.uniform(-0.05, 0.05) * h
    xs = []
    # legs
    leg_w = w_sh * 0.38
    stride_ = rng.uniform(0, 0.12) * h
    for sgn in (-1, 1):
        lx = cx + lean * 0.3 + sgn * w_sh * 0.22 + sgn * stride_ * 0.5
        rect(img, lx - leg_w / 2, torso_b - 2, lx + leg_w / 2, top + h, pants)
        xs += [lx - leg_w / 2, lx + leg_w / 2]
    # torso
    rect(img, cx + lean * 0.5 - w_sh / 2, neck, cx + lean * 0.5 + w_sh / 2, torso_b, shirt)
    xs += [cx + lean * 0.5 - w_sh / 2, cx + lean * 0.5 + w_sh / 2]
    # arms
    arm_w = w_sh * 0.22
    for sgn in (-1, 1):
        ax = cx + lean * 0.5 + sgn * (w_sh / 2 + arm_w / 2)
        swing = rng.uniform(-0.12, 0.25) * h * sgn
        ellipse(img, ax + swing / 2, neck + h * 0.18, arm_w / 2, h * 0.19, shirt, angle=-swing / h)
        xs += [ax + swing / 2 - arm_w, ax + swing / 2 + arm_w]
    # head (+ hair)
    hx = cx + lean
    ellipse(img, hx, head_cy, head_r * 0.85, head_r, skin)
    if rng.random() < 0.7:
        ellipse(img, hx, head_cy - head_r * 0.5, head_r * 0.9, head_r * 0.55, _rand_color(rng) * 0.4)
    xs += [hx - head_r, hx + head_r]
    return [min(xs), top, max(xs), top + h]


def draw_distractor(img, rng, s):
    kind = rng.integers(0, 4)
    x, y = rng.uniform(0, s), rng.uniform(0, s)
    sz = rng.uniform(0.08, 0.45) * s
    if kind == 0:  # car
        c = _rand_color(rng)
        rect(img, x - sz, y - sz * 0.25, x + sz, y + sz * 0.2, c)
        rect(img, x - sz * 0.5, y - sz * 0.5, x + sz * 0.4, y - sz * 0.2, c * 0.8)
        for dx in (-0.6, 0.6):
            ellipse(img, x + dx * sz, y + sz * 0.22, sz * 0.18, sz * 0.18, (0.05, 0.05, 0.05))
    elif kind == 1:  # tree (person-like hard negative)
        rect(img, x - sz * 0.07, y, x + sz * 0.07, y + sz, (0.35, 0.22, 0.1))
        ellipse(img, x, y, sz * rng.uniform(0.25, 0.5), sz * rng.uniform(0.3, 0.6),
                (rng.uniform(0, 0.3), rng.uniform(0.3, 0.8), rng.uniform(0, 0.3)))
    elif kind == 2:  # cat / dog
        c = _rand_color(rng) * 0.7
        ellipse(img, x, y, sz * 0.35, sz * 0.15, c)
        ellipse(img, x + sz * 0.35, y - sz * 0.12, sz * 0.1, sz * 0.1, c)
    else:  # blob / sign
        if rng.random() < 0.5:
            ellipse(img, x, y, sz * rng.uniform(0.2, 0.6), sz * rng.uniform(0.2, 0.6), _rand_color(rng),
                    angle=rng.uniform(0, 3.14))
        else:
            rect(img, x - sz * 0.3, y - sz * 0.3, x + sz * 0.3, y + sz * 0.3, _rand_color(rng))


def render_person_scene(seed: int, size: int = 256, max_people: int = 4):
    """Returns (image float32 HxWx3 in [0,1], boxes float32 [K,4] xyxy pixels)."""
    rng = np.random.default_rng(seed)
    img = smooth_noise(rng, size, size, cells=int(rng.integers(3, 9)))
    if rng.random() < 0.5:  # ground plane
        gy = rng.uniform(0.4, 0.8) * size
        rect(img, 0, gy, size, size, _rand_color(rng) * 0.6 + 0.2, alpha=0.6)
    for _ in range(int(rng.integers(0, 5))):
        draw_distractor(img, rng, size)
    n = int(rng.choice([0, 1, 1, 1, 2, 2, 3, 4][: max_people + 4]))
    n = min(n, max_people)
    boxes = []
    # draw far (small) people first so near people occlude them
    hs = sorted(rng.uniform(0.15, 0.95, n) * size)
    for h in hs:
        cx = rng.uniform(0.1, 0.9) * size
        top = rng.uniform(-0.05 * h, size - 0.9 * h)
        b = draw_person(img, rng, cx, top, h)
        boxes.append(b)
    img = finish(img, rng)
    boxes = np.array(boxes, np.float32).reshape(-1, 4)
    boxes = np.clip(boxes, 0, size)
    keep = ((boxes[:, 2] - boxes[:, 0]) > 4) & ((boxes[:, 3] - boxes[:, 1]) > 8)
    return img, boxes[keep]


class SyntheticPersonDataset(Dataset):
    """Rendered once into memory as uint8 (fast epochs); random h-flip at train time."""

    def __init__(self, n: int, seed: int = 0, size: int = 256, train: bool = True):
        self.size, self.train = size, train
        self.images, self.boxes = [], []
        for i in range(n):
            im, b = render_person_scene(seed * 1_000_003 + i, size)
            self.images.append(to_uint8(im))
            self.boxes.append(b)
        self.rng = np.random.default_rng(seed + 17)

    def __len__(self):
        return len(self.images)

    def __getitem__(self, i):
        im = self.images[i].astype(np.float32) / 255.0
        b = self.boxes[i].copy()
        if self.train and self.rng.random() < 0.5:
            im = im[:, ::-1].copy()
            b[:, [0, 2]] = self.size - b[:, [2, 0]]
        x = torch.from_numpy(np.ascontiguousarray(im.transpose(2, 0, 1)))
        return x, {"boxes": torch.from_numpy(b), "kpts": torch.zeros(len(b), 0)}


def collate(batch):
    xs, ts = zip(*batch)
    return torch.stack(xs), list(ts)
