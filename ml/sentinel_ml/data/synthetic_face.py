"""Procedural synthetic faces with stable identities.

``FaceIdentity`` is a set of shape/colour parameters drawn from a seed; rendering
the same identity with different nuisance (pose jitter, expression, lighting,
noise, IR, occlusion) gives intra-class variation, so the embedder has a real
(if toy) verification problem. ``render_face_into`` draws into any canvas at any
scale/angle and returns the 5 landmarks, so the face detector uses the same
renderer.

Canonical frame = 112x112 aligned crop, landmarks near the ArcFace template.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch.utils.data import Dataset

from .render import ellipse, finish, rect, smooth_noise, to_uint8

TEMPLATE_112 = np.array([[38.3, 51.7], [73.5, 51.5], [56.0, 71.7], [41.5, 92.4], [70.7, 92.2]], np.float32)


@dataclass
class FaceIdentity:
    skin: np.ndarray
    face_rx: float
    face_ry: float
    hair: np.ndarray
    hair_style: int
    hairline: float
    eye_dx: float
    eye_r: float
    iris: np.ndarray
    brow_t: float
    brow_tilt: float
    nose_len: float
    nose_w: float
    mouth_w: float
    lip: np.ndarray
    beard: bool
    glasses: bool
    mole: tuple

    @staticmethod
    def from_seed(seed: int) -> "FaceIdentity":
        r = np.random.default_rng(10_007 * seed + 3)
        tone = r.uniform(0.25, 1.0)
        return FaceIdentity(
            skin=np.array([tone, tone * r.uniform(0.65, 0.85), tone * r.uniform(0.45, 0.7)], np.float32),
            face_rx=r.uniform(30, 40), face_ry=r.uniform(40, 50),
            hair=r.uniform(0, 1, 3).astype(np.float32) * r.uniform(0.2, 1.0),
            hair_style=int(r.integers(0, 4)), hairline=r.uniform(14, 30),
            eye_dx=r.uniform(14, 21), eye_r=r.uniform(3.0, 6.0),
            iris=r.uniform(0, 0.7, 3).astype(np.float32),
            brow_t=r.uniform(1.0, 3.5), brow_tilt=r.uniform(-0.35, 0.35),
            nose_len=r.uniform(10, 22), nose_w=r.uniform(3, 9),
            mouth_w=r.uniform(9, 19), lip=np.array([r.uniform(0.4, 0.9), r.uniform(0.1, 0.4), r.uniform(0.1, 0.4)], np.float32),
            beard=bool(r.random() < 0.25), glasses=bool(r.random() < 0.25),
            mole=(r.uniform(-20, 20), r.uniform(-10, 25)) if r.random() < 0.5 else None,
        )


def render_face_into(img, ident: FaceIdentity, rng, cx, cy, scale, angle=0.0, frontal=True):
    """Draw ``ident`` centred at (cx, cy) with canonical-unit ``scale``.

    Returns (landmarks [5,2] in image px, box [4] xyxy of the face oval).
    """
    ca, sa = np.cos(angle), np.sin(angle)
    ox, oy = 56.0, 60.0  # canonical face centre

    def T(x, y):
        dx, dy = (x - ox) * scale, (y - oy) * scale
        return cx + dx * ca - dy * sa, cy + dx * sa + dy * ca

    def E(x, y, rx, ry, color, a=0.0, alpha=1.0):
        px, py = T(x, y)
        ellipse(img, px, py, rx * scale, ry * scale, color, angle=angle + a, alpha=alpha)

    yaw = rng.uniform(-4, 4)  # small horizontal shift of inner features = fake yaw
    s = ident
    # hair behind (long styles)
    if s.hair_style == 1:
        E(56, 62, s.face_rx + 8, s.face_ry + 6, s.hair)
    E(56, 62, s.face_rx + 3, s.face_ry * 0.25, s.skin * 0.9, alpha=1.0)  # ears
    E(56, 60, s.face_rx, s.face_ry, s.skin)
    if not frontal:  # back of head: only hair visible (hard negative for the face detector)
        E(56, 58, s.face_rx + 1, s.face_ry + 1, s.hair)
        return None, None
    # hair on top
    if s.hair_style in (0, 1, 3):
        E(56, 60 - s.face_ry + s.hairline * 0.5, s.face_rx * 1.02, s.hairline, s.hair)
    if s.beard:
        E(56 + yaw, 96, s.face_rx * 0.75, 14, s.hair * 0.8, alpha=0.9)
    blink = rng.random() < 0.08
    lm = []
    for sgn in (-1, 1):
        ex, ey = 56 + yaw + sgn * s.eye_dx, 51.6
        E(ex, ey, s.eye_r * 1.6, s.eye_r * (0.25 if blink else 1.0), (0.95, 0.95, 0.95))
        if not blink:
            E(ex + rng.uniform(-1, 1), ey, s.eye_r * 0.8, s.eye_r * 0.8, s.iris)
        E(ex, ey - s.eye_r - 5, s.eye_r * 2.0, s.brow_t, s.hair * 0.7, a=sgn * s.brow_tilt)
        if s.glasses:
            E(ex, ey, s.eye_r * 2.6, s.eye_r * 2.2, (0.1, 0.1, 0.1), alpha=0.35)
        lm.append(T(ex, ey))
    nx, ny = 56 + yaw * 1.3, 51.6 + s.nose_len + 6
    E(nx, ny - s.nose_len / 2, s.nose_w * 0.4, s.nose_len / 2, s.skin * 0.8)
    E(nx, ny, s.nose_w, s.nose_w * 0.6, s.skin * 0.7)
    lm.append(T(nx, ny))
    my = 92.3 + rng.uniform(-1.5, 1.5)
    smile = rng.uniform(1.5, 5.0)
    E(56 + yaw, my, s.mouth_w, smile, s.lip)
    lm.append(T(56 + yaw - s.mouth_w, my))
    lm.append(T(56 + yaw + s.mouth_w, my))
    if s.mole is not None:
        E(56 + s.mole[0], 60 + s.mole[1], 1.5, 1.5, (0.2, 0.12, 0.08))
    # face box: the oval's axis-aligned bounds (rotation-aware)
    rx, ry = s.face_rx * scale, s.face_ry * scale
    hw = np.sqrt((rx * ca) ** 2 + (ry * sa) ** 2)
    hh = np.sqrt((rx * sa) ** 2 + (ry * ca) ** 2)
    fcx, fcy = T(56, 60)
    box = np.array([fcx - hw, fcy - hh, fcx + hw, fcy + hh], np.float32)
    return np.array(lm, np.float32), box


def render_aligned_face(ident_seed: int, sample_seed: int, size: int = 112):
    """Aligned 112x112 crop of identity ``ident_seed`` with nuisance from ``sample_seed``."""
    rng = np.random.default_rng(sample_seed)
    img = smooth_noise(rng, size, size, cells=4)
    ident = FaceIdentity.from_seed(ident_seed)
    k = size / 112.0
    render_face_into(img, ident, rng, 56 * k + rng.normal(0, 2.0), 60 * k + rng.normal(0, 2.0),
                     k * rng.uniform(0.92, 1.08), angle=rng.normal(0, 0.08))
    # lighting gradient (side light)
    if rng.random() < 0.5:
        g = np.linspace(rng.uniform(0.6, 1.0), rng.uniform(0.6, 1.0), size, dtype=np.float32)
        img *= g[None, :, None]
    if rng.random() < 0.15:  # partial occlusion (hand / scarf)
        y = rng.uniform(70, 100) * k
        rect(img, 0, y, size, size, rng.uniform(0, 1, 3).astype(np.float32))
    return finish(img, rng, ir_prob=0.15)


def render_face_scene(seed: int, size: int = 160, n_identities: int = 10_000):
    """A "person crop" with 0-2 faces. Returns (img, boxes [K,4], kpts [K,5,2])."""
    rng = np.random.default_rng(seed)
    img = smooth_noise(rng, size, size, cells=int(rng.integers(3, 8)))
    # body below the head
    rect(img, rng.uniform(0.1, 0.3) * size, rng.uniform(0.55, 0.8) * size, rng.uniform(0.7, 0.9) * size, size,
         rng.uniform(0, 1, 3).astype(np.float32))
    for _ in range(int(rng.integers(0, 3))):  # blobs
        ellipse(img, rng.uniform(0, size), rng.uniform(0, size), rng.uniform(4, 30), rng.uniform(4, 30),
                rng.uniform(0, 1, 3).astype(np.float32))
    n = int(rng.choice([0, 1, 1, 1, 1, 2]))
    boxes, kpts = [], []
    for _ in range(n):
        sc = rng.uniform(0.15, 1.0)  # face height ~ 90 * sc px
        cx, cy = rng.uniform(0.2, 0.8) * size, rng.uniform(0.2, 0.7) * size
        ident = FaceIdentity.from_seed(int(rng.integers(0, n_identities)))
        frontal = rng.random() > 0.12
        lm, box = render_face_into(img, ident, rng, cx, cy, sc, angle=rng.normal(0, 0.12), frontal=frontal)
        if lm is not None:
            boxes.append(box)
            kpts.append(lm)
    img = finish(img, rng)
    boxes = np.array(boxes, np.float32).reshape(-1, 4)
    kpts = np.array(kpts, np.float32).reshape(-1, 5, 2)
    vis = (boxes[:, 0] > -0.15 * (boxes[:, 2] - boxes[:, 0])) & (boxes[:, 2] < size * 1.0 + 0.15 * (boxes[:, 2] - boxes[:, 0])) \
        & (boxes[:, 1] > -0.15 * (boxes[:, 3] - boxes[:, 1])) & (boxes[:, 3] < size + 0.15 * (boxes[:, 3] - boxes[:, 1]))
    return img, np.clip(boxes[vis], 0, size), kpts[vis]


class SyntheticFaceDetDataset(Dataset):
    def __init__(self, n: int, seed: int = 0, size: int = 160, train: bool = True):
        self.size, self.train = size, train
        self.images, self.boxes, self.kpts = [], [], []
        for i in range(n):
            im, b, k = render_face_scene(seed * 1_000_003 + i, size)
            self.images.append(to_uint8(im))
            self.boxes.append(b)
            self.kpts.append(k)
        self.rng = np.random.default_rng(seed + 29)

    def __len__(self):
        return len(self.images)

    def __getitem__(self, i):
        im = self.images[i].astype(np.float32) / 255.0
        b, k = self.boxes[i].copy(), self.kpts[i].copy()
        if self.train and self.rng.random() < 0.5:
            im = im[:, ::-1].copy()
            b[:, [0, 2]] = self.size - b[:, [2, 0]]
            k[:, :, 0] = self.size - k[:, :, 0]
            k = k[:, [1, 0, 2, 4, 3]]  # swap left/right eye and mouth corners
        x = torch.from_numpy(np.ascontiguousarray(im.transpose(2, 0, 1)))
        return x, {"boxes": torch.from_numpy(b), "kpts": torch.from_numpy(np.ascontiguousarray(k).reshape(len(k), 10))}


class SyntheticFaceIdDataset(Dataset):
    """Aligned crops: ``n_ids`` identities x ``per_id`` samples. Identity seeds start at
    ``id_offset`` so train / test identities are disjoint."""

    def __init__(self, n_ids: int, per_id: int, id_offset: int = 0, seed: int = 0, size: int = 112):
        self.labels = np.repeat(np.arange(n_ids), per_id)
        self.images = np.stack([
            to_uint8(render_aligned_face(id_offset + int(lbl), seed * 1_000_003 + i, size))
            for i, lbl in enumerate(self.labels)])

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, i):
        x = torch.from_numpy(self.images[i].transpose(2, 0, 1).astype(np.float32) / 255.0)
        return x, int(self.labels[i])
