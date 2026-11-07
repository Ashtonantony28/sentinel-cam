"""End-to-end quick profile (tiny models, synthetic data) + CLIs + device-embedding rounding."""
import json
import os

import numpy as np
import pytest

from sentinel_ml import gallery, pipeline
from sentinel_ml.npu_fit import main as npu_fit_main


def test_quick_pipeline_end_to_end(quick_run):
    res, out = quick_run
    assert set(res["models"]) == {"person", "face", "emb"}
    for k, R in res["models"].items():
        assert R["loss_last"] < R["loss_first"], k
        for v in ("float", "ptq", "qat"):
            V = R["variants"][v]
            assert V["npu_fit"]["pass"], (k, v, V["npu_fit"]["violations"])
            assert V["macs"] > 0 and V["params"] > 0 and V["latency_cpu_1t"]["median_ms"] > 0
            metric = "map50" if k != "emb" else "tar@far=0.01"
            assert 0.0 <= V[metric] <= 1.0
            assert len(V["npu_fit"]["op_set_sha256"]) == 64
        assert R["variants"]["qat"]["npu_fit"]["float_compute_inputs"] == 0
        e = R["energy"]
        assert e["mj_per_inference"] == pytest.approx(R["variants"]["qat"]["macs"] * e["pj_per_mac"] * 1e-9)
    md = (out / "results.md").read_text()
    assert "synthetic" in md and "mAP@0.5" in md
    assert json.loads((out / "results.json").read_text())["profile"] == "quick"


def test_npu_fit_cli_on_exported_models(quick_run):
    _, out = quick_run
    models = [str(out / f"{k}_int8_qat.onnx") for k in ("person", "face", "emb")]
    assert npu_fit_main(models + ["--require-int8", "--json", str(out / "fit.json")]) == 0
    assert len(json.loads((out / "fit.json").read_text())) == 3
    assert (out / "person_int8_qat.opset.txt").read_text().startswith("add\n")


def test_device_embedding_path_uses_contract_rounding(monkeypatch):
    """verification_int8 (the device path) must quantise via gallery.quantize_embedding
    (round-half-away-from-zero), not numpy's half-to-even."""
    calls = []
    real_q = gallery.quantize_embedding

    def spy(x):
        calls.append(np.asarray(x).shape)
        return real_q(x)

    monkeypatch.setattr(pipeline, "quantize_embedding", spy)
    rng = np.random.default_rng(0)
    e = rng.normal(size=(12, 128)).astype(np.float32)
    pipeline.verification_int8(e, np.repeat(np.arange(4), 3))
    assert calls == [(12, 128)]
    # and that rule differs from half-to-even exactly on the ties
    x = np.array([0.5, 1.5, 2.5, -0.5, -2.5], np.float32) / np.float32(127)
    assert list(real_q(x)) == [1, 2, 3, -1, -3]
    assert list(np.round(x * np.float32(127)).astype(int)) != [1, 2, 3, -1, -3]


def test_no_other_rounding_of_embeddings_in_code():
    """Only gallery.py may turn embeddings into int8 (QDQ weight/bias folding in quant.py is
    model quantisation and correctly uses ONNX's half-to-even)."""
    root = os.path.join(os.path.dirname(__file__), "..", "sentinel_ml")
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".py") or f in ("gallery.py", "quant.py"):
                continue
            src = open(os.path.join(dp, f)).read()
            for bad in ("np.round(", "np.rint(", "torch.round(", ".round("):
                assert bad not in src, (f, bad)


def test_train_and_export_clis(tmp_path):
    from sentinel_ml import export, train_detector, train_embedder
    out = str(tmp_path)
    train_detector.main(["--task", "face", "--width", "0.5", "--input-size", "96", "--n-train", "16", "--n-val", "8",
                         "--epochs", "1", "--bs", "8", "--out", out])
    assert export.main(["--ckpt", os.path.join(out, "face_float.pt"), "--mode", "ptq", "--n-train", "16",
                        "--n-val", "8"]) == 0
    assert os.path.exists(os.path.join(out, "face_int8_ptq.onnx"))
    assert os.path.exists(os.path.join(out, "face_int8_ptq.opset.txt"))
    train_embedder.main(["--width", "0.25", "--depth", "0.34", "--n-train", "4", "--per-id", "4", "--n-val", "20",
                         "--epochs", "1", "--bs", "8", "--out", out])
    assert export.main(["--ckpt", os.path.join(out, "emb_float.pt"), "--mode", "qat", "--qat-steps", "2", "--bs", "8",
                        "--n-train", "4", "--per-id", "4", "--n-val", "20"]) == 0
