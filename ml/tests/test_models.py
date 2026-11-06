import pytest
import torch
import torch.nn as nn

from sentinel_ml.models import (STRIDES, build_embedder, build_face_detector, build_person_detector, count_params,
                                fold_all_bn)
from sentinel_ml.models.blocks import ConvBNAct

ALLOWED_MODULES = (nn.Conv2d, nn.BatchNorm2d, nn.ReLU, nn.Identity, nn.Upsample, nn.AdaptiveAvgPool2d, nn.Linear,
                   nn.Sequential, nn.ModuleList)


def _leaf_types(m):
    return {type(x) for x in m.modules() if not list(x.children())}


@pytest.mark.parametrize("build", [build_person_detector, build_face_detector, build_embedder])
def test_only_npu_friendly_modules(build):
    m = build()
    from torch.ao.nn.quantized import FloatFunctional
    from torch.ao.quantization import DeQuantStub, QuantStub
    for t in _leaf_types(m):
        assert issubclass(t, ALLOWED_MODULES + (FloatFunctional, QuantStub, DeQuantStub)), t
    for t in (nn.SiLU, nn.Hardswish, nn.PReLU, nn.GELU, nn.Sigmoid):
        assert t not in _leaf_types(m)


def test_person_detector_shapes_and_budget():
    m = build_person_detector().eval()
    params = count_params(m) - count_params(m.aux)
    assert params < 1_000_000
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
    raw = m(torch.rand(1, 3, 112, 112), normalize=False)
    assert raw.shape == (1, 128)


@pytest.mark.parametrize("build,size", [(build_person_detector, 256), (build_embedder, 112)])
def test_fold_bn_is_exact(build, size):
    m = build()
    m.train()
    for _ in range(3):
        m(torch.rand(4, 3, size, size))  # non-trivial BN stats
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
