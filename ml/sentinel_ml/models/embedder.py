"""MobileFaceNet-style embedder: aligned 112x112 face → 128-d embedding.

Deviations from the paper, for an int8 NPU:
* PReLU → ReLU (PReLU is not reliably accelerated; ReLU fuses into the conv epilogue).
* The final L2 normalisation is NOT in the graph (ReduceSum/Sqrt/Div are CPU ops);
  the host normalises the 128 floats (trivial on the M55), then quantises per
  INTERFACES.md §1. ``forward(normalize=True)`` does it in PyTorch for training/eval.
The global depthwise conv (GDC, 7x7 depthwise, no padding) replaces global pooling,
as in the paper.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.ao.quantization import DeQuantStub, QuantStub

from .blocks import ConvBNAct, InvertedResidual, make_divisible

# (expand, out, n, stride) from the MobileFaceNet paper (Table 1)
MFN_SETTINGS = [(2, 64, 5, 2), (4, 128, 1, 2), (2, 128, 6, 1), (4, 128, 1, 2), (2, 128, 2, 1)]


class MobileFaceNet(nn.Module):
    def __init__(self, emb_dim: int = 128, width: float = 1.0, depth: float = 1.0, input_size: int = 112):
        super().__init__()
        self.input_size = input_size
        c0 = make_divisible(64 * width)
        self.quant = QuantStub()
        self.stem = nn.Sequential(ConvBNAct(3, c0, 3, 2), ConvBNAct(c0, c0, 3, 1, groups=c0))
        blocks, c = [], c0
        for t, cout, n, s in MFN_SETTINGS:
            cout = make_divisible(cout * width)
            n = max(1, round(n * depth))
            for i in range(n):
                blocks.append(InvertedResidual(c, cout, s if i == 0 else 1, t))
                c = cout
        self.blocks = nn.Sequential(*blocks)
        cl = make_divisible(512 * width)
        self.conv_last = ConvBNAct(c, cl, 1)
        k = input_size // 16
        self.gdc = ConvBNAct(cl, cl, k, 1, groups=cl, act=False, padding=0)
        self.emb = ConvBNAct(cl, emb_dim, 1, act=False)
        self.dequant = DeQuantStub()
        self.emb_dim = emb_dim

    def forward(self, x, normalize: bool = True):
        x = self.quant(x)
        x = self.blocks(self.stem(x))
        x = self.emb(self.gdc(self.conv_last(x)))
        x = self.dequant(x).flatten(1)
        return F.normalize(x, dim=1) if normalize else x

    def output_names(self):
        return ["emb"]


def build_embedder(width: float = 1.0, depth: float = 1.0) -> MobileFaceNet:
    return MobileFaceNet(width=width, depth=depth)
