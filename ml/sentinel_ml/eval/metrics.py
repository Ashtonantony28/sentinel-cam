"""Detection mAP@0.5 (VOC all-point interpolation, single class) and face
verification TAR@FAR."""
from __future__ import annotations

import numpy as np


def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), np.float32)
    x0 = np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (aa[:, None] + ab[None] - inter + 1e-9)


def average_precision(dets, gts, iou_thr: float = 0.5) -> float:
    """dets: list per image of (boxes [K,4], scores [K]); gts: list per image of boxes [G,4].
    Returns AP for one class (VOC2010+ all-point interpolated). No GT at all → nan."""
    n_gt = sum(len(g) for g in gts)
    recs = []
    for img_i, (b, s) in enumerate(dets):
        for k in range(len(s)):
            recs.append((float(s[k]), img_i, k))
    if n_gt == 0:
        return float("nan")
    recs.sort(key=lambda r: -r[0])
    used = [np.zeros(len(g), bool) for g in gts]
    tp = np.zeros(len(recs))
    for r, (_, img_i, k) in enumerate(recs):
        g = gts[img_i]
        if len(g) == 0:
            continue
        ious = box_iou(dets[img_i][0][k:k + 1], g)[0]
        ious[used[img_i]] = -1  # VOC: a GT matches at most one detection
        j = int(np.argmax(ious))
        if ious[j] >= iou_thr:
            tp[r] = 1
            used[img_i][j] = True
    fp = 1 - tp
    ctp, cfp = np.cumsum(tp), np.cumsum(fp)
    recall = ctp / n_gt
    precision = ctp / np.maximum(ctp + cfp, 1e-12)
    mrec = np.concatenate([[0.0], recall, [1.0]])
    mpre = np.concatenate([[0.0], precision, [0.0]])
    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])
    idx = np.nonzero(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def tar_at_far(genuine: np.ndarray, impostor: np.ndarray, far: float):
    """Threshold = smallest score such that the fraction of impostor scores >= threshold
    is <= ``far``; returns (TAR, threshold, achieved FAR)."""
    genuine, impostor = np.asarray(genuine, np.float64), np.asarray(impostor, np.float64)
    imp = np.sort(impostor)[::-1]
    k = int(np.floor(far * len(imp)))  # number of impostors allowed at or above thr
    # threshold just above the (k+1)-th largest impostor score
    thr = np.nextafter(imp[k], np.inf) if k < len(imp) else -np.inf
    tar = float(np.mean(genuine >= thr))
    achieved = float(np.mean(impostor >= thr))
    return tar, float(thr), achieved


def verification_pairs(emb: np.ndarray, labels: np.ndarray, max_impostor: int = 200_000, seed: int = 0):
    """All genuine pairs + up to ``max_impostor`` random impostor pairs; cosine scores."""
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    S = emb @ emb.T
    iu = np.triu_indices(len(labels), 1)
    same = labels[iu[0]] == labels[iu[1]]
    gen = S[iu][same]
    imp = S[iu][~same]
    if len(imp) > max_impostor:
        imp = np.random.default_rng(seed).choice(imp, max_impostor, replace=False)
    return gen, imp
