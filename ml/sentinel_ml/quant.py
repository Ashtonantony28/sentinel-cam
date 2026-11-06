"""Quantisation: float ONNX export, torch.ao QAT / PTQ → int8 QDQ ONNX, and an
onnxruntime static-quantisation PTQ path for comparison.

Int8 scheme (what ST Edge AI Core / Neural-ART expects from a QDQ ONNX):
* weights: int8, per-output-channel, symmetric (zero-point 0)
* activations: int8 per-tensor affine
* biases: int32, scale = s_in * s_w[c], zero-point 0
* model input: FIXED qparams scale = 1/255, zero_point = -128, i.e. the firmware
  feeds ``int8 = uint8_pixel - 128`` straight from the ISP; no float pre-processing.

Export flow (both torch.ao paths)::

    float model ──fold BN──► Conv(+bias)/ReLU graph ──fuse conv+relu──► prepare_qat
        QAT: fine-tune with fake-quant          PTQ: run calibration batches, observers only
    ──► torch.onnx.export (TorchScript exporter: fake-quant → QuantizeLinear/DequantizeLinear)
    ──► finalize_qdq(): weight Q folded into int8 initialisers, bias → int32 + DQ,
        Identity nodes removed.
"""
from __future__ import annotations

import copy
import io
import warnings

import numpy as np
import onnx
import torch
import torch.ao.quantization as tq
import torch.nn as nn
from onnx import helper, numpy_helper

from .models.blocks import fold_all_bn, fuse_conv_relu

OPSET = 17
INPUT_SCALE = 1.0 / 255.0
INPUT_ZP = -128


# ----------------------------------------------------------------- qconfig

def _act_fq(observer=tq.MovingAverageMinMaxObserver):
    return tq.FakeQuantize.with_args(observer=observer, quant_min=-128, quant_max=127, dtype=torch.qint8,
                                     qscheme=torch.per_tensor_affine, reduce_range=False)


def _w_fq(observer=tq.MovingAveragePerChannelMinMaxObserver):
    # ONNX QuantizeLinear only allows (-128, 127) for int8, so the symmetric range is
    # [-128, 127] with scale = absmax / 127.5 and zero-point 0.
    return tq.FakeQuantize.with_args(observer=observer, quant_min=-128, quant_max=127, dtype=torch.qint8,
                                     qscheme=torch.per_channel_symmetric, ch_axis=0, reduce_range=False)


def qconfig(ptq: bool = False):
    if ptq:
        return tq.QConfig(activation=_act_fq(tq.MinMaxObserver), weight=_w_fq(tq.PerChannelMinMaxObserver))
    return tq.QConfig(activation=_act_fq(), weight=_w_fq())


def input_qconfig():
    fixed = tq.FixedQParamsFakeQuantize.with_args(
        observer=tq.FixedQParamsObserver.with_args(scale=INPUT_SCALE, zero_point=INPUT_ZP, dtype=torch.qint8,
                                                   quant_min=-128, quant_max=127))
    return tq.QConfig(activation=fixed, weight=_w_fq())


def deploy_float(model: nn.Module) -> nn.Module:
    """Copy, eval, fold BN. The result is what gets quantised/exported."""
    m = copy.deepcopy(model).eval()
    return fold_all_bn(m)


def prepare_quant(model: nn.Module, ptq: bool = False) -> nn.Module:
    """Fold BN, fuse Conv+ReLU, attach fake-quant (torch.ao eager mode)."""
    m = deploy_float(model)
    fuse_conv_relu(m)
    m.qconfig = qconfig(ptq)
    m.quant.qconfig = input_qconfig()
    if hasattr(m, "aux"):
        m.aux.qconfig = None  # training-only head, not exported
    m.train()
    tq.prepare_qat(m, inplace=True)
    return m


def freeze_observers(m: nn.Module) -> nn.Module:
    m.apply(tq.disable_observer)
    m.apply(tq.enable_fake_quant)
    return m


@torch.no_grad()
def calibrate_ptq(m: nn.Module, batches) -> nn.Module:
    """PTQ: observers on, fake-quant off, run calibration data, then freeze."""
    m.eval()
    m.apply(tq.enable_observer)
    m.apply(tq.disable_fake_quant)
    for x in batches:
        m(x)
    return freeze_observers(m)


