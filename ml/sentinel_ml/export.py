"""Quantise a float checkpoint to an int8 QDQ ONNX, check parity and NPU fit.

    python -m sentinel_ml.export --ckpt artifacts/person/person_float.pt --mode qat --qat-steps 300
    python -m sentinel_ml.export --ckpt artifacts/emb/emb_float.pt --mode ptq
    python -m sentinel_ml.export --ckpt ... --mode ort-ptq     # onnxruntime static quantisation instead

``--mode qat`` needs training data (same data flags as the train CLIs; synthetic by
default). Exits non-zero if the int8 model fails the NPU-fit allowlist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch

from . import checkpoint
from .datasets import add_data_args, make_datasets
from .npu_fit import analyze, format_report
from .pipeline import _calib, _x, parity_detector, parity_embedder
from .quant import OrtModel, calibrate_ptq, export_float_onnx, export_quant_onnx, ort_ptq, prepare_quant
from .train import train_detector, train_embedder


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--mode", choices=["ptq", "qat", "ort-ptq"], default="qat")
    ap.add_argument("--qat-steps", type=int, default=300)
    ap.add_argument("--qat-lr", type=float, default=3e-4)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--out", default=None, help="output .onnx (default next to the checkpoint)")
    pre = ap.parse_known_args(argv)[0]
    kind, kw, model = checkpoint.load(pre.ckpt)
    add_data_args(ap, kind)
    a = ap.parse_args(argv)
    torch.set_num_threads(os.cpu_count() or 1)
    size = checkpoint.input_size(kind, kw)
    names = model.output_names()
    base = os.path.splitext(a.ckpt)[0].replace("_float", "")
    out = a.out or f"{base}_int8_{a.mode.replace('-', '')}.onnx"
    f_onnx = f"{base}_float.onnx"
    export_float_onnx(model, f_onnx, size, names)
    a.n_train = min(a.n_train, 512) if a.mode != "qat" else a.n_train
    train_ds, val_ds = make_datasets(kind, a, size)
    calib = _calib(train_ds)
    if a.mode == "ptq":
        export_quant_onnx(calibrate_ptq(prepare_quant(model, ptq=True), calib), out, size, names)
    elif a.mode == "ort-ptq":
        ort_ptq(f_onnx, out, [c.numpy()[i:i + 1] for c in calib for i in range(len(c))])
    else:
        q = prepare_quant(model, ptq=False)
        with torch.no_grad():
            for xb in calib:
                q(xb)
        if kind == "emb":
            from .train import CosFaceHead

            ex = checkpoint.load_extra(a.ckpt)
            n_cls = int(train_ds.labels.max()) + 1
            head = CosFaceHead(model.emb_dim, n_cls)
            if ex.get("n_cls") == n_cls:
                head.load_state_dict(ex["head"])  # continue the same loss; else a fresh head is learned
            train_embedder(q, train_ds, n_cls, epochs=10 ** 6, bs=a.bs, lr=a.qat_lr, max_steps=a.qat_steps,
                           head=head)
        else:
            train_detector(q, train_ds, epochs=10 ** 6, bs=a.bs, lr=a.qat_lr, n_kpt=5 if kind == "face" else 0,
                           max_steps=a.qat_steps)
        export_quant_onnx(q, out, size, names)
    xs = _x(val_ds, 32)
    fo, qo = OrtModel(f_onnx)(xs), OrtModel(out)(xs)
    par = parity_embedder(fo[0], qo[0]) if kind == "emb" else parity_detector(fo, qo)
    rep = analyze(out)
    print(format_report(rep))
    print("parity float vs int8:", json.dumps(par))
    print(f"wrote {out}")
    return 0 if not rep["violations"] else 1


if __name__ == "__main__":
    sys.exit(main())
