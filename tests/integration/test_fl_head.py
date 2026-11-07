"""3d: fl/ head serialization (65 LE fp32) -> firmware/core sn_head -> same probabilities."""
import subprocess

import numpy as np
import pytest
from sentinel_fl import head as fh


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """A head trained by fl's own local solver on separable-ish features."""
    rng = np.random.default_rng(7)
    n = 512
    X = rng.normal(size=(n, fh.DIM))
    w_true = rng.normal(size=fh.DIM)
    y = (X @ w_true + 0.5 * rng.normal(size=n) > 0).astype(float)
    theta = fh.train_local(np.zeros(fh.N_PARAMS), X, y, fh.TrainConfig(epochs=3), rng)
    return theta, tmp_path_factory.mktemp("head")


def _c_probs(fw_bin, d, blob, X32):
    hp, fp = d / "head.bin", d / "x.bin"
    hp.write_bytes(blob)
    fp.write_bytes(X32.astype("<f4").tobytes())
    r = subprocess.run([str(fw_bin("core/head_eval")), str(hp), str(fp)], capture_output=True, text=True)
    return r.returncode, r.stdout


def test_round_trip_probabilities_match(fw_bin, trained):
    theta, d = trained
    blob = fh.serialize(theta)
    assert len(blob) == fh.WIRE_BYTES == 260 and blob == fh.serialize_struct(np.float32(theta))
    rng = np.random.default_rng(1)
    X32 = (rng.normal(size=(1000, fh.DIM)) * rng.choice([0.1, 1.0, 5.0], size=(1000, 1))).astype(np.float32)
    rc, out = _c_probs(fw_bin, d, blob, X32)
    assert rc == 0, out
    p_c = np.array([float(v) for v in out.split()])
    p_fl = fh.predict_proba(fh.deserialize(blob).astype(np.float64), X32.astype(np.float64))
    assert p_c.shape == p_fl.shape == (1000,)
    assert np.max(np.abs(p_c - p_fl)) <= 1e-6
    # fl's own float32 path (theta float32 x features float32) also agrees
    p_fl32 = fh.predict_proba(fh.deserialize(blob), X32)
    assert np.max(np.abs(p_c - p_fl32)) <= 1e-6
    assert p_c.min() < 0.05 and p_c.max() > 0.95             # the test spans the sigmoid


def test_extreme_logits_stay_finite(fw_bin, trained):
    theta, d = trained
    t = np.zeros(fh.N_PARAMS)
    t[0], t[-1] = 50.0, -3.0
    X32 = np.zeros((3, fh.DIM), np.float32)
    X32[:, 0] = [-1e6, 0.0, 1e6]
    rc, out = _c_probs(fw_bin, d, fh.serialize(t), X32)
    p_c = np.array([float(v) for v in out.split()])
    assert rc == 0 and np.all(np.isfinite(p_c))
    assert np.allclose(p_c, fh.predict_proba(t, X32.astype(np.float64)), atol=1e-12)


def test_c_rejects_what_fl_rejects(fw_bin, trained):
    theta, d = trained
    blob = bytearray(fh.serialize(theta))
    X32 = np.zeros((1, fh.DIM), np.float32)
    assert _c_probs(fw_bin, d, bytes(blob[:-1]), X32)[0] == 1           # truncated
    blob[8:12] = np.float32(np.nan).tobytes()
    with pytest.raises(ValueError):
        fh.deserialize(bytes(blob))
    assert _c_probs(fw_bin, d, bytes(blob), X32) == (1, "REJECT\n")