# ----------------------------------------------------------------- export

class _ExportWrapper(nn.Module):
    """Fixes forward kwargs and output arity for export."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, x):
        if hasattr(self.m, "emb_dim"):
            return self.m(x, normalize=False)
        return self.m(x)


def export_onnx(model: nn.Module, path: str, input_size: int, output_names):
    model = model.eval()
    x = torch.rand(1, 3, input_size, input_size)
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.onnx.export(_ExportWrapper(model), (x,), buf, dynamo=False, opset_version=OPSET,
                          input_names=["input"], output_names=list(output_names), do_constant_folding=True)
    g = onnx.load_from_string(buf.getvalue())
    g = remove_identity(g)
    g = finalize_qdq(g)
    g.producer_name = "sentinel_ml"
    onnx.checker.check_model(g)
    onnx.save(g, path)
    return g


def export_float_onnx(model, path, input_size, output_names):
    return export_onnx(deploy_float(model), path, input_size, output_names)


def export_quant_onnx(qmodel, path, input_size, output_names):
    freeze_observers(qmodel)
    return export_onnx(qmodel, path, input_size, output_names)


# ----------------------------------------------------------------- graph post-passes

def remove_identity(g: onnx.ModelProto) -> onnx.ModelProto:
    outs = {o.name for o in g.graph.output}
    rename = {}
    keep = []
    for n in g.graph.node:
        if n.op_type == "Identity" and n.output[0] not in outs:
            rename[n.output[0]] = n.input[0]
        else:
            keep.append(n)

    def r(name):
        while name in rename:
            name = rename[name]
        return name

    for n in keep:
        for i, v in enumerate(n.input):
            n.input[i] = r(v)
    # Identity feeding a graph output: rewire producer instead
    del g.graph.node[:]
    g.graph.node.extend(keep)
    return g


def _const_value(g, name):
    for init in g.graph.initializer:
        if init.name == name:
            return numpy_helper.to_array(init)
    for n in g.graph.node:
        if n.op_type == "Constant" and n.output[0] == name:
            return numpy_helper.to_array(n.attribute[0].t)
    return None


def finalize_qdq(g: onnx.ModelProto) -> onnx.ModelProto:
    """(1) QuantizeLinear(const float W) → int8 initialiser; (2) Conv float bias → int32
    initialiser + DequantizeLinear (scale = s_x * s_w); (3) drop unused initialisers."""
    nodes = list(g.graph.node)
    producer = {o: n for n in nodes for o in n.output}
    inits = {i.name: i for i in g.graph.initializer}
    new_inits, drop_nodes = [], set()
    # (1) fold weight quantisation
    for n in nodes:
        if n.op_type != "QuantizeLinear":
            continue
        w = _const_value(g, n.input[0])
        if w is None:
            continue
        scale = _const_value(g, n.input[1])
        zp = _const_value(g, n.input[2]) if len(n.input) > 2 else np.zeros((), np.int8)
        axis = next((a.i for a in n.attribute if a.name == "axis"), 1)
        shape = [1] * w.ndim
        if scale.ndim == 1 and scale.size > 1:
            shape[axis] = -1
        q = np.clip(np.rint(w / scale.reshape(shape)) + zp.reshape(shape).astype(np.int32), -128, 127).astype(np.int8)
        name = n.output[0]
        new_inits.append(numpy_helper.from_array(q, name))
        drop_nodes.add(id(n))
    nodes = [n for n in nodes if id(n) not in drop_nodes]
    g.graph.initializer.extend(new_inits)
    # (2) bias → int32 + DQ
    producer = {o: n for n in nodes for o in n.output}
    extra = []
    for ci, n in enumerate(nodes):
        if n.op_type != "Conv" or len(n.input) < 3:
            continue
        b = _const_value(g, n.input[2])
        dx, dw = producer.get(n.input[0]), producer.get(n.input[1])
        if b is None or dx is None or dw is None or dx.op_type != "DequantizeLinear" or dw.op_type != "DequantizeLinear":
            continue
        sx = _const_value(g, dx.input[1])
        sw = _const_value(g, dw.input[1])
        if sx is None or sw is None:
            continue
        sb = (np.float32(sx) * sw.astype(np.float32)).reshape(-1).astype(np.float32)
        if sb.size == 1:
            sb = np.full(b.shape, sb[0], np.float32)
        qb = np.clip(np.rint(b / sb), -2**31, 2**31 - 1).astype(np.int32)
        base = f"{n.name or 'conv'}_{ci}_bias_q"
        g.graph.initializer.extend([numpy_helper.from_array(qb, base),
                                    numpy_helper.from_array(sb, base + "_scale"),
                                    numpy_helper.from_array(np.zeros(b.shape, np.int32), base + "_zp")])
        dq = helper.make_node("DequantizeLinear", [base, base + "_scale", base + "_zp"], [base + "_dq"],
                              name=base + "_DQ", axis=0)
        extra.append((n, dq))
        n.input[2] = base + "_dq"
    for conv, dq in extra:
        nodes.insert(nodes.index(conv), dq)
    del g.graph.node[:]
    g.graph.node.extend(nodes)
    # (3) 1-element Q/DQ scale / zero-point → true scalars (QLinear* fusions need scalars)
    qparam_names = {i for n in g.graph.node if n.op_type in ("QuantizeLinear", "DequantizeLinear") for i in n.input[1:]}
    for k, init in enumerate(g.graph.initializer):
        if init.name in qparam_names and len(init.dims) == 1 and init.dims[0] == 1:
            arr = numpy_helper.to_array(init).reshape(())
            g.graph.initializer[k].CopyFrom(numpy_helper.from_array(arr, init.name))
    for n in g.graph.node:
        if n.op_type in ("QuantizeLinear", "DequantizeLinear"):
            keep_attrs = [a for a in n.attribute if not (a.name == "axis" and _is_scalar(g, n.input[1]))]
            del n.attribute[:]
            n.attribute.extend(keep_attrs)
    # (4) prune unused initialisers / constants
    used = {i for n in g.graph.node for i in n.input} | {o.name for o in g.graph.output}
    keep = [i for i in g.graph.initializer if i.name in used]
    del g.graph.initializer[:]
    g.graph.initializer.extend(keep)
    keepn = [n for n in g.graph.node if not (n.op_type == "Constant" and n.output[0] not in used)]
    del g.graph.node[:]
    g.graph.node.extend(keepn)
    return g


def _is_scalar(g, name):
    for init in g.graph.initializer:
        if init.name == name:
            return len(init.dims) == 0
    return False


# ----------------------------------------------------------------- onnxruntime PTQ (alternative)

def ort_ptq(float_onnx: str, out_path: str, calib: list[np.ndarray], per_channel=True):
    """onnxruntime static quantisation (QDQ, int8 act + int8 per-channel symmetric weights)."""
    from onnxruntime.quantization import (CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType,
                                          quantize_static)

    class Reader(CalibrationDataReader):
        def __init__(self):
            self.it = iter(calib)

        def get_next(self):
            x = next(self.it, None)
            return None if x is None else {"input": x.astype(np.float32)}

    quantize_static(float_onnx, out_path, Reader(), quant_format=QuantFormat.QDQ, per_channel=per_channel,
                    activation_type=QuantType.QInt8, weight_type=QuantType.QInt8,
                    calibrate_method=CalibrationMethod.MinMax,
                    extra_options={"WeightSymmetric": True, "ActivationSymmetric": False})
    return onnx.load(out_path)


# ----------------------------------------------------------------- run helpers

class OrtModel:
    """Thin onnxruntime wrapper: ``m(x_nchw_float) -> list of outputs``."""

    def __init__(self, path, threads: int = 1):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name
        self.outputs = [o.name for o in self.sess.get_outputs()]

    def __call__(self, x: np.ndarray):
        if x.shape[0] == 1:
            return self.sess.run(None, {self.input: x.astype(np.float32)})
        outs = [self.sess.run(None, {self.input: x[i:i + 1].astype(np.float32)}) for i in range(x.shape[0])]
        return [np.concatenate([o[k] for o in outs]) for k in range(len(outs[0]))]
