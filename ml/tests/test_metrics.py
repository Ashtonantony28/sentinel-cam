import numpy as np
import pytest

from sentinel_ml.eval.metrics import average_precision, box_iou, tar_at_far, verification_pairs


def test_box_iou_basic():
    a = np.array([[0, 0, 10, 10]], np.float32)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], np.float32)
    np.testing.assert_allclose(box_iou(a, b)[0], [1.0, 1 / 3, 0.0], atol=1e-6)


def test_ap_perfect_and_empty():
    gts = [np.array([[0, 0, 10, 10]]), np.array([[5, 5, 20, 20], [30, 30, 40, 40]])]
    dets = [(g.astype(np.float32), np.ones(len(g))) for g in gts]
    assert average_precision(dets, gts) == pytest.approx(1.0)
    assert average_precision([(np.zeros((0, 4)), np.zeros(0))] * 2, gts) == 0.0
    assert np.isnan(average_precision(dets, [np.zeros((0, 4))] * 2))


def test_ap_ranked_fp_first():
    # one GT; a high-scoring FP then the TP → precision at recall 1 is 0.5
    gts = [np.array([[0, 0, 10, 10]])]
    dets = [(np.array([[50, 50, 60, 60], [0, 0, 10, 10]], np.float32), np.array([0.9, 0.8]))]
    assert average_precision(dets, gts) == pytest.approx(0.5)


def test_ap_duplicate_detection_is_fp():
    gts = [np.array([[0, 0, 10, 10]])]
    dets = [(np.array([[0, 0, 10, 10], [0, 0, 10, 10]], np.float32), np.array([0.9, 0.8]))]
    assert average_precision(dets, gts) == pytest.approx(1.0)  # FP after full recall doesn't hurt AP
    dets = [(np.array([[0, 0, 10, 10], [1, 1, 10, 10]], np.float32), np.array([0.9, 0.8]))]
    gts2 = [np.array([[0, 0, 10, 10], [100, 100, 110, 110]])]
    assert average_precision(dets, gts2) == pytest.approx(0.5)


def test_tar_at_far():
    imp = np.linspace(0, 0.5, 1000)
    gen = np.array([0.45, 0.6, 0.7, 0.2])
    tar, thr, far = tar_at_far(gen, imp, 0.01)
    assert far <= 0.01
    assert 0.49 < thr <= 0.5 + 1e-9
    assert tar == pytest.approx(0.5)
    tar0, thr0, far0 = tar_at_far(gen, imp, 0.0)
    assert far0 == 0.0 and thr0 > 0.5


def test_verification_pairs_counts():
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(6, 8))
    labels = np.array([0, 0, 0, 1, 1, 2])
    g, i = verification_pairs(emb, labels)
    assert len(g) == 3 + 1 and len(i) == 15 - 4
