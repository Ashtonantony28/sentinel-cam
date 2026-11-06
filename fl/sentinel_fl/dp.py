"""Differential privacy for DP-FedAvg (user/home-level DP).

Mechanism (INTERFACES.md §5): each client's update is clipped to L2 norm C; the
server adds N(0, sigma^2 C^2 I) to the *sum* of clipped updates; clients are sampled
per round with Poisson probability q. One round is therefore a Poisson-subsampled
Gaussian mechanism with noise multiplier sigma and sensitivity C (add/remove one home).

Accounting: Renyi DP of the subsampled Gaussian at integer orders (Mironov, Talwar,
Zhang 2019, "Renyi Differential Privacy of the Sampled Gaussian Mechanism", §3.3,
exact for integer alpha), composed over T rounds, converted to (eps, delta) with the
tighter conversion of Balle et al. 2020 / Canonne, Kamath, Steinke 2020 (the one
`dp-accounting` uses):

    eps = min_alpha  T*rdp(alpha) + log((alpha-1)/alpha) - (log(delta) + log(alpha)) / (alpha-1)

The implementation is checked against Google's `dp-accounting` RdpAccountant in the tests.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from scipy.special import gammaln, logsumexp

DEFAULT_ORDERS: tuple[int, ...] = tuple(range(2, 65)) + (80, 96, 128, 160, 192, 256, 384, 512, 1024)


def clip_l2(v: np.ndarray, C: float) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n <= C else v * (C / n)


def rdp_sampled_gaussian(q: float, sigma: float, alpha: int) -> float:
    """RDP epsilon at integer order alpha for one Poisson-subsampled Gaussian step.

    A_alpha = sum_k C(alpha,k) (1-q)^(alpha-k) q^k exp((k^2 - k) / (2 sigma^2)),
    rdp = log(A_alpha) / (alpha - 1). Computed in log space.
    """
    if not (0.0 <= q <= 1.0):
        raise ValueError("q must be in [0,1]")
    if sigma <= 0:
        return math.inf
    if alpha < 2 or int(alpha) != alpha:
        raise ValueError("integer alpha >= 2 required")
    if q == 0:
        return 0.0
    if q == 1.0:
        return alpha / (2 * sigma ** 2)
    k = np.arange(alpha + 1, dtype=np.float64)
    log_binom = gammaln(alpha + 1) - gammaln(k + 1) - gammaln(alpha - k + 1)
    terms = log_binom + (alpha - k) * math.log1p(-q) + k * math.log(q) + (k * k - k) / (2 * sigma ** 2)
    return float(logsumexp(terms)) / (alpha - 1)


def rdp_to_eps(orders: Sequence[int], rdp: Sequence[float], delta: float) -> tuple[float, int]:
    """Return (eps, best order)."""
    best = (math.inf, orders[0])
    for a, r in zip(orders, rdp):
        if not math.isfinite(r):
            continue
        eps = r + math.log1p(-1.0 / a) - (math.log(delta) + math.log(a)) / (a - 1)
        if eps < best[0]:
            best = (eps, a)
    return max(best[0], 0.0), best[1]


def epsilon(q: float, sigma: float, steps: int, delta: float,
            orders: Iterable[int] = DEFAULT_ORDERS) -> float:
    orders = list(orders)
    if sigma <= 0:
        return math.inf
    rdp = [steps * rdp_sampled_gaussian(q, sigma, a) for a in orders]
    return rdp_to_eps(orders, rdp, delta)[0]


def epsilon_dp_accounting(q: float, sigma: float, steps: int, delta: float,
                          orders: Iterable[int] = DEFAULT_ORDERS) -> float:
    """Reference value from Google's dp-accounting library (same orders)."""
    import dp_accounting

    acc = dp_accounting.rdp.RdpAccountant(orders=[float(o) for o in orders])
    ev = dp_accounting.PoissonSampledDpEvent(q, dp_accounting.GaussianDpEvent(sigma))
    acc.compose(dp_accounting.SelfComposedDpEvent(ev, steps))
    return float(acc.get_epsilon(delta))


def epsilon_pld(q: float, sigma: float, steps: int, delta: float) -> float:
    """Tighter privacy-loss-distribution accountant (dp-accounting), for comparison."""
    import dp_accounting

    acc = dp_accounting.pld.PLDAccountant()
    ev = dp_accounting.PoissonSampledDpEvent(q, dp_accounting.GaussianDpEvent(sigma))
    acc.compose(dp_accounting.SelfComposedDpEvent(ev, steps))
    return float(acc.get_epsilon(delta))
