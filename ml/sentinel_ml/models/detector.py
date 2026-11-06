"""Anchor-free single-class detector (YOLOX/FCOS-style head), shared by the person
and face detectors.

Per pyramid level (strides 8/16/32) the model emits one raw tensor
``[N, 1 + 4 + 2*n_kpt, H, W]``:

* channel 0: objectness logit (sigmoid is applied on the host / M55, not on the NPU)
* channels 1..4: ``log(d / stride)`` for the left/top/right/bottom distances from the
  cell centre to the box edges (``exp`` is applied in post-processing)
* channels 5..: keypoint offsets ``(kpt - cell_centre) / stride`` (face detector only)

Keeping sigmoid/exp/NMS out of the graph keeps the NPU graph to Conv/ReLU/Add/
Resize/pool only. Decoding lives in ``sentinel_ml.detection``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import torch
import torch.nn as nn
from torch.ao.quantization import DeQuantStub, QuantStub

from .blocks import ConvBNAct, InvertedResidual, Requant, Upsample2x, dw_sep, make_divisible
from torch.ao.nn.quantized import FloatFunctional


@dataclass
class DetectorConfig:
    name: str = "person_det"
    input_size: int = 256
    stem: int = 16
    # (out_channels, n_blocks, first_stride, expand) per stage; strides 2,4,8,16,32 overall
    stages: list = field(default_factory=lambda: [
        (24, 2, 2, 3),   # /4
        (32, 3, 2, 3),   # /8  -> C3
        (64, 3, 2, 3),   # /16 -> C4
        (96, 2, 2, 3),   # /32 -> C5
    ])
    neck: int = 48
    n_kpt: int = 0
    feat_dim: int = 0  # >0: expose a pooled + projected neck feature (false-alert head input)
    width: float = 1.0

    @property
    def n_out(self) -> int:
        return 1 + 4 + 2 * self.n_kpt


STRIDES = (8, 16, 32)


class Head(nn.Module):
    def __init__(self, ch, n_out):
        super().__init__()
        self.body = nn.Sequential(ConvBNAct(ch, ch, 3, groups=ch), ConvBNAct(ch, ch, 1))
        self.pred = nn.Conv2d(ch, n_out, 1)
        nn.init.normal_(self.pred.weight, std=0.01)
        nn.init.zeros_(self.pred.bias)
        with torch.no_grad():
            self.pred.bias[0] = -4.0  # prior ~ 0.018 for objectness

    def forward(self, x):
        return self.pred(self.body(x))


class AnchorFreeDetector(nn.Module):
    def __init__(self, cfg: DetectorConfig):
        super().__init__()
        self.cfg = cfg
        w = cfg.width
        c = make_divisible(cfg.stem * w)
        self.quant = QuantStub()
        layers = [ConvBNAct(3, c, 3, 2), dw_sep(c, make_divisible(16 * w))]
        c = make_divisible(16 * w)
        self.stem = nn.Sequential(*layers)
        self.stages = nn.ModuleList()
        outs = []
        for cout, n, s, e in cfg.stages:
            cout = make_divisible(cout * w)
            blocks = [InvertedResidual(c, cout, s, e)] + [InvertedResidual(cout, cout, 1, e) for _ in range(n - 1)]
            self.stages.append(nn.Sequential(*blocks))
            c = cout
            outs.append(c)
        c3, c4, c5 = outs[-3:]
        nk = make_divisible(cfg.neck * w)
        self.lat3, self.lat4, self.lat5 = ConvBNAct(c3, nk, 1), ConvBNAct(c4, nk, 1), ConvBNAct(c5, nk, 1)
        self.up4, self.up3 = Upsample2x(), Upsample2x()
        self.add4, self.add3 = FloatFunctional(), FloatFunctional()
        # bottom-up path (PAN-lite): strided depthwise-separable convs
        self.down3, self.down4 = dw_sep(nk, nk, 2), dw_sep(nk, nk, 2)
        self.add4b, self.add5b = FloatFunctional(), FloatFunctional()
        self.smooth3, self.smooth4, self.smooth5 = dw_sep(nk, nk), dw_sep(nk, nk), dw_sep(nk, nk)
        self.heads = nn.ModuleList([Head(nk, cfg.n_out) for _ in STRIDES])
        self.dequant = nn.ModuleList([DeQuantStub() for _ in STRIDES])
        if cfg.feat_dim:
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.pool_rq = Requant()
            self.proj = nn.Conv2d(nk, cfg.feat_dim, 1)  # linear projection, 1x1 conv (NPU-friendly)
            self.feat_dq = DeQuantStub()
            # training-only auxiliary "image contains a person" logit on the feature;
            # not part of the exported graph (see export.py: forward(export=True)).
            self.aux = nn.Linear(cfg.feat_dim, 1)
        self.neck_ch = nk

    def forward(self, x, with_aux: bool = False):
        x = self.quant(x)
        x = self.stem(x)
        feats = []
        for st in self.stages:
            x = st(x)
            feats.append(x)
        c3, c4, c5 = feats[-3:]
        p5 = self.lat5(c5)
        p4 = self.add4.add(self.lat4(c4), self.up4(p5))
        p3 = self.add3.add(self.lat3(c3), self.up3(p4))
        p3 = self.smooth3(p3)
        p4 = self.smooth4(self.add4b.add(p4, self.down3(p3)))
        p5 = self.smooth5(self.add5b.add(p5, self.down4(p4)))
        outs = [dq(h(p)) for dq, h, p in zip(self.dequant, self.heads, (p3, p4, p5))]
        if self.cfg.feat_dim:
            f = self.feat_dq(self.proj(self.pool_rq(self.pool(p4)))).flatten(1)
            outs.append(f)
            if with_aux:
                outs.append(self.aux(f).squeeze(1))
        return tuple(outs)

    def output_names(self):
        names = [f"p{s}" for s in STRIDES]
        if self.cfg.feat_dim:
            names.append("feat")
        return names
