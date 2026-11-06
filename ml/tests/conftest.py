import os

import pytest
import torch

torch.set_num_threads(min(4, os.cpu_count() or 1))


@pytest.fixture(scope="session")
def quick_run(tmp_path_factory):
    """The whole pipeline on the 'quick' profile (tiny models / data, ~1-2 min)."""
    from sentinel_ml.pipeline import run

    out = tmp_path_factory.mktemp("quick")
    return run("quick", str(out), log=lambda *a: None), out
