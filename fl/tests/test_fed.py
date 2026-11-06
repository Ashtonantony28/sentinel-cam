from dataclasses import replace

import numpy as np
import pytest

from sentinel_fl import dp
from sentinel_fl.fed import (FedConfig, ServerState, client_payload, client_rng, client_update, evaluate, roc_auc,
                             run_federated)
from sentinel_fl.head import TrainConfig, serialize, deserialize
from sentinel_fl.homes import make_world

BASE = FedConfig(rounds=30, q=0.2, local=TrainConfig(epochs=3, lr=0.1, l2=1e-3))


@pytest.fixture(scope="module")
def world():
    return make_world(60, 11)


def test_fedavg_aggregate_is_sample_weighted_mean():
    s = ServerState(replace(BASE, server_lr=1.0), 3, 0)
    d = {0: np.ones(65), 1: 2 * np.ones(65), 2: 10 * np.ones(65)}
    n = {0: 10, 1: 30, 2: 0}
    s.aggregate(1, {i: client_payload(d[i], n[i], s.cfg) for i in d}, dropped=set())
    assert np.allclose(s.theta, (10 * 1 + 30 * 2) / 40)


def test_dropped_clients_excluded():
    s = ServerState(BASE, 3, 0)
    s.aggregate(1, {0: client_payload(np.ones(65), 5, BASE), 1: client_payload(9 * np.ones(65), 5, BASE)}, dropped={1})
    assert np.allclose(s.theta, 1.0)


def test_client_update_is_clipped(world):
    cfg = replace(BASE, clip=1.0, local=replace(BASE.local, lr=2.0, epochs=10))
    norms = [np.linalg.norm(client_update(np.zeros(65), h, cfg, client_rng(0, 1, h.idx))) for h in world.homes[:20]]
    assert max(norms) <= 1.0 + 1e-9 and max(norms) > 0.99  # clipping actually binds


def test_dp_noise_scale():
    cfg = replace(BASE, clip=1.0, noise_multiplier=2.0, q=0.1)
    s = ServerState(cfg, 50, 0)  # expected cohort qN = 5
    steps = []
    for r in range(400):
        before = s.theta.copy()
        s.aggregate(r, {0: np.zeros(65)}, set())
        steps.append(s.theta - before)
    std = np.std(np.concatenate(steps))
    assert std == pytest.approx(2.0 * 1.0 / 5, rel=0.05)


def test_dp_requires_clip(world):
    with pytest.raises(ValueError):
        run_federated(world, replace(BASE, noise_multiplier=1.0), 0)


def test_fedprox_limits_client_drift(world):
    h = max(world.homes, key=lambda h: h.n)
    g = np.zeros(65)
    long = replace(BASE.local, epochs=20)
    d_avg = client_update(g, h, replace(BASE, algo="fedavg", local=long), client_rng(0, 1, 0))
    d_prox = client_update(g, h, replace(BASE, algo="fedprox", local=replace(long, prox_mu=1.0)), client_rng(0, 1, 0))
    assert np.linalg.norm(d_prox) < 0.8 * np.linalg.norm(d_avg)


def test_deterministic(world):
    a = run_federated(world, BASE, 3)[1].theta
    b = run_federated(world, BASE, 3)[1].theta
    assert np.array_equal(a, b)


@pytest.mark.parametrize("cfg", [BASE, replace(BASE, clip=1.0, noise_multiplier=1.0)])
def test_secagg_gives_same_model_as_plain_sum(world, cfg):
    cfg = replace(cfg, rounds=5)
    plain = run_federated(world, cfg, 2)[1].theta
    sec = run_federated(world, replace(cfg, secagg=True), 2)[1].theta
    assert np.allclose(plain, sec, atol=1e-5)


def test_secagg_with_dropouts_trains(world):
    cfg = replace(BASE, rounds=10, secagg=True, dropout=0.2)
    _, s = run_federated(world, cfg, 4)
    assert any(r.survivors < r.cohort for r in s.log)
    assert np.all(np.isfinite(s.theta)) and np.linalg.norm(s.theta) > 0


def test_epsilon_reported(world):
    cfg = replace(BASE, clip=1.0, noise_multiplier=1.0, rounds=10)
    _, s = run_federated(world, cfg, 0)
    assert s.epsilon() == pytest.approx(dp.epsilon(cfg.q, 1.0, 10, cfg.delta))
    assert run_federated(world, BASE, 0)[1].epsilon() == float("inf")


def test_auc():
    assert roc_auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1])) == 1.0
    assert roc_auc(np.array([0.5, 0.5]), np.array([0, 1])) == 0.5


def test_federation_beats_local_only(world):
    """The research question in miniature: on non-IID homes, FedAvg's global head
    has better ranking (AUC) and recall than each home learning alone, and the
    biggest gain is on data-poor homes."""
    loc = evaluate(run_federated(world, replace(BASE, algo="local"), 0)[0], world).summary()
    fed = evaluate(run_federated(world, BASE, 0)[0], world).summary()
    assert fed["auc_mean"] > loc["auc_mean"] + 0.03
    assert fed["recall_mean"] > loc["recall_mean"] + 0.1
    assert fed["recall_small"] - loc["recall_small"] > fed["recall_mean"] - loc["recall_mean"] - 0.05


def test_finetuning_reduces_false_alerts(world):
    fed = evaluate(run_federated(world, BASE, 0)[0], world).summary()
    ft = evaluate(run_federated(world, replace(BASE, finetune_epochs=5), 0)[0], world).summary()
    assert ft["far_mean"] < fed["far_mean"]


def test_global_model_serializes_for_ota(world):
    theta = run_federated(world, BASE, 0)[1].theta
    back = deserialize(serialize(theta))
    assert np.allclose(back, theta, rtol=1e-6, atol=1e-6)
    # The fp32 model a device would load behaves like the fp64 one.
    m64 = evaluate([theta] * len(world.homes), world).summary()
    m32 = evaluate([back.astype(np.float64)] * len(world.homes), world).summary()
    assert abs(m64["far_mean"] - m32["far_mean"]) < 1e-3


def test_secagg_never_aggregates_tiny_cohort_in_plaintext():
    cfg = replace(BASE, secagg=True)
    s = ServerState(cfg, 10, 0)
    s.aggregate(1, {0: client_payload(np.ones(65), 5, cfg), 1: client_payload(np.ones(65), 5, cfg)}, set())
    assert np.array_equal(s.theta, np.zeros(65)) and s.log[-1].survivors == 0


def test_dp_round_with_no_clients_still_releases_noise():
    cfg = replace(BASE, clip=1.0, noise_multiplier=1.0)
    s = ServerState(cfg, 10, 0)
    s.aggregate(1, {}, set())
    assert np.linalg.norm(s.theta) > 0
    s2 = ServerState(replace(cfg, secagg=True), 10, 0)
    s2.aggregate(1, {0: np.ones(65), 1: np.ones(65)}, set())  # aborted SecAgg round
    assert np.linalg.norm(s2.theta) > 0 and s2.log[-1].survivors == 0
