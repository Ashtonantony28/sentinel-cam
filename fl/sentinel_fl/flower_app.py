"""Flower (flwr >= 1.20 Message API) orchestration of the same algorithms.

The ServerApp drives rounds through Flower's `Grid` (send_and_receive) and calls
`ServerState.sample/aggregate`. The ClientApp runs `client_update` + `client_payload`
on its home's data. Each SuperNode is one home: in simulation the home index is the
node's `partition-id`. On real devices the ClientApp is replaced by firmware speaking
the same message shape (65 floats in, 65 floats out), see README.

Why the experiment grid does not go through Flower: a Ray-backed Flower simulation
costs ~8 s to start and adds per-message overhead; the grid is 10 configs x 3 seeds x
100 rounds x ~30 clients. The algorithm code is shared, and `test_flower.py` checks
that a Flower run yields exactly the same global model as the in-process loop.

Secure aggregation is not wired through Flower messages here (its four rounds run
through `secagg.run_protocol` with the same client/server classes); configs with
`secagg=True` are rejected by this adapter rather than silently sending plaintext.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional

import numpy as np
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, RecordDict
from flwr.clientapp import ClientApp
from flwr.serverapp import Grid, ServerApp

from .fed import FedConfig, ServerState, client_payload, client_rng, client_update
from .homes import WorldConfig, make_world


@lru_cache(maxsize=4)
def _world(n_homes: int, seed: int):
    return make_world(n_homes, seed, WorldConfig())


def make_client_app(n_homes: int, seed: int, cfg: FedConfig) -> ClientApp:
    app = ClientApp()

    @app.query()
    def whoami(msg: Message, context: Context) -> Message:
        pid = int(context.node_config["partition-id"])
        return Message(content=RecordDict({"info": ConfigRecord({"home": pid})}), reply_to=msg)

    @app.train()
    def train(msg: Message, context: Context) -> Message:
        home = _world(n_homes, seed).homes[int(context.node_config["partition-id"])]
        theta = msg.content["arrays"].to_numpy_ndarrays()[0].astype(np.float64)
        rnd = int(msg.content["config"]["round"])
        delta = client_update(theta, home, cfg, client_rng(seed, rnd, home.idx))
        payload = client_payload(delta, home.n, cfg)
        return Message(content=RecordDict({"arrays": ArrayRecord([payload]),
                                           "config": ConfigRecord({"home": home.idx})}), reply_to=msg)

    return app


def make_server_app(n_homes: int, seed: int, cfg: FedConfig, result: dict) -> ServerApp:
    if cfg.secagg:
        raise ValueError("secagg is not wired through Flower messages in this adapter")
    if cfg.algo == "local":
        raise ValueError("local-only needs no server")
    app = ServerApp()

    @app.main()
    def main(grid: Grid, context: Context) -> None:
        t0 = time.time()
        nodes = list(grid.get_node_ids())
        while len(nodes) < n_homes and time.time() - t0 < 60:
            time.sleep(0.1)
            nodes = list(grid.get_node_ids())
        q = [Message(content=RecordDict({}), message_type="query", dst_node_id=n) for n in nodes]
        node_of = {int(r.content["info"]["home"]): r.metadata.src_node_id for r in grid.send_and_receive(q)}
        server = ServerState(cfg, n_homes, seed)
        for rnd in range(1, cfg.rounds + 1):
            cohort, dropped = server.sample(rnd)
            msgs = [Message(content=RecordDict({"arrays": ArrayRecord([server.theta.copy()]),
                                                "config": ConfigRecord({"round": rnd})}),
                            message_type="train", dst_node_id=node_of[h]) for h in cohort]
            payloads = {}
            for r in grid.send_and_receive(msgs):
                if r.has_error():
                    continue  # a failed client is treated as a dropout
                payloads[int(r.content["config"]["home"])] = r.content["arrays"].to_numpy_ndarrays()[0]
            server.aggregate(rnd, payloads, dropped)
        result["theta"] = server.theta.copy()
        result["server"] = server

    return app


def run(n_homes: int, seed: int, cfg: FedConfig, backend_config: Optional[dict] = None) -> dict:
    from flwr.simulation import run_simulation

    result: dict = {}
    run_simulation(
        server_app=make_server_app(n_homes, seed, cfg, result),
        client_app=make_client_app(n_homes, seed, cfg),
        num_supernodes=n_homes,
        backend_config=backend_config or {"client_resources": {"num_cpus": 1, "num_gpus": 0.0}},
    )
    return result
