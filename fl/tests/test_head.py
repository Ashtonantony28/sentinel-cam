import struct

import numpy as np
import pytest

from sentinel_fl.head import (DIM, N_PARAMS, WIRE_BYTES, TrainConfig, deserialize, loss_and_grad, predict_proba,
                              serialize, serialize_struct, train_local)


def test_wire_constants_match_interfaces():
    assert DIM == 64 and N_PARAMS == 65 and WIRE_BYTES == 260


def test_serialize_layout_w_then_b_little_endian():
    theta = np.arange(65, dtype=np.float64) * 0.5 - 3.0
    blob = serialize(theta)
    assert len(blob) == 260
    assert struct.unpack("<f", blob[0:4])[0] == theta[0]          # w[0]
    assert struct.unpack("<f", blob[252:256])[0] == theta[63]     # w[63]
    assert struct.unpack("<f", blob[256:260])[0] == theta[64]     # b
    assert blob == serialize_struct(theta)                        # independent encoder agrees
    assert serialize(np.ones(65))[:4] == b"\x00\x00\x80\x3f"      # 1.0f LE


def test_roundtrip_random():
    rng = np.random.default_rng(0)
    for _ in range(50):
        theta = rng.normal(scale=10, size=65).astype(np.float32)
        back = deserialize(serialize(theta))
        assert back.dtype == np.float32 and np.array_equal(back, theta)
        assert serialize(back) == serialize(theta)


def test_roundtrip_float64_rounds_to_fp32():
    theta = np.full(65, 1 / 3)
    assert np.allclose(deserialize(serialize(theta)), theta, rtol=1e-7)


@pytest.mark.parametrize("bad", [b"", b"\x00" * 259, b"\x00" * 261])
def test_deserialize_rejects_wrong_length(bad):
    with pytest.raises(ValueError):
        deserialize(bad)


def test_rejects_nonfinite_and_wrong_shape():
    t = np.zeros(65)
    t[3] = np.nan
    with pytest.raises(ValueError):
        serialize(t)
    with pytest.raises(ValueError):
        serialize(np.zeros(64))
    with pytest.raises(ValueError):
        deserialize(struct.pack("<65f", *([0.0] * 64 + [float("inf")])))


def test_gradient_matches_finite_differences():
    rng = np.random.default_rng(1)
    X, y = rng.normal(size=(40, 64)), (rng.random(40) < 0.3).astype(float)
    theta, ref = rng.normal(size=65) * 0.1, rng.normal(size=65) * 0.1
    kw = dict(l2=0.01, prox_mu=0.3, theta_ref=ref, pos_weight=2.0)
    _, g = loss_and_grad(theta, X, y, **kw)
    num = np.zeros(65)
    for i in range(65):
        e = np.zeros(65); e[i] = 1e-6
        num[i] = (loss_and_grad(theta + e, X, y, **kw)[0] - loss_and_grad(theta - e, X, y, **kw)[0]) / 2e-6
    assert np.allclose(g, num, atol=1e-6)


def test_sigmoid_stable_at_extremes():
    X = np.ones((2, 64))
    p = predict_proba(np.concatenate([np.full(64, 100.0), [0]]), X)
    assert np.all(np.isfinite(p)) and p[0] == pytest.approx(1.0)
    p = predict_proba(np.concatenate([np.full(64, -100.0), [0]]), X)
    assert np.all(np.isfinite(p)) and p[0] == pytest.approx(0.0)


def test_training_learns_separable_data():
    rng = np.random.default_rng(2)
    w_true = rng.normal(size=64)
    X = rng.normal(size=(500, 64))
    y = (X @ w_true > 0).astype(float)
    theta = train_local(np.zeros(65), X, y, TrainConfig(epochs=20, lr=0.5, l2=0.0), rng)
    acc = ((predict_proba(theta, X) >= 0.5) == y).mean()
    assert acc > 0.95


def test_training_on_empty_home_is_noop():
    t0 = np.ones(65)
    out = train_local(t0, np.empty((0, 64)), np.empty(0), TrainConfig(), np.random.default_rng(0))
    assert np.array_equal(out, t0)
