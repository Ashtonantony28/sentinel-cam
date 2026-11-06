"""Federated training of the false-alert head: local-only, FedAvg, FedProx, DP-FedAvg,
optional secure aggregation, optional per-home fine-tuning.

The logic is split so the same code runs in two orchestrators:

* `run_federated` - a small in-process round loop (used by the experiment grid and tests);
* `flower_app`    - Flower ServerApp/ClientApp that calls the same `client_update` and
                    `ServerState.aggregate` (see that module).

Determinism: the server RNG (client sampling, DP noise) is seeded by (seed, "server"),
and each client's local SGD RNG by (seed, round, home index), so both orchestrators
produce identical models.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

from . import dp, secagg
from .head import N_PARAMS, TrainConfig, predict_proba, train_local
from .homes import Home, World

THRESHOLD = 0.5


@dataclass(frozen=True)
class FedConfig:
    algo: str = "fedavg"             # "local" | "fedavg" | "fedprox"
    rounds: int = 100
    q: float = 0.1                   # Poisson client-sampling probability per round
    local: TrainConfig = TrainConfig()
    server_lr: float = 1.0
    clip: Optional[float] = None     # L2 clip of each client update (INTERFACES §5: C = 1.0)
    noise_multiplier: float = 0.0    # sigma; server adds N(0, (sigma*C)^2) to the clipped sum
    secagg: bool = False
    secagg_t_frac: float = 0.5       # Shamir threshold as a fraction of the round's cohort
    dropout: float = 0.0             # P(sampled client drops after ShareKeys)
    finetune_epochs: int = 0         # personalization: local epochs from the final global model
    local_only_epochs: int = 60
    local_only_l2: float = 1e-2
    delta: float = 1e-4

    @property
    def dp(self) -> bool:
        return self.noise_multiplier > 0

    def label(self) -> str:
        s = {"local": "Local only", "fedavg": "FedAvg", "fedprox": f"FedProx(mu={self.local.prox_mu:g})"}[self.algo]
        if self.clip and not self.dp:
            s += f"+clip{self.clip:g}"
        if self.dp:
            s += f"+DP(sigma={self.noise_multiplier:g})"
        if self.secagg:
            s += "+SecAgg"
        if self.dropout:
            s += f"+drop{self.dropout:.0%}"
        if self.finetune_epochs:
            s += f"+FT{self.finetune_epochs}"
        return s


def client_rng(seed: int, rnd: int, cid: int) -> np.random.Generator:
    return np.random.default_rng([seed, rnd, cid, 7])


def client_update(theta_global: np.ndarray, home: Home, cfg: FedConfig, rng: np.random.Generator) -> np.ndarray:
    """Local training from the global model; returns the update (delta) only - the 65
    floats of INTERFACES §5. Clipping happens here, on the device, before anything leaves it."""
    tc = cfg.local if cfg.algo == "fedprox" else replace(cfg.local, prox_mu=0.0)
    theta = train_local(theta_global, home.X, home.y, tc, rng, theta_ref=theta_global)
    delta = theta - theta_global
    if cfg.clip:
        delta = dp.clip_l2(delta, cfg.clip)
    return delta


def client_payload(delta: np.ndarray, n: int, cfg: FedConfig) -> np.ndarray:
    """What a client feeds into (secure) aggregation.

    Without DP: [n*delta, n] so the server can form the n-weighted FedAvg mean from the sum.
    With clipping/DP: the clipped delta, uniformly weighted (bounded sensitivity)."""
    if cfg.clip:
        return delta
    return np.concatenate([n * delta, [float(n)]])


@dataclass
class RoundLog:
    rnd: int
    cohort: int
    survivors: int
    update_norm: float


@dataclass
class ServerState:
    cfg: FedConfig
    n_clients: int
    seed: int
    theta: np.ndarray = field(default_factory=lambda: np.zeros(N_PARAMS))
    log: list[RoundLog] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng([self.seed, 99])

    def sample(self, rnd: int) -> tuple[list[int], set[int]]:
        """Poisson sampling (each home independently w.p. q), then dropouts."""
        cohort = [i for i in range(self.n_clients) if self.rng.random() < self.cfg.q]
        dropped = {i for i in cohort if self.cfg.dropout and self.rng.random() < self.cfg.dropout}
        return cohort, dropped

    def aggregate(self, rnd: int, payloads: dict[int, np.ndarray], dropped: set[int]) -> None:
        cfg = self.cfg
        survivors = sorted(set(payloads) - dropped)
        dim = N_PARAMS if cfg.clip else N_PARAMS + 1
        total = np.zeros(dim)
        if cfg.secagg:
            t = max(2, int(np.ceil(cfg.secagg_t_frac * len(payloads))))
            # Abort (no client input used) if the cohort is too small to hide individuals
            # or too many dropped; never fall back to a plaintext sum.
            if len(payloads) >= 3 and len(survivors) >= t:
                total, _, _ = secagg.run_protocol(payloads, t=t, drop_after_share=frozenset(dropped), round_id=rnd)
            else:
                survivors = []
        else:
            for i in survivors:
                total = total + payloads[i]
        if cfg.clip:
            if cfg.dp:
                # Noise is drawn every round, including empty or aborted ones, so the released
                # sequence is exactly the mechanism the accountant analyses.
                total = total + self.rng.normal(0.0, cfg.noise_multiplier * cfg.clip, size=N_PARAMS)
                denom = cfg.q * self.n_clients  # fixed denominator keeps sensitivity C / (qN)
            else:
                denom = max(len(survivors), 1)
            step = total / denom
        else:
            step = total[:N_PARAMS] / total[N_PARAMS] if total[N_PARAMS] > 0 else np.zeros(N_PARAMS)
        self.theta = self.theta + cfg.server_lr * step
        self.log.append(RoundLog(rnd, len(payloads), len(survivors), float(np.linalg.norm(step))))

    def epsilon(self) -> float:
        if not self.cfg.dp:
            return float("inf")
        return dp.epsilon(self.cfg.q, self.cfg.noise_multiplier, len(self.log), self.cfg.delta)


def finetune(theta: np.ndarray, home: Home, cfg: FedConfig, seed: int) -> np.ndarray:
    if not cfg.finetune_epochs or home.n == 0:
        return theta
    tc = replace(cfg.local, epochs=cfg.finetune_epochs, prox_mu=0.0)
    return train_local(theta, home.X, home.y, tc, client_rng(seed, 10_000, home.idx))


def run_local_only(world: World, cfg: FedConfig, seed: int) -> list[np.ndarray]:
    tc = replace(cfg.local, epochs=cfg.local_only_epochs, l2=cfg.local_only_l2, prox_mu=0.0)
    return [train_local(np.zeros(N_PARAMS), h.X, h.y, tc, client_rng(seed, 0, h.idx)) for h in world.homes]


def run_federated(world: World, cfg: FedConfig, seed: int) -> tuple[list[np.ndarray], ServerState]:
    """In-process orchestration. Returns per-home models (global, or fine-tuned) and the server."""
    if cfg.algo == "local":
        return run_local_only(world, cfg, seed), ServerState(cfg, len(world.homes), seed)
    if cfg.dp and not cfg.clip:
        raise ValueError("DP requires clipping")
    server = ServerState(cfg, len(world.homes), seed)
    for rnd in range(1, cfg.rounds + 1):
        cohort, dropped = server.sample(rnd)
        payloads = {}
        for cid in cohort:
            h = world.homes[cid]
            delta = client_update(server.theta, h, cfg, client_rng(seed, rnd, cid))
            payloads[cid] = client_payload(delta, h.n, cfg)
        server.aggregate(rnd, payloads, dropped)
    models = [finetune(server.theta, h, cfg, seed) for h in world.homes]
    return models, server


# ---------------------------------------------------------------- evaluation
@dataclass
class HomeMetrics:
    far: np.ndarray       # false-alert rate on nuisance test events, per home
    recall: np.ndarray    # person recall, per home
    auc: np.ndarray       # threshold-free ROC AUC, per home
    n_train: np.ndarray
    quirk: np.ndarray

    def summary(self, small: int = 20) -> dict:
        s = self.n_train < small
        qk = self.quirk
        return {
            "far_mean": float(self.far.mean()), "recall_mean": float(self.recall.mean()),
            "auc_mean": float(self.auc.mean()),
            "far_quirk": float(self.far[qk].mean()) if qk.any() else float("nan"),
            "far_p90": float(np.quantile(self.far, 0.9)), "recall_p10": float(np.quantile(self.recall, 0.1)),
            "far_small": float(self.far[s].mean()) if s.any() else float("nan"),
            "recall_small": float(self.recall[s].mean()) if s.any() else float("nan"),
            "n_small": int(s.sum()),
        }


def roc_auc(score: np.ndarray, y: np.ndarray) -> float:
    """Mann-Whitney AUC with tie handling."""
    from scipy.stats import rankdata

    pos = y == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    r = rankdata(score)
    return float((r[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def evaluate(models: list[np.ndarray], world: World) -> HomeMetrics:
    far, rec, auc, n, qk = [], [], [], [], []
    for theta, h in zip(models, world.homes):
        p = predict_proba(theta, h.X_test)
        pred = p >= THRESHOLD
        neg, pos = h.y_test == 0, h.y_test == 1
        far.append(pred[neg].mean())
        rec.append(pred[pos].mean())
        auc.append(roc_auc(p, h.y_test))
        n.append(h.n)
        qk.append(h.has_quirk)
    return HomeMetrics(np.array(far), np.array(rec), np.array(auc), np.array(n), np.array(qk))
