"""Hooks for real datasets. Nothing is downloaded: point the CLI flags at local copies.

* COCO-person:  ``--coco-root DIR`` with ``DIR/annotations/instances_{split}2017.json``
  and ``DIR/{split}2017/*.jpg``. Category 1 (person), ``iscrowd == 0`` only.
* CrowdHuman:   ``--crowdhuman-root DIR`` with ``DIR/annotation_{split}.odgt`` and
  ``DIR/Images/<ID>.jpg``. Uses full-body boxes (``fbox``) of ``tag == "person"``,
  skipping ``extra.ignore == 1``.
* WIDER FACE:   ``--wider-root DIR`` with ``DIR/wider_face_split/wider_face_{split}_bbx_gt.txt``
  and ``DIR/WIDER_{split}/images/...``. Optional 5-point landmarks from the
  RetinaFace ``label.txt`` format via ``--wider-landmarks FILE``.
* Face ID:      ``--faceid-root DIR`` with ``DIR/<identity>/<img>.jpg`` aligned 112x112 crops
  (e.g. a CASIA-WebFace / MS1M-cleaned export; check each dataset's licence).

Images are letterboxed to the model input (aspect preserved, grey pad), boxes
mapped accordingly. All loaders return the same ``(tensor CHW in [0,1], target)``
contract as the synthetic datasets.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np
import torch
from torch.utils.data import Dataset


def letterbox(img: np.ndarray, size: int):
    """img HxWx3 uint8 → (size x size float32, scale, (pad_x, pad_y))."""
    from PIL import Image

    h, w = img.shape[:2]
    s = size / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    im = np.asarray(Image.fromarray(img).resize((nw, nh), Image.BILINEAR), np.float32) / 255.0
    out = np.full((size, size, 3), 0.5, np.float32)
    px, py = (size - nw) // 2, (size - nh) // 2
    out[py:py + nh, px:px + nw] = im
    return out, s, (px, py)


def _load_rgb(path):
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


class BoxDataset(Dataset):
    """Generic list of (image_path, boxes xyxy px [G,4], kpts [G,K,2] or None)."""

    def __init__(self, items, size: int, n_kpt: int = 0, train: bool = True, min_size: float = 2.0):
        self.items, self.size, self.n_kpt, self.train, self.min_size = items, size, n_kpt, train, min_size
        self.rng = np.random.default_rng(0)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, boxes, kpts = self.items[i]
        img, s, (px, py) = letterbox(_load_rgb(path), self.size)
        b = np.asarray(boxes, np.float32).reshape(-1, 4) * s + [px, py, px, py]
        k = (np.zeros((len(b), self.n_kpt, 2), np.float32) if kpts is None
             else np.asarray(kpts, np.float32).reshape(-1, self.n_kpt, 2) * s + [px, py])
        keep = np.minimum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) >= self.min_size
        b, k = b[keep], k[keep]
        if self.train and self.rng.random() < 0.5:
            img = img[:, ::-1].copy()
            b[:, [0, 2]] = self.size - b[:, [2, 0]]
            if self.n_kpt:
                k[:, :, 0] = self.size - k[:, :, 0]
                if self.n_kpt == 5:
                    k = k[:, [1, 0, 2, 4, 3]]
        x = torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1)))
        return x, {"boxes": torch.from_numpy(b), "kpts": torch.from_numpy(np.ascontiguousarray(k).reshape(len(k), 2 * self.n_kpt))}


def coco_person_items(root: str, split: str = "train", limit: int | None = None):
    ann = os.path.join(root, "annotations", f"instances_{split}2017.json")
    with open(ann) as f:
        d = json.load(f)
    files = {im["id"]: im["file_name"] for im in d["images"]}
    per = defaultdict(list)
    for a in d["annotations"]:
        if a.get("category_id") == 1 and not a.get("iscrowd", 0):
            x, y, w, h = a["bbox"]
            per[a["image_id"]].append([x, y, x + w, y + h])
    # keep person-free images too (negatives), up to the same count as positives
    ids = sorted(files)
    pos = [i for i in ids if i in per]
    neg = [i for i in ids if i not in per][: len(pos)]
    items = [(os.path.join(root, f"{split}2017", files[i]), per.get(i, []), None) for i in sorted(pos + neg)]
    return items[:limit] if limit else items


def crowdhuman_items(root: str, split: str = "train", limit: int | None = None):
    items = []
    with open(os.path.join(root, f"annotation_{split}.odgt")) as f:
        for line in f:
            r = json.loads(line)
            boxes = []
            for g in r.get("gtboxes", []):
                if g.get("tag") != "person" or g.get("extra", {}).get("ignore", 0) == 1:
                    continue
                x, y, w, h = g["fbox"]
                boxes.append([x, y, x + w, y + h])
            items.append((os.path.join(root, "Images", r["ID"] + ".jpg"), boxes, None))
            if limit and len(items) >= limit:
                break
    return items


def wider_face_items(root: str, split: str = "train", landmarks_file: str | None = None, limit: int | None = None):
    gt = os.path.join(root, "wider_face_split", f"wider_face_{split}_bbx_gt.txt")
    lm = _retinaface_landmarks(landmarks_file) if landmarks_file else {}
    items = []
    with open(gt) as f:
        lines = [ln.strip() for ln in f]
    i = 0
    while i < len(lines):
        name = lines[i]
        n = int(lines[i + 1])
        rows = lines[i + 2: i + 2 + max(n, 1)]
        i += 2 + max(n, 1)
        boxes = []
        for r in rows[:n]:
            v = [int(x) for x in r.split()[:10]]
            x, y, w, h, invalid = v[0], v[1], v[2], v[3], v[7]
            if invalid or w <= 0 or h <= 0:
                continue
            boxes.append([x, y, x + w, y + h])
        k = lm.get(name)
        kp = k if (k is not None and len(k) == len(boxes)) else None
        items.append((os.path.join(root, f"WIDER_{split}", "images", name), boxes, kp))
        if limit and len(items) >= limit:
            break
    return items


def _retinaface_landmarks(path):
    """RetinaFace label.txt: '# name' then rows 'x y w h l0x l0y v l1x l1y v ...'."""
    out, cur = {}, None
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("#"):
                cur = ln[1:].strip()
                out[cur] = []
            elif ln and cur is not None:
                v = [float(x) for x in ln.split()]
                if v[2] <= 0 or v[3] <= 0:
                    continue
                pts = [[v[4 + 3 * j], v[5 + 3 * j]] for j in range(5)] if len(v) >= 19 else [[0, 0]] * 5
                out[cur].append(pts)
    return out


class FaceIdFolder(Dataset):
    """``root/<identity>/*.{jpg,png}`` aligned crops → (tensor, label)."""

    def __init__(self, root: str, size: int = 112, limit_ids: int | None = None):
        ids = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))[:limit_ids]
        self.items = [(os.path.join(root, d, f), li) for li, d in enumerate(ids)
                      for f in sorted(os.listdir(os.path.join(root, d)))
                      if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        self.labels = np.array([lbl for _, lbl in self.items])
        self.size = size

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        p, lbl = self.items[i]
        img, _, _ = letterbox(_load_rgb(p), self.size)
        return torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1))), lbl
