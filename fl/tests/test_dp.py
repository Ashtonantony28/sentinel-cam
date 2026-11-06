import math

import numpy as np
import pytest
from scipy.optimize import brentq
from scipy.stats import norm

from sentinel_fl import dp


def test_clip_l2():
    v = np.array([3.0, 4.0])
    assert np.allclose(dp.clip_l2(v, 1.0), [0.6, 0.8])
    assert np.array_equal(dp.clip_l2(v, 10.0), v)
    assert np.linalg.norm(dp.clip_l2(np.random.default_rng(0).normal(size=65) * 50, 1.0)) == pytest.approx(1.0)


@pytest.mark.parametrize("alpha", [2, 3, 10, 64])
@pytest.mark.parametrize("sigma", [0.5, 1.0, 3.0])
def test_unsampled_gaussian_rdp_closed_form(alpha, sigma):
    # Mironov 2017, Prop. 7: Gaussian mechanism with sensitivity 1 has RDP alpha / (2 sigma^2).
    assert dp.rdp_sampled_gaussian(1.0, sigma, alpha) == pytest.approx(alpha / (2 * sigma ** 2))


def test_hand_computed_value():
    # q=0.5, sigma=1, alpha=2: A = 0.25*e^0 + 2*0.25*e^0 + 0.25*e^1 = 0.75 + 0.25e
    assert dp.rdp_sampled_gaussian(0.5, 1.0, 2) == pytest.approx(math.log(0.75 + 0.25 * math.e))


def test_q_zero_is_free_and_sigma_zero_is_infinite():
    assert dp.rdp_sampled_gaussian(0.0, 1.0, 5) == 0.0
    assert dp.epsilon(0.1, 0.0, 10, 1e-5) == math.inf


@pytest.mark.parametrize("q,sigma,steps,delta", [
    (0.01, 1.1, 10_000, 1e-5),   # DP-SGD-like regime
    (0.1, 1.0, 100, 1e-4),       # this project's DP-FedAvg default
    (0.1, 2.0, 100, 1e-4),
    (0.05, 0.7, 500, 1e-5),
    (1.0, 4.0, 50, 1e-6),
])
def test_matches_google_dp_accounting(q, sigma, steps, delta):
    ours = dp.epsilon(q, sigma, steps, delta)
    ref = dp.epsilon_dp_accounting(q, sigma, steps, delta)
    assert ours == pytest.approx(ref, rel=1e-6)
    # RDP is an upper bound; the PLD accountant is (near-)exact and must not exceed it.
    assert dp.epsilon_pld(q, sigma, steps, delta) <= ours * 1.001


def _exact_gaussian_eps(mu: float, delta: float) -> float:
    """Exact (eps, delta) of a Gaussian mechanism with sensitivity/sigma = mu
    (Balle & Wang 2018, Thm 8): delta(eps) = Phi(mu/2 - eps/mu) - e^eps Phi(-mu/2 - eps/mu)."""
    f = lambda e: norm.cdf(mu / 2 - e / mu) - math.exp(e) * norm.cdf(-mu / 2 - e / mu) - delta  # noqa: E731
    return brentq(f, 0.0, 100.0)


@pytest.mark.parametrize("sigma,steps", [(2.0, 10), (5.0, 100), (1.0, 1)])
def test_known_value_full_participation(sigma, steps):
    # T compositions of a Gaussian with noise sigma = one Gaussian with mu = sqrt(T)/sigma.
    delta = 1e-5
    exact = _exact_gaussian_eps(math.sqrt(steps) / sigma, delta)
    ours = dp.epsilon(1.0, sigma, steps, delta)
    assert exact <= ours <= 1.3 * exact + 0.1


def test_monotonicity():
    e = lambda **kw: dp.epsilon(**{**dict(q=0.1, sigma=1.0, steps=100, delta=1e-5), **kw})  # noqa: E731
    assert e(sigma=2.0) < e() < e(sigma=0.7)
    assert e(steps=50) < e() < e(steps=200)
    assert e(q=0.05) < e() < e(q=0.2)
    assert e(delta=1e-3) < e()
