"""Target assignment, losses and (numpy) decoding for the anchor-free detectors.

Decoding is the reference for the firmware post-processing, which runs on the
M55 after the NPU: per level, per cell ``(i, j)`` with centre
``c = ((j + 0.5) * s, (i + 0.5) * s)``::

    score = sigmoid(out[0])
    l, t, r, b = exp(clamp(out[1:5], max=6)) * s
    box = (cx - l, cy - t, cx + r, cy + b)
    kpt_k = c + out[5 + 2k : 7 + 2k] * s

then NMS (IoU 0.5).
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

from .models.detector import STRIDES

MAX_LOG = 6.0


def level_ranges(strides=STRIDES):
    """Max-distance ranges per level, proportional to stride, overlapping:
    s8 (0, 40], s16 (32, 80], s32 (64, inf)."""
    r = []
    for i, s in enumerate(strides):
        lo = 0.0 if i == 0 else 2.0 * s
        hi = float("inf") if i == len(strides) - 1 else 5.0 * s
        r.append((lo, hi))
    return r


def grid_centres(h, w, s, device=None):
    ys, xs = torch.meshgrid(torch.arange(h, device=device), torch.arange(w, device=device), indexing="ij")
    return torch.stack([(xs + 0.5) * s, (ys + 0.5) * s], -1).reshape(-1, 2).float()


def assign_level(boxes, centres, s, rng, radius=2.5):
    """Return (gt_index per cell or -1). boxes [G,4], centres [P,2]."""
    P = centres.shape[0]
    if boxes.numel() == 0:
        return torch.full((P,), -1, dtype=torch.long)
    cx, cy = centres[:, 0:1], centres[:, 1:2]
    l, t = cx - boxes[None, :, 0], cy - boxes[None, :, 1]
    r, b = boxes[None, :, 2] - cx, boxes[None, :, 3] - cy
    ltrb = torch.stack([l, t, r, b], -1)  # [P,G,4]
    inside = ltrb.min(-1).values > 0
    bcx, bcy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
    near = ((cx - bcx[None]).abs() < radius * s) & ((cy - bcy[None]).abs() < radius * s)
    m = ltrb.max(-1).values
    in_range = (m > rng[0]) & (m <= rng[1])
    ok = inside & near & in_range
    area = ((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]))[None].expand(P, -1)
    area = torch.where(ok, area, torch.full_like(area, float("inf")))
    a, idx = area.min(1)
    idx[torch.isinf(a)] = -1
    return idx


def build_targets(targets, shapes, strides=STRIDES, n_kpt=0):
    """targets: list of dicts with 'boxes' [G,4] and 'kpts' [G,2*n_kpt].
    shapes: list of (H, W) per level. Returns per-level (assign idx [B,P], gt boxes [B,P,4], kpts)."""
    ranges = level_ranges(strides)
    out = []
    for (h, w), s, rg in zip(shapes, strides, ranges):
        c = grid_centres(h, w, s)
        idxs, gtb, gtk = [], [], []
        for t in targets:
            idx = assign_level(t["boxes"], c, s, rg)
            idxs.append(idx)
            safe = idx.clamp(min=0)
            gtb.append(t["boxes"][safe] if len(t["boxes"]) else torch.zeros(len(idx), 4))
            gtk.append(t["kpts"][safe] if (n_kpt and len(t["boxes"])) else torch.zeros(len(idx), 2 * n_kpt))
        out.append((torch.stack(idxs), torch.stack(gtb), torch.stack(gtk), c))
    # guarantee >= 1 positive per GT: if a GT got no cell on any level, force the cell
    # containing its centre on the level whose range fits best
    for bi, t in enumerate(targets):
        for g in range(len(t["boxes"])):
            if any((lv[0][bi] == g).any() for lv in out):
                continue
            box = t["boxes"][g]
            half = float(torch.max(box[2:] - box[:2])) / 2
            li = min(range(len(strides)), key=lambda k: abs(np.log((half + 1e-3) / (2.5 * strides[k]))))
            (h, w), s = shapes[li], strides[li]
            cx, cy = float(box[0] + box[2]) / 2, float(box[1] + box[3]) / 2
            j, i = min(int(cx // s), w - 1), min(int(cy // s), h - 1)
            p = i * w + j
            idx, gtb, gtk, _ = out[li]
            idx[bi, p] = g
            gtb[bi, p] = box
            if n_kpt:
                gtk[bi, p] = t["kpts"][g]
    return out


def sigmoid_focal(logits, target, alpha=0.25, gamma=2.0):
    p = torch.sigmoid(logits)
    ce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    pt = p * target + (1 - p) * (1 - target)
    a = alpha * target + (1 - alpha) * (1 - target)
    return (a * ce * (1 - pt) ** gamma).sum()


def giou_loss(pred, gt):
    ix0, iy0 = torch.max(pred[:, 0], gt[:, 0]), torch.max(pred[:, 1], gt[:, 1])
    ix1, iy1 = torch.min(pred[:, 2], gt[:, 2]), torch.min(pred[:, 3], gt[:, 3])
    inter = (ix1 - ix0).clamp(0) * (iy1 - iy0).clamp(0)
    ap = (pred[:, 2] - pred[:, 0]) * (pred[:, 3] - pred[:, 1])
    ag = (gt[:, 2] - gt[:, 0]) * (gt[:, 3] - gt[:, 1])
    union = ap + ag - inter + 1e-6
    iou = inter / union
    cx0, cy0 = torch.min(pred[:, 0], gt[:, 0]), torch.min(pred[:, 1], gt[:, 1])
    cx1, cy1 = torch.max(pred[:, 2], gt[:, 2]), torch.max(pred[:, 3], gt[:, 3])
    carea = (cx1 - cx0) * (cy1 - cy0) + 1e-6
    return (1 - iou + (carea - union) / carea).sum()


def detection_loss(outs, targets, n_kpt=0, strides=STRIDES):
    shapes = [o.shape[-2:] for o in outs]
    tg = build_targets(targets, shapes, strides, n_kpt)
    l_obj = l_box = l_kpt = 0.0
    npos = 0
    for o, s, (idx, gtb, gtk, c) in zip(outs, strides, tg):
        B, C, H, W = o.shape
        o = o.permute(0, 2, 3, 1).reshape(B, H * W, C)
        pos = idx >= 0
        l_obj = l_obj + sigmoid_focal(o[..., 0], pos.float())
        if pos.any():
            pp = o[pos]
            cc = c[None].expand(B, -1, -1)[pos]
            d = torch.exp(pp[:, 1:5].clamp(max=MAX_LOG)) * s
            pb = torch.cat([cc - d[:, :2], cc + d[:, 2:]], 1)
            l_box = l_box + giou_loss(pb, gtb[pos])
            if n_kpt:
                tk = (gtk[pos].reshape(-1, n_kpt, 2) - cc[:, None]) / s
                l_kpt = l_kpt + F.smooth_l1_loss(pp[:, 5:].reshape(-1, n_kpt, 2), tk, reduction="sum") / n_kpt
            npos += int(pos.sum())
    npos = max(npos, 1)
    f = lambda v: float(v.detach()) if torch.is_tensor(v) else float(v)  # noqa: E731
    return (l_obj + 2.0 * l_box + 0.5 * l_kpt) / npos, {"obj": f(l_obj) / npos, "box": f(l_box) / npos,
                                                         "kpt": f(l_kpt) / npos}


# ---------------------------------------------------------------- decoding (numpy)

def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = 0.5) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        iou = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return np.array(keep, dtype=np.int64)


def decode(level_outs, strides=STRIDES, conf_thr=0.05, iou_thr=0.5, n_kpt=0, max_det=100):
    """level_outs: list of numpy [C,H,W] (one image). Returns boxes [K,4], scores [K], kpts [K,n_kpt,2]."""
    bs, ss, ks = [], [], []
    for o, s in zip(level_outs, strides):
        C, H, W = o.shape
        o = o.reshape(C, -1).T
        score = 1.0 / (1.0 + np.exp(-np.clip(o[:, 0], -60, 60)))
        m = score >= conf_thr
        if not m.any():
            continue
        ys, xs = np.divmod(np.nonzero(m)[0], W)
        cx, cy = (xs + 0.5) * s, (ys + 0.5) * s
        d = np.exp(np.minimum(o[m, 1:5], MAX_LOG)) * s
        bs.append(np.stack([cx - d[:, 0], cy - d[:, 1], cx + d[:, 2], cy + d[:, 3]], 1))
        ss.append(score[m])
        if n_kpt:
            k = o[m, 5:5 + 2 * n_kpt].reshape(-1, n_kpt, 2) * s
            ks.append(k + np.stack([cx, cy], 1)[:, None])
    if not bs:
        return np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros((0, n_kpt, 2), np.float32)
    b, sc = np.concatenate(bs), np.concatenate(ss)
    k = np.concatenate(ks) if n_kpt else np.zeros((len(b), 0, 2), np.float32)
    keep = nms(b, sc, iou_thr)[:max_det]
    return b[keep].astype(np.float32), sc[keep].astype(np.float32), k[keep].astype(np.float32)
