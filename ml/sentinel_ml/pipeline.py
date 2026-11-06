"""End-to-end run on synthetic data (or real data via the per-model CLIs):

    float train → float ONNX → PTQ (torch.ao) int8 ONNX → QAT (torch.ao) int8 ONNX
    → [optional onnxruntime PTQ] → parity float vs int8 → task metrics (mAP / TAR@FAR)
    → NPU-fit → MACs x pJ/MAC energy → CPU latency (onnxruntime, 1 thread)

    python -m sentinel_ml.pipeline --profile full --out artifacts/full
    python -m sentinel_ml.pipeline --profile quick --out /tmp/x   # ~1 min, used by tests

Writes ``results.json`` and ``results.md`` (the README tables) into ``--out``.
All numbers from this module are on SYNTHETIC data unless real roots are wired in.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import time
from types import SimpleNamespace

import numpy as np
import torch

from . import checkpoint
from .datasets import make_datasets
from .detection import decode
from .eval.energy import energy_mj, load_energy_constants, measure_latency_ms
from .eval.metrics import tar_at_far, verification_pairs
from .gallery import quantize_embedding
from .models import count_params
from .npu_fit import analyze
from .quant import (OrtModel, calibrate_ptq, export_float_onnx, export_quant_onnx, ort_ptq, prepare_quant)
from .train import eval_detector, train_detector, train_embedder

# Parity tolerances (float ONNX vs int8 QDQ ONNX, same inputs). Stated here, asserted
# in tests/test_pipeline.py and reported in results.md.
TOL = {
    "det_prob_max_abs": 0.15,     # max |sigmoid(obj_f) - sigmoid(obj_q)| over all cells
    "det_prob_mean_abs": 0.01,    # mean of the same
    "det_box_rel_l2": 0.10,       # ||q - f|| / ||f|| on box channels of positive-ish cells
    "feat_cos_min": 0.97,         # 64-d false-alert feature
    "emb_cos_mean": 0.98,         # 128-d embedding, after L2 norm
    "emb_cos_min": 0.95,
    "map_gap": 0.03,              # |mAP_float - mAP_int8| (QAT model), full profile
    "tar_gap": 0.03,              # |TAR_float - TAR_int8| @ FAR 1e-2, full profile
}

PROFILES = {
    "quick": {
        "person": dict(kwargs=dict(width=0.5, input_size=128), n_train=96, n_val=32, epochs=2, bs=16, lr=3e-3,
                       qat_steps=6, qat_lr=3e-4, ort_ptq=True),
        "face": dict(kwargs=dict(width=0.5, input_size=96), n_train=96, n_val=32, epochs=2, bs=16, lr=3e-3,
                     qat_steps=6, qat_lr=3e-4, ort_ptq=False),
        "emb": dict(kwargs=dict(width=0.25, depth=0.34), n_train=16, per_id=6, n_val=60, epochs=2, bs=32, lr=3e-3,
                    qat_steps=6, qat_lr=3e-4, ort_ptq=False),
        "lat_runs": 3,
    },
    "full": {
        "person": dict(kwargs=dict(width=1.0, input_size=256), n_train=2400, n_val=300, epochs=24, bs=32, lr=3e-3,
                       qat_steps=300, qat_lr=3e-4, ort_ptq=True),
        "face": dict(kwargs=dict(width=1.0, input_size=160), n_train=3000, n_val=300, epochs=24, bs=32, lr=3e-3,
                     qat_steps=300, qat_lr=3e-4, ort_ptq=True),
        "emb": dict(kwargs=dict(width=1.0, depth=1.0), n_train=400, per_id=16, n_val=1000, epochs=14, bs=64,
                    lr=3e-3, qat_steps=200, qat_lr=3e-4, ort_ptq=True),
        "lat_runs": 30,
    },
}


def _x(ds, n):
    return np.stack([ds[i][0].numpy() for i in range(min(n, len(ds)))])


def _calib(ds, n=64, bs=16):
    idx = np.random.default_rng(0).permutation(len(ds))[:n]
    xs = np.stack([ds[int(i)][0].numpy() for i in idx])
    return [torch.from_numpy(xs[i:i + bs]) for i in range(0, len(xs), bs)]


def _sig(x):
    return 1 / (1 + np.exp(-x))


def parity_detector(f_outs, q_outs):
    """f_outs/q_outs: lists of arrays per output (batched)."""
    res = {"prob_max_abs": 0.0, "prob_mean_abs": [], "box_rel_l2": []}
    for f, q in zip(f_outs[:3], q_outs[:3]):
        pf, pq = _sig(f[:, 0]), _sig(q[:, 0])
        d = np.abs(pf - pq)
        res["prob_max_abs"] = max(res["prob_max_abs"], float(d.max()))
        res["prob_mean_abs"].append(float(d.mean()))
        m = (pf > 0.05)[:, None].repeat(4, 1)
        if m.any():
            bf, bq = f[:, 1:5][m], q[:, 1:5][m]
            res["box_rel_l2"].append(float(np.linalg.norm(bq - bf) / max(np.linalg.norm(bf), 1e-9)))
    res["prob_mean_abs"] = float(np.mean(res["prob_mean_abs"]))
    res["box_rel_l2"] = float(np.max(res["box_rel_l2"])) if res["box_rel_l2"] else 0.0
    if len(f_outs) > 3:
        a, b = f_outs[3], q_outs[3]
        cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12)
        res["feat_cos_min"] = float(cos.min())
    return res


def parity_embedder(f, q):
    f = f / np.linalg.norm(f, axis=1, keepdims=True)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    c = (f * q).sum(1)
    return {"cos_mean": float(c.mean()), "cos_min": float(c.min())}


def verification_int8(emb_f32: np.ndarray, labels: np.ndarray, fars=(1e-2, 1e-3)):
    """Device path: L2-normalised float → int8 (INTERFACES §1) → int8 cosine."""
    e = emb_f32 / np.linalg.norm(emb_f32, axis=1, keepdims=True)
    qi = quantize_embedding(e).astype(np.int32)
    n = np.sqrt((qi * qi).sum(1)).astype(np.float64)
    S = (qi @ qi.T) / np.outer(n, n)
    iu = np.triu_indices(len(labels), 1)
    same = labels[iu[0]] == labels[iu[1]]
    gen, imp = S[iu][same], S[iu][~same]
    return {f"tar@far={far:g}": tar_at_far(gen, imp, far)[0] for far in fars}


def verification_float(emb, labels, fars=(1e-2, 1e-3)):
    gen, imp = verification_pairs(emb, labels, max_impostor=10 ** 9)
    return {f"tar@far={far:g}": tar_at_far(gen, imp, far)[0] for far in fars}


def _embed_onnx(path, xs, bs=64):
    m = OrtModel(path, threads=os.cpu_count() or 1)
    return np.concatenate([m(xs[i:i + bs])[0] for i in range(0, len(xs), bs)])


def run_model(kind: str, cfg: dict, out: str, log=print, lat_runs: int = 20, seed: int = 0) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)
    os.makedirs(out, exist_ok=True)
    kw = cfg["kwargs"]
    size = checkpoint.input_size(kind, kw)
    a = SimpleNamespace(n_train=cfg["n_train"], n_val=cfg["n_val"], seed=seed, limit=None, per_id=cfg.get("per_id", 16),
                        coco_root=None, crowdhuman_root=None, wider_root=None, wider_landmarks=None,
                        faceid_root=None, faceid_val_root=None)
    t0 = time.time()
    train_ds, val_ds = make_datasets(kind, a, size)
    log(f"[{kind}] data: {len(train_ds)} train / {len(val_ds)} val ({time.time() - t0:.0f}s)")
    model = checkpoint.build(kind, **kw)
    n_kpt = 5 if kind == "face" else 0
    R = {"kind": kind, "input": [1, 3, size, size], "kwargs": kw, "data": "synthetic",
         "n_train": len(train_ds), "n_val": len(val_ds)}
    R["params_torch"] = count_params(model) - (count_params(model.aux) if hasattr(model, "aux") else 0)

    # ---------------- float training
    t = time.time()
    head = None
    if kind == "emb":
        hist, head = train_embedder(model, train_ds, int(train_ds.labels.max()) + 1, epochs=cfg["epochs"],
                                    bs=cfg["bs"], lr=cfg["lr"], log=log)
    else:
        hist = train_detector(model, train_ds, epochs=cfg["epochs"], bs=cfg["bs"], lr=cfg["lr"], n_kpt=n_kpt, log=log)
    R["train_s"] = round(time.time() - t, 1)
    R["loss_first"], R["loss_last"] = float(np.mean(hist[:5])), float(np.mean(hist[-5:]))
    model.eval()
    extra = {"head": head.state_dict(), "n_cls": int(train_ds.labels.max()) + 1} if head is not None else {}
    checkpoint.save(os.path.join(out, f"{kind}_float.pt"), kind, kw, model, **extra)
    names = model.output_names()
    paths = {"float": os.path.join(out, f"{kind}_float.onnx")}
    export_float_onnx(model, paths["float"], size, names)

    # ---------------- PTQ (torch.ao): observers only, calibration batches
    calib = _calib(train_ds)
    q_ptq = calibrate_ptq(prepare_quant(model, ptq=True), calib)
    paths["ptq"] = os.path.join(out, f"{kind}_int8_ptq.onnx")
    export_quant_onnx(q_ptq, paths["ptq"], size, names)

    # ---------------- QAT (torch.ao): start from float weights, observers warmed on calib, fine-tune
    t = time.time()
    q_qat = prepare_quant(model, ptq=False)
    with torch.no_grad():
        q_qat.train()
        for xb in calib:
            q_qat(xb)
    if kind == "emb":
        train_embedder(q_qat, train_ds, int(train_ds.labels.max()) + 1, epochs=10 ** 6, bs=cfg["bs"],
                       lr=cfg["qat_lr"], log=log, max_steps=cfg["qat_steps"], head=copy.deepcopy(head))
    else:
        train_detector(q_qat, train_ds, epochs=10 ** 6, bs=cfg["bs"], lr=cfg["qat_lr"], n_kpt=n_kpt, log=log,
                       max_steps=cfg["qat_steps"])
    R["qat_s"] = round(time.time() - t, 1)
    paths["qat"] = os.path.join(out, f"{kind}_int8_qat.onnx")
    export_quant_onnx(q_qat, paths["qat"], size, names)
    if cfg.get("ort_ptq"):
        paths["ort_ptq"] = os.path.join(out, f"{kind}_int8_ortptq.onnx")
        ort_ptq(paths["float"], paths["ort_ptq"], [c.numpy()[i:i + 1] for c in calib for i in range(len(c))])

    # ---------------- metrics per variant
    R["variants"] = {}
    xs_par = _x(val_ds, 32)
    f_outs = OrtModel(paths["float"])(xs_par)
    for v, p in paths.items():
        r = {}
        if kind == "emb":
            xs = _x(val_ds, len(val_ds))
            labels = np.array([val_ds[i][1] for i in range(len(val_ds))])
            e = _embed_onnx(p, xs)
            r.update({k: round(x, 4) for k, x in verification_float(e, labels).items()})
            r.update({k + " (int8 emb)": round(x, 4) for k, x in verification_int8(e, labels).items()})
            if v != "float":
                r["parity"] = parity_embedder(_embed_onnx(paths["float"], xs[:256]), e[:256])
        else:
            m = OrtModel(p, threads=os.cpu_count() or 1)
            r["map50"] = round(eval_detector(m, val_ds, n_kpt=n_kpt)[0], 4)
            if v != "float":
                r["parity"] = parity_detector(f_outs, OrtModel(p)(xs_par))
        fit = analyze(p)
        r["npu_fit"] = {"pass": not fit["violations"], "violations": fit["violations"],
                        "float_compute_inputs": len(fit["float_compute_inputs"]),
                        "ops": fit["ops"], "op_set_sha256": fit["op_set_sha256"]}
        r["params"], r["macs"] = fit["params"], fit["macs"]
        r["weight_bytes"], r["peak_activation_bytes"] = fit["weight_bytes"], fit["peak_activation_bytes"]
        r["latency_cpu_1t"] = measure_latency_ms(OrtModel(p, threads=1), xs_par[:1], runs=lat_runs)
        r["onnx_bytes"] = os.path.getsize(p)
        R["variants"][v] = r
        log(f"[{kind}] {v}: " + ", ".join(f"{k}={r[k]}" for k in r if k.startswith(("map", "tar"))))
    c = load_energy_constants()
    macs = R["variants"]["qat"]["macs"]
    R["energy"] = {"pj_per_mac": c["pj_per_mac"], "source": c["source"],
                   "mj_per_inference": energy_mj(macs, c["pj_per_mac"]),          # the spec'd MACs x pJ/MAC
                   "mj_range": [energy_mj(macs, c["low"]), energy_mj(macs, c["high"])],
                   "overhead_mj": c["overhead_mj"],
                   "mj_with_overhead": (energy_mj(macs, c["pj_per_mac"]) + c["overhead_mj"])
                   if c["overhead_mj"] is not None else None}
    R["paths"] = paths
    return R


def _fmt_pct(x):
    return f"{100 * x:.1f}"


def render_markdown(results: dict) -> str:
    env = results["env"]
    L = [f"Profile `{results['profile']}` — **synthetic data** — {env['cpu']} ({env['threads']} threads), "
         f"torch {env['torch']}, onnxruntime {env['ort']}, total {results['total_s']:.0f} s.", ""]
    L += ["| Model | Input | Params | MACs | Weights (int8) | Peak act. (int8, est.) | NPU-fit | Energy/inf. MACs x pJ/MAC (range) | CPU latency float / int8 (1 thread) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k, R in results["models"].items():
        q = R["variants"]["qat"]
        f = R["variants"]["float"]
        L.append(f"| {k} | {R['input'][2]}x{R['input'][3]} | {q['params']:,} | {q['macs'] / 1e6:.1f} M | "
                 f"{q['weight_bytes'] / 1024:.0f} KiB | {q['peak_activation_bytes'] / 1024:.0f} KiB | "
                 f"{'PASS' if q['npu_fit']['pass'] else 'FAIL'} | {R['energy']['mj_per_inference']:.3f} mJ "
                 f"({R['energy']['mj_range'][0]:.3f}–{R['energy']['mj_range'][1]:.3f}) | "
                 f"{f['latency_cpu_1t']['median_ms']:.1f} / {q['latency_cpu_1t']['median_ms']:.1f} ms |")
    L += ["", "| Model | Metric | float | int8 PTQ (torch.ao) | int8 QAT (torch.ao) | int8 PTQ (onnxruntime) |",
          "|---|---|---|---|---|---|"]
    for k, R in results["models"].items():
        V = R["variants"]
        metrics = ["map50"] if k != "emb" else ["tar@far=0.01", "tar@far=0.001", "tar@far=0.01 (int8 emb)",
                                                 "tar@far=0.001 (int8 emb)"]
        for mname in metrics:
            row = [f"{_fmt_pct(V[v][mname])}" if v in V else "–" for v in ("float", "ptq", "qat", "ort_ptq")]
            label = {"map50": "mAP@0.5 (%)"}.get(mname, mname.replace("tar@far=", "TAR@FAR=") + " (%)")
            L.append(f"| {k} | {label} | " + " | ".join(row) + " |")
    L += ["", "Parity, float ONNX vs int8 QDQ ONNX (QAT), on 32 validation inputs (tolerance in brackets):", ""]
    for k, R in results["models"].items():
        p = R["variants"]["qat"]["parity"]
        if k == "emb":
            L.append(f"- {k}: cosine mean {p['cos_mean']:.4f} [>= {TOL['emb_cos_mean']}], "
                     f"min {p['cos_min']:.4f} [>= {TOL['emb_cos_min']}]")
        else:
            s = (f"- {k}: objectness prob max |Δ| {p['prob_max_abs']:.3f} [<= {TOL['det_prob_max_abs']}], "
                 f"mean |Δ| {p['prob_mean_abs']:.4f} [<= {TOL['det_prob_mean_abs']}], "
                 f"box rel-L2 {p['box_rel_l2']:.3f} [<= {TOL['det_box_rel_l2']}]")
            if "feat_cos_min" in p:
                s += f", 64-d feature cosine min {p['feat_cos_min']:.4f} [>= {TOL['feat_cos_min']}]"
            L.append(s)
    e = results["models"][next(iter(results["models"]))]["energy"]
    L += ["", f"Energy = MACs x {e['pj_per_mac']} pJ/MAC (range from the file's low/high) from `{e['source']}`."]
    if e["overhead_mj"] is not None:
        L += [f"With the file's fixed per-inference overhead ({e['overhead_mj']} mJ) added: " + ", ".join(
            f"{k} {R['energy']['mj_with_overhead']:.3f} mJ" for k, R in results["models"].items()) + "."]
    return "\n".join(L) + "\n"


def run(profile: str, out: str, kinds=("person", "face", "emb"), log=print) -> dict:
    P = PROFILES[profile]
    t = time.time()
    torch.set_num_threads(os.cpu_count() or 1)
    res = {"profile": profile, "models": {}, "tolerances": TOL,
           "env": {"cpu": platform.processor() or platform.machine(), "threads": torch.get_num_threads(),
                   "torch": torch.__version__, "ort": __import__("onnxruntime").__version__}}
    for k in kinds:
        tm = time.time()
        res["models"][k] = run_model(k, P[k], out, log=log, lat_runs=P["lat_runs"])
        res["models"][k]["wall_s"] = round(time.time() - tm, 1)
        with open(os.path.join(out, f"results_{k}.json"), "w") as f:
            json.dump(res["models"][k], f, indent=2, default=float)
    res["total_s"] = time.time() - t
    return _write(res, out)


def _write(res, out):
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(os.path.join(out, "results.md"), "w") as f:
        f.write(render_markdown(res))
    return res


def merge(out: str, profile: str) -> dict:
    """Re-render results.json/.md from per-model ``results_<kind>.json`` (models run separately)."""
    res = {"profile": profile, "models": {}, "tolerances": TOL,
           "env": {"cpu": platform.processor() or platform.machine(), "threads": os.cpu_count(),
                   "torch": torch.__version__, "ort": __import__("onnxruntime").__version__}}
    for k in ("person", "face", "emb"):
        p = os.path.join(out, f"results_{k}.json")
        if os.path.exists(p):
            with open(p) as f:
                res["models"][k] = json.load(f)
    res["total_s"] = sum(m.get("wall_s", 0) for m in res["models"].values())
    return _write(res, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    ap.add_argument("--out", default="artifacts/run")
    ap.add_argument("--models", default="person,face,emb")
    ap.add_argument("--merge", action="store_true", help="only re-render from results_<kind>.json in --out")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    res = merge(a.out, a.profile) if a.merge else run(a.profile, a.out, tuple(a.models.split(",")))
    print(render_markdown(res))


if __name__ == "__main__":
    main()
