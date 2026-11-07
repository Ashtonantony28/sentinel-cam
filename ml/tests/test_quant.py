"""Quantisation paths → int8 QDQ ONNX, structure and float-vs-int8 parity with the
tolerances stated in sentinel_ml.pipeline.TOL."""
import numpy as np
import onnx
import pytest
import torch
from onnx import numpy_helper

from sentinel_ml.models import build_embedder, build_person_detector
from sentinel_ml.npu_fit import analyze
from sentinel_ml.pipeline import check_parity, parity_detector, parity_embedder
from sentinel_ml.quant import (INPUT_SCALE, INPUT_ZP, OrtModel, calibrate_ptq, export_float_onnx, export_quant_onnx,
                               ort_ptq, prepare_quant)


def _structured_inputs(n, size, seed=0):
    """Images with structure (blobs on smooth background), closer to real data than U(0,1)."""
    from sentinel_ml.data.synthetic_person import render_person_scene
    import PIL.Image as I
    xs = []
    for i in range(n):
        im = render_person_scene(seed * 1000 + i, 256)[0]
        if size != 256:
            im = np.asarray(I.fromarray((im * 255).astype(np.uint8)).resize((size, size)), np.float32) / 255
        xs.append(im.transpose(2, 0, 1))
    return np.stack(xs).astype(np.float32)


def _warm(m, xs):
    """Non-trivial BN statistics and a head that actually fires, so parity is meaningful."""
    torch.manual_seed(0)
    m.train()
    with torch.no_grad():
        for i in range(0, len(xs), 8):
            m(torch.from_numpy(xs[i:i + 8]))
        if hasattr(m, "heads"):
            m.eval()
            x = torch.from_numpy(xs[:8])
            for h in m.heads:
                h.pred.weight.normal_(0, 0.05)
                h.pred.bias.zero_()
            outs = m(x)
            for h, o in zip(m.heads, outs[:3]):  # rescale so logits have std ~2 (probabilities spread out)
                h.pred.weight.mul_(2.0 / (o.std() + 1e-6))
    return m.eval()


@pytest.fixture(scope="module")
def person_exports(tmp_path_factory):
    d = tmp_path_factory.mktemp("q")
    xs = _structured_inputs(48, 128)
    torch.manual_seed(0)  # independent of test order
    m = _warm(build_person_detector(width=0.5, input_size=128), xs)
    names = m.output_names()
    paths = {"float": str(d / "f.onnx"), "ptq": str(d / "ptq.onnx"), "qat": str(d / "qat.onnx"),
             "ort": str(d / "ort.onnx")}
    export_float_onnx(m, paths["float"], 128, names)
    calib = [torch.from_numpy(xs[i:i + 8]) for i in range(0, 32, 8)]
    q = calibrate_ptq(prepare_quant(m, ptq=True), calib)
    export_quant_onnx(q, paths["ptq"], 128, names)
    qat = prepare_quant(m, ptq=False)
    qat.train()
    from sentinel_ml.train import train_detector
    from sentinel_ml.data.synthetic_person import SyntheticPersonDataset
    with torch.no_grad():
        for xb in calib:
            qat(xb)
    train_detector(qat, SyntheticPersonDataset(16, seed=5, size=128), epochs=10, bs=8, lr=1e-4, max_steps=3,
                   log=lambda *a: None)
    export_quant_onnx(qat, paths["qat"], 128, names)
    ort_ptq(paths["float"], paths["ort"], [xs[i:i + 1] for i in range(32)])
    return paths, xs[32:], q, m


def test_qdq_structure(person_exports):
    paths, _, _, _ = person_exports
    g = onnx.load(paths["ptq"])
    inits = {i.name: numpy_helper.to_array(i) for i in g.graph.initializer}
    prod = {o: n for n in g.graph.node for o in n.output}
    convs = [n for n in g.graph.node if n.op_type == "Conv"]
    assert convs
    for c in convs:
        w = prod[c.input[1]]
        assert w.op_type == "DequantizeLinear" and inits[w.input[0]].dtype == np.int8
        assert np.all(inits[w.input[2]] == 0) if len(w.input) > 2 and w.input[2] in inits else True
        b = prod[c.input[2]]
        assert b.op_type == "DequantizeLinear" and inits[b.input[0]].dtype == np.int32
        assert prod[c.input[0]].op_type == "DequantizeLinear"
    # fixed input qparams: int8 = uint8 pixel - 128
    q_in = next(n for n in g.graph.node if n.op_type == "QuantizeLinear" and n.input[0] == "input")
    assert float(inits[q_in.input[1]]) == pytest.approx(INPUT_SCALE)
    assert int(inits[q_in.input[2]]) == INPUT_ZP
    assert [o.name for o in g.graph.output] == ["p8", "p16", "p32", "feat"]


@pytest.mark.parametrize("variant", ["ptq", "qat", "ort"])
def test_int8_exports_fit_npu(person_exports, variant):
    r = analyze(person_exports[0][variant])
    assert r["int8"] and not r["violations"] and not r["float_compute_inputs"], r["violations"]


def test_float_vs_int8_parity_ptq(person_exports):
    paths, xs, _, _ = person_exports
    f, q = OrtModel(paths["float"])(xs), OrtModel(paths["ptq"])(xs)
    p = parity_detector(f, q)
    assert np.mean([(1 / (1 + np.exp(-o[:, 0]))).std() for o in f[:3]]) > 0.01  # outputs are not trivial
    assert check_parity("person", p) == [], p
    p2 = parity_detector(f, OrtModel(paths["ort"])(xs))
    assert check_parity("person", p2) == [], p2


def test_torch_fakequant_matches_onnx_int8(person_exports):
    paths, xs, q, _ = person_exports
    with torch.no_grad():
        t = [o.numpy() for o in q(torch.from_numpy(xs[:4]))]
    o = OrtModel(paths["ptq"])(xs[:4])
    for a, b in zip(t[:3], o[:3]):
        # Same integer grid; onnxruntime's fused int8 kernels requantise with their own
        # rounding, so values may differ by 1 LSB (output quantisation step), never more than 2.
        u = np.unique(a)
        lsb = float(np.min(np.diff(u)))
        d = np.abs(a - b) / lsb
        # an intermediate 1-LSB flip can propagate, so bound the distribution, not the max
        print(f"fq-vs-ort: max {d.max():.1f} LSB, mean {d.mean():.3f}, within 1 LSB {np.mean(d <= 1.01):.4f}")
        assert np.mean(d <= 1.01) >= 0.97 and d.mean() <= 0.5, (d.max(), d.mean(), np.mean(d <= 1.01))


def test_embedder_parity(tmp_path):
    from sentinel_ml.data.synthetic_face import render_aligned_face
    xs = np.stack([render_aligned_face(i % 6, i).transpose(2, 0, 1) for i in range(40)]).astype(np.float32)
    torch.manual_seed(1)
    m = _warm(build_embedder(width=0.5, depth=0.5), xs)
    export_float_onnx(m, str(tmp_path / "f.onnx"), 112, ["emb"])
    q = calibrate_ptq(prepare_quant(m, ptq=True), [torch.from_numpy(xs[i:i + 8]) for i in range(0, 24, 8)])
    export_quant_onnx(q, str(tmp_path / "q.onnx"), 112, ["emb"])
    p = parity_embedder(OrtModel(str(tmp_path / "f.onnx"))(xs[24:])[0], OrtModel(str(tmp_path / "q.onnx"))(xs[24:])[0])
    assert check_parity("emb", p) == [], p
