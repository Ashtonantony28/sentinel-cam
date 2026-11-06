"""Flower orchestration produces exactly the model of the in-process loop."""
from dataclasses import replace

import numpy as np
import pytest

from sentinel_fl.fed import FedConfig, run_federated
from sentinel_fl.head import TrainConfig

pytestmark = pytest.mark.flower


def test_flower_parity_dp_fedavg():
    try:
        import ray  # noqa: F401
        from sentinel_fl import flower_app
    except Exception as e:  # pragma: no cover
        pytest.skip(f"flwr[simulation] unavailable: {e}")
    cfg = FedConfig(rounds=3, q=0.5, local=TrainConfig(epochs=2, lr=0.1), clip=1.0, noise_multiplier=1.0)
    res = flower_app.run(6, 5, cfg)
    _, server = run_federated(flower_app._world(6, 5), cfg, 5)
    assert "theta" in res, "Flower ServerApp did not finish"
    assert np.array_equal(res["theta"], server.theta)
    assert [(r.cohort, r.survivors) for r in res["server"].log] == [(r.cohort, r.survivors) for r in server.log]


def test_flower_adapter_refuses_plaintext_secagg():
    from sentinel_fl import flower_app

    with pytest.raises(ValueError):
        flower_app.make_server_app(4, 0, FedConfig(secagg=True), {})
