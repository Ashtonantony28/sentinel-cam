"""Training / evaluation loops shared by the CLIs (train_detector.py, train_embedder.py)
and the end-to-end pipeline. CPU-only by design."""
from __future__ import annotations

import math
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from .detection import decode, detection_loss
from .eval.metrics import average_precision, tar_at_far, verification_pairs


def _collate(batch):
    xs, ts = zip(*batch)
    return torch.stack(xs), list(ts)


def cosine_lr(opt, base, step, total, warmup=50):
    lr = base * min(1.0, (step + 1) / warmup) * 0.5 * (1 + math.cos(math.pi * min(step, total) / max(total, 1)))
    for g in opt.param_groups:
        g["lr"] = lr
    return lr


def train_detector(model, ds, epochs=10, bs=32, lr=3e-3, n_kpt=0, aux_weight=0.2, log=print, wd=1e-4,
                   max_steps=None, freeze_bn=False):
    model.train()
    if freeze_bn:
        for m in model.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()
    dl = DataLoader(ds, batch_size=bs, shuffle=True, collate_fn=_collate, drop_last=True)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=wd)
    total = epochs * len(dl) if max_steps is None else min(max_steps, epochs * len(dl))
    has_aux = getattr(model, "cfg", None) is not None and model.cfg.feat_dim > 0
    step, t0, hist = 0, time.time(), []
    for ep in range(epochs):
        for x, tg in dl:
            cosine_lr(opt, lr, step, total)
            outs = model(x, with_aux=True) if has_aux else model(x)
            levels = outs[:3]
            loss, parts = detection_loss(list(levels), tg, n_kpt=n_kpt)
            if has_aux:
                y = torch.tensor([float(len(t["boxes"]) > 0) for t in tg])
                la = F.binary_cross_entropy_with_logits(outs[-1], y)
                loss = loss + aux_weight * la
                parts["aux"] = float(la)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 10.0)
            opt.step()
            hist.append(float(loss))
            step += 1
            if step % 25 == 0 or step == 1:
                log(f"  ep {ep} step {step}/{total} loss {float(loss):.3f} " +
                    " ".join(f"{k}={v:.3f}" for k, v in parts.items()) + f" ({time.time() - t0:.0f}s)")
            if step >= total:
                return hist
    return hist


@torch.no_grad()
def predict_torch(model, x):
    model.eval()
    outs = model(x)
    return [o.numpy() for o in outs[:3]]


def eval_detector(predict, ds, n_kpt=0, bs=32, conf_thr=0.05):
    """``predict(x_numpy NCHW) -> list of 3 level arrays [N,C,H,W]``. Returns (mAP@0.5, dets)."""
    dets, gts = [], []
    for i in range(0, len(ds), bs):
        items = [ds[j] for j in range(i, min(len(ds), i + bs))]
        x = np.stack([it[0].numpy() for it in items])
        outs = predict(x)
        for b, it in enumerate(items):
            bx, sc, _ = decode([o[b] for o in outs], conf_thr=conf_thr, n_kpt=n_kpt)
            dets.append((bx, sc))
            gts.append(it[1]["boxes"].numpy())
    return average_precision(dets, gts, 0.5), dets


def torch_predictor(model):
    def f(x):
        return predict_torch(model, torch.from_numpy(x))
    return f


# ------------------------------------------------------------------ embedder

class CosFaceHead(nn.Module):
    """Large-margin cosine loss head (training only)."""

    def __init__(self, dim, n_cls, s=30.0, m=0.35):
        super().__init__()
        self.W = nn.Parameter(torch.randn(n_cls, dim) * 0.01)
        self.s, self.m = s, m

    def forward(self, emb, y):
        cos = F.linear(F.normalize(emb), F.normalize(self.W))
        onehot = F.one_hot(y, cos.shape[1]).float()
        return F.cross_entropy(self.s * (cos - self.m * onehot), y)


def train_embedder(model, ds, n_cls, epochs=10, bs=64, lr=3e-3, log=print, wd=5e-4, max_steps=None, head=None,
                   freeze_bn=False):
    model.train()
    if freeze_bn:
        for m in model.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()
    head = head or CosFaceHead(model.emb_dim, n_cls)
    dl = DataLoader(ds, batch_size=bs, shuffle=True, drop_last=True)
    opt = torch.optim.AdamW(list(model.parameters()) + list(head.parameters()), lr=lr, weight_decay=wd)
    total = epochs * len(dl) if max_steps is None else min(max_steps, epochs * len(dl))
    step, t0, hist = 0, time.time(), []
    for ep in range(epochs):
        for x, y in dl:
            if torch.rand(1).item() < 0.5:
                x = x.flip(3)
            cosine_lr(opt, lr, step, total)
            loss = head(model(x, normalize=False), y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            hist.append(float(loss))
            step += 1
            if step % 25 == 0 or step == 1:
                log(f"  ep {ep} step {step}/{total} loss {float(loss):.3f} ({time.time() - t0:.0f}s)")
            if step >= total:
                return hist, head
    return hist, head


@torch.no_grad()
def embed_all(predict, ds, bs=64):
    """``predict(x_numpy) -> raw embeddings [N,128]``; returns L2-normalised embeddings and labels."""
    embs, labels = [], []
    for i in range(0, len(ds), bs):
        items = [ds[j] for j in range(i, min(len(ds), i + bs))]
        x = np.stack([it[0].numpy() for it in items])
        e = predict(x)
        embs.append(e / np.linalg.norm(e, axis=1, keepdims=True))
        labels += [it[1] for it in items]
    return np.concatenate(embs), np.array(labels)


def eval_verification(predict, ds, fars=(1e-2, 1e-3)):
    emb, labels = embed_all(predict, ds)
    gen, imp = verification_pairs(emb, labels)
    res = {"n_genuine": int(len(gen)), "n_impostor": int(len(imp))}
    for far in fars:
        tar, thr, a = tar_at_far(gen, imp, far)
        res[f"tar@far={far:g}"] = tar
        res[f"thr@far={far:g}"] = thr
    return res, emb, labels


def torch_embed_predictor(model):
    @torch.no_grad()
    def f(x):
        model.eval()
        return model(torch.from_numpy(x), normalize=False).numpy()
    return f
