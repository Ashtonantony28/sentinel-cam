"""Model checkpoints: ``{"kind", "kwargs", "state_dict"}`` so a model can be rebuilt anywhere."""
from __future__ import annotations

import torch

from .models import build_embedder, build_face_detector, build_person_detector

BUILDERS = {"person": build_person_detector, "face": build_face_detector, "emb": build_embedder}
INPUT_SIZE = {"person": 256, "face": 160, "emb": 112}


def build(kind: str, **kwargs):
    return BUILDERS[kind](**kwargs)


def input_size(kind: str, kwargs: dict) -> int:
    return kwargs.get("input_size", INPUT_SIZE[kind])


def save(path: str, kind: str, kwargs: dict, model, **extra):
    """``extra``: e.g. ``head=<CosFace state_dict>, n_cls=N`` so QAT can continue the same loss."""
    torch.save({"kind": kind, "kwargs": kwargs, "state_dict": model.state_dict(), **extra}, path)


def load_extra(path: str) -> dict:
    ck = torch.load(path, map_location="cpu", weights_only=False)
    return {k: v for k, v in ck.items() if k not in ("kind", "kwargs", "state_dict")}


def load(path: str):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = build(ck["kind"], **ck["kwargs"])
    m.load_state_dict(ck["state_dict"])
    return ck["kind"], ck["kwargs"], m.eval()
