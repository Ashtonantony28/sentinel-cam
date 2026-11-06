"""The committed golden vectors (ml/golden/) are what the C firmware tests against.
These tests check they are (1) reproducible from the generator, byte for byte, and
(2) self-consistent with the reference implementation when read back from disk."""
import csv
import filecmp
import json
import os

import numpy as np
import pytest

from sentinel_ml import gallery as G
from sentinel_ml.golden import generate

GOLDEN = os.path.join(os.path.dirname(__file__), "..", "golden")


def test_golden_is_reproducible(tmp_path):
    m = generate(str(tmp_path))
    committed = json.load(open(os.path.join(GOLDEN, "MANIFEST.json")))
    assert m["sha256"] == committed["sha256"], "golden/ is stale: run `make golden` and commit"
    for name in m["sha256"]:
        assert filecmp.cmp(tmp_path / name, os.path.join(GOLDEN, name), shallow=False), name


def test_golden_decisions_from_disk():
    g = G.read_blob(open(os.path.join(GOLDEN, "gallery_v1.sgal"), "rb").read())
    qf = np.fromfile(os.path.join(GOLDEN, "queries_f32.bin"), "<f4").reshape(-1, 128)
    qi = np.fromfile(os.path.join(GOLDEN, "queries_i8.bin"), np.int8).reshape(-1, 128)
    np.testing.assert_array_equal(G.quantize_embedding(qf), qi)
    rows = list(csv.DictReader(open(os.path.join(GOLDEN, "expected.csv"))))
    assert len(rows) == len(qi) >= 50
    cases = {r["case"] for r in rows}
    assert {"known_close", "margin_fail_mix", "strict_threshold_reject", "lenient_threshold_accept",
            "exact_tie_twins", "zero_vector"} <= cases
    n_known = 0
    for r, q in zip(rows, qi):
        res = g.match(q)
        assert (-1 if res.person_id is None else res.person_id) == int(r["match_id"]), r
        assert res.best_score == pytest.approx(float(r["best_score"]), abs=1e-6)
        assert res.second_score == pytest.approx(float(r["second_score"]), abs=1e-6)
        n_known += res.person_id is not None
    assert 10 <= n_known <= len(rows) - 10  # both outcomes well represented


def test_golden_scores_float32_vs_float64():
    """A float32 C implementation must land on the same decisions (scores within 1e-5)."""
    g = G.read_blob(open(os.path.join(GOLDEN, "gallery_v1.sgal"), "rb").read())
    qi = np.fromfile(os.path.join(GOLDEN, "queries_i8.bin"), np.int8).reshape(-1, 128)
    for q in qi:
        for p in g.people:
            for pr in p.protos:
                a, b = q.astype(np.int32), pr.astype(np.int32)
                na, nb = np.float32(np.sqrt(np.float32(a @ a))), np.float32(np.sqrt(np.float32(b @ b)))
                f32 = 0.0 if na == 0 or nb == 0 else np.float32(a @ b) / (na * nb)
                assert abs(float(f32) - G.cosine_int8(q, pr)) < 1e-5


def test_golden_quant_vectors():
    rows = list(csv.DictReader(open(os.path.join(GOLDEN, "quant_vectors.csv"))))
    xs = np.array([np.frombuffer(bytes.fromhex(r["x_f32_hex"])[::-1], "<f4")[0] for r in rows], np.float32)
    assert list(G.quantize_embedding(xs)) == [int(r["expected_int8"]) for r in rows]
    assert any(int(r["expected_int8"]) == 64 for r in rows)  # 63.5 → 64 (half away from zero)


def test_golden_bad_blobs():
    rows = list(csv.DictReader(open(os.path.join(GOLDEN, "bad", "expected_errors.csv"))))
    assert len(rows) >= 9
    for r in rows:
        with pytest.raises(G.GalleryError) as e:
            G.read_blob(open(os.path.join(GOLDEN, "bad", r["file"]), "rb").read())
        assert e.value.code == r["error"], r["file"]
