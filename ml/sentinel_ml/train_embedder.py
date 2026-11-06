"""Train the MobileFaceNet-style embedder (CosFace loss), report TAR@FAR on held-out
identities, save checkpoint + float ONNX.

    python -m sentinel_ml.train_embedder --n-train 400 --per-id 16 --epochs 14 --out artifacts/emb
    python -m sentinel_ml.train_embedder --faceid-root /data/casia_aligned --faceid-val-root /data/lfw_aligned
"""
from __future__ import annotations

import argparse
import json
import os

import torch

from . import checkpoint
from .datasets import add_data_args, make_datasets
from .models import count_params
from .quant import export_float_onnx
from .train import eval_verification, torch_embed_predictor, train_embedder


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--width", type=float, default=1.0)
    ap.add_argument("--depth", type=float, default=1.0)
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--out", default=os.path.join("artifacts", "emb"))
    add_data_args(ap, "emb")
    a = ap.parse_args(argv)
    torch.manual_seed(a.seed)
    torch.set_num_threads(os.cpu_count() or 1)
    os.makedirs(a.out, exist_ok=True)
    kw = {"width": a.width, "depth": a.depth}
    model = checkpoint.build("emb", **kw)
    train_ds, val_ds = make_datasets("emb", a, 112)
    n_cls = int(train_ds.labels.max()) + 1
    print(f"emb: {count_params(model):,} params, {len(train_ds)} train imgs / {n_cls} ids, {len(val_ds)} val imgs")
    _, head = train_embedder(model, train_ds, n_cls, epochs=a.epochs, bs=a.bs, lr=a.lr, max_steps=a.max_steps)
    res, _, _ = eval_verification(torch_embed_predictor(model), val_ds)
    print("val", res)
    checkpoint.save(os.path.join(a.out, "emb_float.pt"), "emb", kw, model, head=head.state_dict(), n_cls=n_cls)
    export_float_onnx(model, os.path.join(a.out, "emb_float.onnx"), 112, model.output_names())
    with open(os.path.join(a.out, "train_metrics.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


if __name__ == "__main__":
    main()
