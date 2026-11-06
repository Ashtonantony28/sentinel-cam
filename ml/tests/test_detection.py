import numpy as np
import torch

from sentinel_ml.detection import MAX_LOG, build_targets, decode, detection_loss, level_ranges, nms
from sentinel_ml.models import STRIDES, build_face_detector, build_person_detector


def _encode(boxes, size, n_kpt=0, kpts=None):
    """Perfect raw outputs for given GT boxes (inverse of decode at assigned cells)."""
    shapes = [(size // s, size // s) for s in STRIDES]
    t = [{"boxes": torch.tensor(boxes, dtype=torch.float32),
          "kpts": torch.tensor(kpts if kpts is not None else np.zeros((len(boxes), 2 * n_kpt)), dtype=torch.float32)}]
    tg = build_targets(t, shapes, n_kpt=n_kpt)
    outs = []
    for (h, w), s, (idx, gtb, gtk, c) in zip(shapes, STRIDES, tg):
        o = np.zeros((5 + 2 * n_kpt, h * w), np.float32)
        o[0] = -10
        pos = idx[0] >= 0
        if pos.any():
            cc, gb = c[pos], gtb[0][pos]
            ltrb = torch.cat([cc - gb[:, :2], gb[:, 2:] - cc], 1) / s
            o[0, pos.numpy()] = 10
            o[1:5, pos.numpy()] = torch.log(ltrb).T.numpy()
            if n_kpt:
                o[5:, pos.numpy()] = ((gtk[0][pos].reshape(-1, n_kpt, 2) - cc[:, None]) / s).reshape(-1, 2 * n_kpt).T.numpy()
        outs.append(o.reshape(-1, h, w))
    return outs


def test_level_ranges_overlap_and_cover():
    r = level_ranges()
    assert r[0][0] == 0 and r[-1][1] == float("inf")
    for (a, b), (c, d) in zip(r, r[1:]):
        assert c < b  # overlapping


def test_encode_decode_roundtrip():
    boxes = [[10, 20, 40, 120], [100, 30, 250, 250], [200, 200, 210, 215]]
    outs = _encode(boxes, 256)
    b, s, _ = decode(outs, conf_thr=0.5)
    assert len(b) == 3
    order = np.argsort(b[:, 0])
    np.testing.assert_allclose(b[order], np.array(boxes, np.float32)[np.argsort(np.array(boxes)[:, 0])], atol=1e-3)


def test_decode_keypoints_roundtrip():
    boxes = [[40, 40, 100, 110]]
    k = np.array([[55, 60, 85, 60, 70, 75, 58, 95, 82, 95]], np.float32)
    outs = _encode(boxes, 160, n_kpt=5, kpts=k)
    b, s, kp = decode(outs, conf_thr=0.5, n_kpt=5)
    assert len(b) == 1
    np.testing.assert_allclose(kp[0].reshape(-1), k[0], atol=1e-3)


def test_every_gt_gets_a_positive():
    boxes = torch.tensor([[0, 0, 3, 5], [120, 0, 128, 3], [10, 10, 250, 250], [60, 60, 66, 140]], dtype=torch.float32)
    tg = build_targets([{"boxes": boxes, "kpts": torch.zeros(4, 0)}], [(32, 32), (16, 16), (8, 8)])
    for g in range(4):
        assert any((lv[0][0] == g).any() for lv in tg), g


def test_nms():
    b = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [20, 20, 30, 30]], np.float32)
    keep = nms(b, np.array([0.9, 0.8, 0.7]), 0.5)
    assert list(keep) == [0, 2]


def test_loss_finite_with_empty_targets_and_decreases():
    torch.manual_seed(0)
    m = build_person_detector(width=0.5, input_size=128)
    x = torch.rand(4, 3, 128, 128)
    tg = [{"boxes": torch.tensor([[20., 10., 50., 100.]]), "kpts": torch.zeros(1, 0)},
          {"boxes": torch.zeros(0, 4), "kpts": torch.zeros(0, 0)}] * 2
    opt = torch.optim.Adam(m.parameters(), 3e-3)
    losses = []
    for _ in range(25):
        loss, parts = detection_loss(list(m(x)[:3]), tg)
        assert torch.isfinite(loss)
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(float(loss))
    assert losses[-1] < 0.7 * losses[0]


def test_face_loss_with_keypoints():
    m = build_face_detector(width=0.5, input_size=96)
    tg = [{"boxes": torch.tensor([[20., 20., 60., 70.]]), "kpts": torch.rand(1, 10) * 40 + 20}]
    loss, parts = detection_loss(list(m(torch.rand(1, 3, 96, 96))), tg, n_kpt=5)
    assert torch.isfinite(loss) and parts["kpt"] > 0


def test_decode_clamps_exp():
    o = [np.full((5, 32, 32), 100.0, np.float32)] + [np.full((5, 16, 16), -100.0, np.float32),
                                                       np.full((5, 8, 8), -100.0, np.float32)]
    b, s, _ = decode(o, conf_thr=0.5)
    assert np.isfinite(b).all() and (b[:, 2] - b[:, 0]).max() <= 2 * np.exp(MAX_LOG) * 8 + 1
