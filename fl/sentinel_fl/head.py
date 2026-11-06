"""False-alert head: logistic regression on 64-d features (INTERFACES.md §5).

P(real person event | x) = sigmoid(w·x + b). Parameters travel as a flat 65-vector
`theta = [w_0..w_63, b]`; the wire/flash format is 65 little-endian fp32, w then b.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

DIM = 64
N_PARAMS = DIM + 1
WIRE_BYTES = 4 * N_PARAMS  # 260


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.where(z >= 0, 1.0 / (1.0 + np.exp(-np.abs(z))), np.exp(-np.abs(z)) / (1.0 + np.exp(-np.abs(z))))


def predict_proba(theta: np.ndarray, X: np.ndarray) -> np.ndarray:
    return sigmoid(X @ theta[:DIM] + theta[DIM])


def loss_and_grad(theta: np.ndarray, X: np.ndarray, y: np.ndarray, l2: float = 0.0,
                  prox_mu: float = 0.0, theta_ref: Optional[np.ndarray] = None,
                  pos_weight: float = 1.0) -> tuple[float, np.ndarray]:
    """Mean (optionally class-weighted) log loss + L2 on w + FedProx proximal term."""
    p = predict_proba(theta, X)
    eps = 1e-12
    sw = np.where(y > 0.5, pos_weight, 1.0)
    norm = sw.sum()
    loss = -np.sum(sw * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))) / norm
    r = sw * (p - y) / norm
    g = np.empty(N_PARAMS)
    g[:DIM] = X.T @ r
    g[DIM] = r.sum()
    if l2:
        loss += 0.5 * l2 * float(theta[:DIM] @ theta[:DIM])
        g[:DIM] += l2 * theta[:DIM]
    if prox_mu and theta_ref is not None:
        d = theta - theta_ref
        loss += 0.5 * prox_mu * float(d @ d)
        g += prox_mu * d
    return float(loss), g


@dataclass(frozen=True)
class TrainConfig:
    epochs: int = 5
    lr: float = 0.5
    batch_size: int = 32
    l2: float = 1e-3
    prox_mu: float = 0.0


def train_local(theta0: np.ndarray, X: np.ndarray, y: np.ndarray, cfg: TrainConfig,
                rng: np.random.Generator, theta_ref: Optional[np.ndarray] = None) -> np.ndarray:
    """Minibatch SGD from theta0. With prox_mu > 0 this is the FedProx local solver
    (proximal to `theta_ref`, normally the current global model)."""
    theta = theta0.astype(np.float64).copy()
    ref = theta0 if theta_ref is None else theta_ref
    n = len(y)
    if n == 0:
        return theta
    for _ in range(cfg.epochs):
        idx = rng.permutation(n)
        for s in range(0, n, cfg.batch_size):
            b = idx[s:s + cfg.batch_size]
            _, g = loss_and_grad(theta, X[b], y[b], cfg.l2, cfg.prox_mu, ref)
            theta -= cfg.lr * g
    return theta


# ---------------------------------------------------------------- serialization
def serialize(theta: np.ndarray) -> bytes:
    """65 little-endian fp32: w[0..63] then b."""
    theta = np.asarray(theta)
    if theta.shape != (N_PARAMS,):
        raise ValueError(f"expected shape ({N_PARAMS},), got {theta.shape}")
    if not np.all(np.isfinite(theta)):
        raise ValueError("non-finite parameter")
    return theta.astype("<f4").tobytes()


def deserialize(blob: bytes) -> np.ndarray:
    if len(blob) != WIRE_BYTES:
        raise ValueError(f"expected {WIRE_BYTES} bytes, got {len(blob)}")
    theta = np.frombuffer(blob, dtype="<f4").astype(np.float32)
    if not np.all(np.isfinite(theta)):
        raise ValueError("non-finite parameter")
    return theta


def serialize_struct(theta: np.ndarray) -> bytes:
    """Independent reference encoder (struct module), used by the round-trip test."""
    return struct.pack("<65f", *[float(v) for v in theta])
