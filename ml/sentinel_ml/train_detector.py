"""Train the person or face detector (float), evaluate mAP@0.5, save a checkpoint + float ONNX.

Synthetic (default, CPU minutes):
    python -m sentinel_ml.train_detector --task person --n-train 2000 --epochs 20 --out artifacts/person
Real data (paths only; nothing is downloaded):
    python -m sentinel_ml.train_detector --task person --coco-root /data/coco --crowdhuman-root /data/crowdhuman
    python -m sentinel_ml.train_detector --task face --wider-root /data/widerface --wider-landmarks /data/retinaface/train/label.txt
Then quantise with ``python -m sentinel_ml.export --ckpt artifacts/person/person_float.pt --mode qat ...``.
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
from .train import eval_detector, torch_predictor, train_detector


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task", choices=["person", "face"], required=True)
    ap.add_argument("--width", type=float, default=1.0)
    ap.add_argument("--input-size", type=int, default=None, help="default 256 (person) / 160 (face)")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--init", help="start from this checkpoint")
    ap.add_argument("--out", default=None)
    add_data_args(ap, args_task := (ap.parse_known_args(argv)[0].task))
    a = ap.parse_args(argv)
    torch.manual_seed(a.seed)
    torch.set_num_threads(os.cpu_count() or 1)
    out = a.out or os.path.join("artifacts", a.task)
    os.makedirs(out, exist_ok=True)
    kw = {"width": a.width, "input_size": a.input_size or checkpoint.INPUT_SIZE[a.task]}
    model = checkpoint.load(a.init)[2] if a.init else checkpoint.build(a.task, **kw)
    train_ds, val_ds = make_datasets(args_task, a, kw["input_size"])
    n_kpt = 5 if a.task == "face" else 0
    print(f"{a.task}: {count_params(model):,} params, {len(train_ds)} train / {len(val_ds)} val")
    train_detector(model, train_ds, epochs=a.epochs, bs=a.bs, lr=a.lr, n_kpt=n_kpt, max_steps=a.max_steps)
    ap50, _ = eval_detector(torch_predictor(model), val_ds, n_kpt=n_kpt)
    print(f"val mAP@0.5 = {ap50:.4f}")
    ck = os.path.join(out, f"{a.task}_float.pt")
    checkpoint.save(ck, a.task, kw, model)
    export_float_onnx(model, os.path.join(out, f"{a.task}_float.onnx"), kw["input_size"], model.output_names())
    with open(os.path.join(out, "train_metrics.json"), "w") as f:
        json.dump({"map50": ap50, "n_train": len(train_ds), "n_val": len(val_ds)}, f, indent=2)
    print(f"saved {ck}")
    return ap50


if __name__ == "__main__":
    main()
