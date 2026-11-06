"""Dataset selection shared by the CLIs: synthetic by default, real datasets via flags."""
from __future__ import annotations

import argparse

from torch.utils.data import ConcatDataset, Subset

from .data import real
from .data.synthetic_face import SyntheticFaceDetDataset, SyntheticFaceIdDataset
from .data.synthetic_person import SyntheticPersonDataset


def add_data_args(ap: argparse.ArgumentParser, task: str):
    g = ap.add_argument_group("data (synthetic unless a real dataset root is given)")
    g.add_argument("--n-train", type=int, default=2000, help="synthetic training images (or identities x per-id)")
    g.add_argument("--n-val", type=int, default=300)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--limit", type=int, default=None, help="cap real-dataset items (debug)")
    if task == "person":
        g.add_argument("--coco-root", help="COCO 2017 root (annotations/, train2017/, val2017/)")
        g.add_argument("--crowdhuman-root", help="CrowdHuman root (annotation_*.odgt, Images/)")
    elif task == "face":
        g.add_argument("--wider-root", help="WIDER FACE root (wider_face_split/, WIDER_train/ ...)")
        g.add_argument("--wider-landmarks", help="RetinaFace-format label.txt with 5-point landmarks")
    else:
        g.add_argument("--faceid-root", help="aligned 112x112 face folders: ROOT/<identity>/*.jpg")
        g.add_argument("--faceid-val-root", help="held-out identities for TAR@FAR (same layout)")
        g.add_argument("--per-id", type=int, default=16, help="synthetic samples per identity")


def make_datasets(task: str, a, size: int):
    """Returns (train, val). For 'emb' val identities are disjoint from train."""
    if task == "person" and (a.coco_root or a.crowdhuman_root):
        tr, va = [], []
        if a.coco_root:
            tr += real.coco_person_items(a.coco_root, "train", a.limit)
            va += real.coco_person_items(a.coco_root, "val", a.limit)
        if a.crowdhuman_root:
            tr += real.crowdhuman_items(a.crowdhuman_root, "train", a.limit)
            va += real.crowdhuman_items(a.crowdhuman_root, "val", a.limit)
        return real.BoxDataset(tr, size), real.BoxDataset(va, size, train=False)
    if task == "face" and a.wider_root:
        return (real.BoxDataset(real.wider_face_items(a.wider_root, "train", a.wider_landmarks, a.limit), size, 5),
                real.BoxDataset(real.wider_face_items(a.wider_root, "val", None, a.limit), size, 5, train=False))
    if task == "emb" and a.faceid_root:
        tr = real.FaceIdFolder(a.faceid_root, size)
        va = real.FaceIdFolder(a.faceid_val_root, size) if a.faceid_val_root else tr
        return tr, va
    if task == "person":
        return (SyntheticPersonDataset(a.n_train, seed=a.seed * 2 + 1, size=size),
                SyntheticPersonDataset(a.n_val, seed=a.seed * 2 + 2, size=size, train=False))
    if task == "face":
        return (SyntheticFaceDetDataset(a.n_train, seed=a.seed * 2 + 1, size=size),
                SyntheticFaceDetDataset(a.n_val, seed=a.seed * 2 + 2, size=size, train=False))
    # emb: n_train identities x per_id; val: n_val//10 unseen identities x 10
    n_ids = max(2, a.n_train)
    tr = SyntheticFaceIdDataset(n_ids, a.per_id, id_offset=0, seed=a.seed * 2 + 1, size=size)
    va = SyntheticFaceIdDataset(max(2, a.n_val // 10), 10, id_offset=1_000_000, seed=a.seed * 2 + 2, size=size)
    return tr, va


def n_classes(ds) -> int:
    if isinstance(ds, (ConcatDataset, Subset)):
        raise TypeError("pass the base dataset")
    return int(ds.labels.max()) + 1
