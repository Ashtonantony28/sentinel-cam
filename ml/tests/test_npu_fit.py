import os
import subprocess
import sys

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from sentinel_ml import npu_fit

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIGN_TOOL = os.path.join(REPO, "tools", "sentinel_sign.py")


def _model(extra_nodes=(), extra_out=None):
    w = numpy_helper.from_array(np.random.rand(8, 3, 3, 3).astype(np.float32), "w")
    b = numpy_helper.from_array(np.zeros(8, np.float32), "b")
    nodes = [helper.make_node("Conv", ["x", "w", "b"], ["c"], name="conv", pads=[1, 1, 1, 1]),
             helper.make_node("Relu", ["c"], ["y"], name="relu")] + list(extra_nodes)
    out = extra_out or "y"
    g = helper.make_graph(nodes, "t", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 16, 16])],
                          [helper.make_tensor_value_info(out, TensorProto.FLOAT, None)], [w, b])
    return helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])


def test_counts_macs_params_and_passes(tmp_path):
    r = npu_fit.analyze(_model())
    assert r["macs"] == 16 * 16 * 8 * 3 * 3 * 3
    assert r["params"] == 8 * 27 + 8
    assert not r["violations"]
    assert r["peak_activation_bytes"] == (3 + 8) * 16 * 16 * 4  # float input + fused conv/relu output


@pytest.mark.parametrize("node,frag", [
    (helper.make_node("Sigmoid", ["y"], ["z"], name="sig"), "Sigmoid"),
    (helper.make_node("HardSwish", ["y"], ["z"], name="hs"), "HardSwish"),
    (helper.make_node("Resize", ["y", "", "sc"], ["z"], name="rs", mode="linear"), "mode 'linear'"),
])
def test_disallowed_ops_fail_cli(tmp_path, node, frag):
    extra = [node]
    if node.op_type == "Resize":
        extra = [helper.make_node("Constant", [], ["sc"], value=numpy_helper.from_array(
            np.array([1, 1, 2, 2], np.float32)))] + extra
    p = tmp_path / "m.onnx"
    onnx.save(_model(extra, "z"), p)
    r = npu_fit.analyze(str(p))
    assert any(frag in v for v in r["violations"])
    assert npu_fit.main([str(p)]) == 1


def test_require_int8_flags_float_model(tmp_path):
    p = tmp_path / "m.onnx"
    onnx.save(_model(), p)
    assert npu_fit.main([str(p)]) == 0
    assert npu_fit.main([str(p), "--require-int8"]) == 1


def test_op_set_hash_matches_contract_and_tool(tmp_path):
    ops = ["Conv", "Relu", "DequantizeLinear", "QuantizeLinear", "Add", "Resize", "Constant", "Relu"]
    canon = npu_fit.canonical_op_list(ops)
    assert canon == ["add", "conv", "dequantizelinear", "quantizelinear", "relu", "resize"]
    import hashlib
    want = hashlib.sha256(b"sentinel-opset-v1\n" + "".join(o + "\n" for o in canon).encode()).hexdigest()
    assert npu_fit.op_set_hash(ops) == want
    if not os.path.exists(SIGN_TOOL):
        pytest.skip("tools/sentinel_sign.py not present")
    f = tmp_path / "ops.txt"
    f.write_text("".join(o + "\n" for o in canon))
    out = subprocess.run([sys.executable, SIGN_TOOL, "opset", str(f)], capture_output=True, text=True)
    if out.returncode != 0 and "No module named" in out.stderr:
        pytest.skip("sentinel_sign.py dependencies missing: " + out.stderr.strip().splitlines()[-1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == npu_fit.op_set_hash(ops)


def test_opset_out_file(tmp_path):
    p, o = tmp_path / "m.onnx", tmp_path / "ops.txt"
    onnx.save(_model(), p)
    npu_fit.main([str(p), "--opset-out", str(o)])
    assert o.read_text() == "conv\nrelu\n"
