import pytest
import torch
import torch.nn as nn
from torch.ao.nn.quantized import FloatFunctional
from torch.ao.quantization import DeQuantStub, QuantStub

from sentinel_ml.models import (STRIDES, build_embedder, build_face_detector, build_person_detector, count_params,
                                fold_all_bn)
from sentinel_ml.models.blocks import ConvBNAct

ALLOWED = (nn.Conv2d, nn.BatchNorm2d, nn.ReLU, nn.Identity, nn.Upsample, nn.AdaptiveAvgPool2d, nn.Linear,
           FloatFunctional, QuantStub, DeQuantStub)


@pytest.mark.parametrize("build", [build_person_detector, build_face_detector, build_embedder])
def test_only_npu_friendly_modules(build):
    leaves = {type(x) for x in build().modules() if not list(x.children())}
    for t in leaves:
        assert issubclass(t, ALLOWED), t
    for t in (nn.SiLU, nn.Hardswish, nn.PReLU, nn.GELU, nn.Sigmoid):
        assert t not in leaves


def test_person_detector_shapes_and_budget():
    m = build_person_detector().eval()
    assert count_params(m) - count_params(m.aux) < 1_000_000
    outs = m(torch.rand(2, 3, 256, 256))
    assert [tuple(o.shape) for o in outs[:3]] == [(2, 5, 256 // s, 256 // s) for s in STRIDES]
    assert tuple(outs[3].shape) == (2, 64)  # false-alert feature (INTERFACES §5)
    assert m.output_names() == ["p8", "p16", "p32", "feat"]
    assert len(m(torch.rand(1, 3, 256, 256), with_aux=True)) == 5


def test_face_detector_shapes():
    m = build_face_detector().eval()
    outs = m(torch.rand(1, 3, 160, 160))
    assert [tuple(o.shape) for o in outs] == [(1, 15, 20, 20), (1, 15, 10, 10), (1, 15, 5, 5)]
    assert count_params(m) < 300_000


def test_embedder_normalised_128():
    m = build_embedder().eval()
    e = m(torch.rand(3, 3, 112, 112))
    assert e.shape == (3, 128)
    torch.testing.assert_close(e.norm(dim=1), torch.ones(3))
    assert m(torch.rand(1, 3, 112, 112), normalize=False).shape == (1, 128)


@pytest.mark.parametrize("build,size", [(build_person_detector, 256), (build_embedder, 112)])
def test_fold_bn_is_exact(build, size):
    m = build()
    m.train()
    for _ in range(3):
        m(torch.rand(4, 3, size, size))
    m.eval()
    x = torch.rand(2, 3, size, size)
    with torch.no_grad():
        ref = m(x)
        fold_all_bn(m)
        out = m(x)
    ref, out = (ref, out) if isinstance(ref, tuple) else ((ref,), (out,))
    for a, b in zip(ref, out):
        torch.testing.assert_close(a, b, atol=1e-4, rtol=1e-4)
    assert not any(isinstance(x, nn.BatchNorm2d) for x in m.modules())
    assert all(isinstance(x.bn, nn.Identity) for x in m.modules() if isinstance(x, ConvBNAct))
