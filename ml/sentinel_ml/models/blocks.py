"""NPU-friendly building blocks.

Every block uses only: Conv (regular / depthwise / pointwise), BatchNorm (folded
into Conv before export), ReLU, Add, Concat, nearest Resize, pooling.
No SiLU / Swish / hard-swish / PReLU: Neural-ART-class int8 NPUs run ReLU fused
into the conv epilogue for free, while the others need LUTs or CPU fallback.

Residual adds and concats go through ``FloatFunctional`` so torch.ao eager-mode
QAT can attach an output observer to them (they become Q/DQ pairs on export).
``Requant`` (a ``QuantStub``) is placed after ops that eager QAT does not observe
(Resize, pooling) so that every tensor in the exported graph is int8-on-the-wire.
"""
from __future__ import annotations

import torch
import torch.nn as nn
from torch.ao.nn.quantized import FloatFunctional
from torch.ao.quantization import QuantStub


def make_divisible(v: float, d: int = 8) -> int:
    return max(d, int(v + d / 2) // d * d)


def Requant() -> QuantStub:
    """Identity in float; after ``prepare_qat`` it is an activation fake-quant.
    (A plain ``QuantStub``: eager-mode prepare matches the exact type, not subclasses.)"""
    return QuantStub()


class ConvBNAct(nn.Module):
    """Conv2d → BatchNorm2d → optional ReLU.

    ``fold_bn()`` turns this into Conv(bias) → ReLU and ``fuse_names()`` lists the
    (conv, act) pair for ``torch.ao.quantization.fuse_modules``.
    """

    def __init__(self, cin, cout, k=3, s=1, groups=1, act=True, padding=None):
        super().__init__()
        p = (k - 1) // 2 if padding is None else padding
        self.conv = nn.Conv2d(cin, cout, k, s, p, groups=groups, bias=False)
        self.bn = nn.BatchNorm2d(cout)
        self.act = nn.ReLU(inplace=False) if act else nn.Identity()
        self.has_act = act

    def forward(self, x):
        return self.act(self.bn(self.conv(x)))

    @torch.no_grad()
    def fold_bn(self):
        if isinstance(self.bn, nn.Identity):
            return
        w = self.conv.weight
        bn = self.bn
        std = torch.sqrt(bn.running_var + bn.eps)
        scale = bn.weight / std
        b = self.conv.bias if self.conv.bias is not None else torch.zeros_like(bn.running_mean)
        new_b = (b - bn.running_mean) * scale + bn.bias
        conv = nn.Conv2d(self.conv.in_channels, self.conv.out_channels, self.conv.kernel_size,
                         self.conv.stride, self.conv.padding, groups=self.conv.groups, bias=True)
        conv.weight.copy_(w * scale.reshape(-1, 1, 1, 1))
        conv.bias.copy_(new_b)
        self.conv = conv
        self.bn = nn.Identity()


def dw_sep(cin, cout, s=1):
    """Depthwise 3x3 + pointwise 1x1 (MobileNetV1 style)."""
    return nn.Sequential(ConvBNAct(cin, cin, 3, s, groups=cin), ConvBNAct(cin, cout, 1))


class InvertedResidual(nn.Module):
    """MobileNetV2 bottleneck with ReLU. expand(1x1) → DW 3x3 → project(1x1, linear)."""

    def __init__(self, cin, cout, s=1, expand=2):
        super().__init__()
        hid = make_divisible(cin * expand)
        layers = []
        if expand != 1:
            layers.append(ConvBNAct(cin, hid, 1))
        else:
            hid = cin
        layers += [ConvBNAct(hid, hid, 3, s, groups=hid), ConvBNAct(hid, cout, 1, act=False)]
        self.body = nn.Sequential(*layers)
        self.use_res = s == 1 and cin == cout
        self.ff = FloatFunctional()

    def forward(self, x):
        y = self.body(x)
        return self.ff.add(x, y) if self.use_res else y


class Upsample2x(nn.Module):
    def __init__(self):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode="nearest")
        self.rq = Requant()

    def forward(self, x):
        return self.rq(self.up(x))


def fold_all_bn(model: nn.Module) -> nn.Module:
    for m in model.modules():
        if isinstance(m, ConvBNAct):
            m.fold_bn()
    return model


def fuse_conv_relu(model: nn.Module) -> nn.Module:
    """After fold_all_bn: fuse each ConvBNAct's conv+ReLU (for eager QAT)."""
    from torch.ao.quantization import fuse_modules

    for m in model.modules():
        if isinstance(m, ConvBNAct) and m.has_act and isinstance(m.act, nn.ReLU):
            fuse_modules(m, [["conv", "act"]], inplace=True)
    return model


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
