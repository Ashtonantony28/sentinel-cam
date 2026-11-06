"""NPU-fit checker for exported ONNX models.

    python -m sentinel_ml.npu_fit model.onnx [--json out.json] [--require-int8]

Reports op histogram, ops outside the allowlist, parameter count, weight bytes,
MACs (Conv/Gemm/MatMul), a peak activation-memory estimate and an op-set hash.
Exits 1 if any op is outside the allowlist or violates an attribute rule
(and, with ``--require-int8``, if any compute op consumes a non-quantised tensor).

IMPORTANT: the allowlist is an APPROXIMATION of what ST's Neural-ART NPU runs in
hardware (int8 QDQ conv/pool/eltwise/resize-nearest families), written from public
ST Edge AI Core documentation, deliberately conservative. On real hardware it is to
be replaced by the ST Edge AI Core ``analyze``/``generate`` report, which is the
ground truth for HW-mapped vs. SW-fallback epochs (PLAN.md §5.2 "NPU-fit loop").

Peak activation memory: the graph is "logically" collapsed over Q/DQ pairs (a
DequantizeLinear output aliases its quantised tensor); tensors that are quantised
count 1 byte/element, float ones 4. Walking nodes in topological order, the peak
of (live tensors + outputs being produced) is reported. This is a lower-bound
style estimate: ST's memory planner adds alignment, scratch and epoch buffers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter

import numpy as np
import onnx
from onnx import numpy_helper, shape_inference

# Hardware-mapped on Neural-ART (approximation, see module docstring).
NPU_ALLOWLIST = {
    "Conv": "standard / depthwise / pointwise convolution (int8 per-channel weights)",
    "Relu": "fused into the conv epilogue",
    "Clip": "ReLU6-style clamp (min must be 0)",
    "Add": "element-wise add (residuals, FPN merges)",
    "Concat": "channel concat",
    "Resize": "nearest-neighbour upsampling only",
    "MaxPool": "pooling",
    "AveragePool": "pooling",
    "GlobalAveragePool": "global pooling",
    "QuantizeLinear": "int8 Q (folded into neighbours by the compiler)",
    "DequantizeLinear": "int8 DQ (folded into neighbours by the compiler)",
}
# Layout / no-op nodes that cost nothing (folded at compile time).
FREE_OPS = {"Constant", "Identity", "Flatten", "Reshape", "Squeeze", "Unsqueeze"}
ALLOWED = set(NPU_ALLOWLIST) | FREE_OPS
QDQ = {"QuantizeLinear", "DequantizeLinear"}
COMPUTE = {"Conv", "Relu", "Clip", "Add", "Concat", "Resize", "MaxPool", "AveragePool", "GlobalAveragePool",
           "Gemm", "MatMul", "Mul", "Sub", "Sigmoid", "HardSwish", "HardSigmoid", "PRelu", "LeakyRelu", "Softmax",
           "BatchNormalization", "ConvTranspose", "Exp", "Div", "Sqrt", "ReduceMean", "ReduceSum", "Tanh"}


def _attr(n, name, default=None):
    for a in n.attribute:
        if a.name == name:
            return onnx.helper.get_attribute_value(a)
    return default


def _shapes(model):
    m = shape_inference.infer_shapes(model)
    out = {}
    for vi in list(m.graph.value_info) + list(m.graph.input) + list(m.graph.output):
        t = vi.type.tensor_type
        if t.HasField("shape"):
            dims = [d.dim_value if d.HasField("dim_value") else 1 for d in t.shape.dim]
            out[vi.name] = dims
    for init in m.graph.initializer:
        out[init.name] = list(init.dims)
    return out


def _consts(model):
    c = {i.name: numpy_helper.to_array(i) for i in model.graph.initializer}
    for n in model.graph.node:
        if n.op_type == "Constant":
            t = _attr(n, "value")
            if t is not None:
                c[n.output[0]] = numpy_helper.to_array(t)
    return c


def analyze(path_or_model) -> dict:
    model = onnx.load(path_or_model) if isinstance(path_or_model, str) else path_or_model
    g = model.graph
    shapes = _shapes(model)
    consts = _consts(model)
    hist = Counter(n.op_type for n in g.node)
    violations = []
    for n in g.node:
        if n.op_type not in ALLOWED:
            violations.append(f"{n.op_type} ({n.name}): not in NPU allowlist")
            continue
        if n.op_type == "Resize":
            mode = _attr(n, "mode", b"nearest")
            mode = mode.decode() if isinstance(mode, bytes) else mode
            if mode != "nearest":
                violations.append(f"Resize ({n.name}): mode '{mode}' (only nearest is allowed)")
        if n.op_type == "Clip":
            lo = consts.get(n.input[1]) if len(n.input) > 1 and n.input[1] else None
            if lo is None or float(np.asarray(lo)) != 0.0:
                violations.append(f"Clip ({n.name}): min must be 0 (ReLU/ReLU6)")
        if n.op_type == "Conv":
            dil = _attr(n, "dilations", [1, 1])
            if any(d != 1 for d in dil):
                violations.append(f"Conv ({n.name}): dilation {dil} not allowed")

    # ---- parameters / weight bytes
    producer = {o: n for n in g.node for o in n.output}
    inits = {i.name: i for i in g.initializer}
    param_names = set()
    for n in g.node:
        if n.op_type in ("Conv", "Gemm", "MatMul", "ConvTranspose"):
            for inp in n.input[1:3]:
                if not inp:
                    continue
                p = producer.get(inp)
                if p is not None and p.op_type == "DequantizeLinear":
                    inp = p.input[0]
                if inp in inits:
                    param_names.add(inp)
    params = int(sum(np.prod(inits[p].dims) for p in param_names))
    weight_bytes = int(sum(numpy_helper.to_array(inits[p]).nbytes for p in param_names))

    # ---- MACs
    macs = 0
    per_node_macs = {}
    for n in g.node:
        if n.op_type == "Conv":
            out = shapes.get(n.output[0])
            w = shapes.get(n.input[1])
            if out is None or w is None:
                continue
            k = int(np.prod(w[1:]))  # Cin/groups * kh * kw
            m = int(np.prod(out[1:])) * k
        elif n.op_type in ("Gemm", "MatMul"):
            a, b = shapes.get(n.input[0]), shapes.get(n.input[1])
            if a is None or b is None:
                continue
            m = int(np.prod(a[:-1])) * int(a[-1]) * int(b[-1] if n.op_type == "MatMul" or not _attr(n, "transB", 0) else b[0])
        else:
            continue
        macs += m
        per_node_macs[n.name] = m

    # ---- activation memory (logical graph over Q/DQ)
    alias = {}
    quantised = set()
    consumers = Counter(t for n in g.node for t in n.input)
    fused = set()  # Relu/Clip folded into the producing Conv's epilogue
    for n in g.node:
        p = producer.get(n.input[0]) if n.input else None
        if n.op_type in ("Relu", "Clip") and p is not None and p.op_type in ("Conv", "Gemm") \
                and consumers[n.input[0]] == 1:
            alias[n.output[0]] = n.input[0]
            fused.add(n.name or id(n))
    for n in g.node:
        if n.op_type in QDQ and n.input[0] not in inits:
            alias[n.output[0]] = n.input[0]
            if n.op_type == "QuantizeLinear":
                quantised.add(n.input[0])

    def root(t):
        while t in alias:
            t = alias[t]
        return t

    quantised_roots = {root(q) for q in quantised}

    def nbytes_root(t):
        s = shapes.get(t)
        return 0 if s is None else int(np.prod(s)) * (1 if t in quantised_roots else 4)

    compute_nodes = [n for n in g.node if n.op_type not in QDQ and n.op_type not in ("Constant",)
                     and (n.name or id(n)) not in fused]
    last_use = {}
    for i, n in enumerate(compute_nodes):
        for t in n.input:
            if t and t not in inits and t not in consts:
                last_use[root(t)] = i
    for o in g.output:
        last_use[root(o.name)] = len(compute_nodes)
    live = {}
    for inp in g.input:
        if inp.name not in inits:
            live[root(inp.name)] = nbytes_root(root(inp.name))
    peak, peak_at = sum(live.values()), "input"
    float_inputs = []
    for i, n in enumerate(compute_nodes):
        outs = {root(o): nbytes_root(root(o)) for o in n.output if o}
        cur = sum(live.values()) + sum(v for k, v in outs.items() if k not in live)
        if cur > peak:
            peak, peak_at = cur, n.name or n.op_type
        live.update(outs)
        for t in list(live):
            if last_use.get(t, -1) <= i:
                live.pop(t)
    for n in g.node:
        if n.op_type in COMPUTE and (n.name or id(n)) not in fused:
            for t in n.input[:1] if n.op_type in ("Conv", "Gemm", "MatMul") else n.input:
                if not t or t in inits or t in consts:
                    continue
                p = producer.get(t)
                if p is None or p.op_type != "DequantizeLinear":
                    float_inputs.append(f"{n.op_type} ({n.name}) input '{t}'")

    has_qdq = hist.get("QuantizeLinear", 0) > 0
    op_set = sorted(set(hist) - {"Constant", "Identity"})
    return {
        "model": path_or_model if isinstance(path_or_model, str) else g.name,
        "ops": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "op_set": op_set,
        "op_set_sha256": hashlib.sha256("\n".join(op_set).encode()).hexdigest(),
        "violations": violations,
        "int8": has_qdq,
        "float_compute_inputs": float_inputs,
        "params": params,
        "weight_bytes": weight_bytes,
        "macs": int(macs),
        "peak_activation_bytes": int(peak),
        "peak_activation_at": peak_at,
        "inputs": {i.name: shapes.get(i.name) for i in g.input if i.name not in inits},
        "outputs": {o.name: shapes.get(o.name) for o in g.output},
        "allowlist_note": "approximation of ST Neural-ART HW support; replace with ST Edge AI Core report on hardware",
    }


def format_report(r: dict) -> str:
    lines = [f"NPU-fit report: {r['model']}",
             f"  inputs : {r['inputs']}", f"  outputs: {r['outputs']}",
             f"  int8 QDQ: {r['int8']}",
             f"  params : {r['params']:,}  (weights {r['weight_bytes'] / 1024:.1f} KiB)",
             f"  MACs   : {r['macs'] / 1e6:.2f} M",
             f"  peak activations (est.): {r['peak_activation_bytes'] / 1024:.1f} KiB at {r['peak_activation_at']}",
             f"  op set sha256: {r['op_set_sha256'][:16]}…",
             "  op histogram:"]
    for k, v in r["ops"].items():
        tag = "" if k in ALLOWED else "   <-- NOT ALLOWED"
        lines.append(f"    {k:<20}{v:>5}{tag}")
    if r["violations"]:
        lines.append(f"  FAIL: {len(r['violations'])} violation(s):")
        lines += [f"    - {v}" for v in r["violations"]]
    else:
        lines.append("  PASS: all ops inside the (approximate) Neural-ART allowlist")
    if r["int8"]:
        lines.append(f"  float compute inputs (would run un-quantised): {len(r['float_compute_inputs'])}")
        lines += [f"    - {v}" for v in r["float_compute_inputs"][:10]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("models", nargs="+")
    ap.add_argument("--json", help="write the report(s) as JSON to this path")
    ap.add_argument("--require-int8", action="store_true",
                    help="also fail if a compute op consumes a non-quantised activation")
    a = ap.parse_args(argv)
    reports, rc = [], 0
    for p in a.models:
        r = analyze(p)
        reports.append(r)
        print(format_report(r))
        if r["violations"] or (a.require_int8 and (not r["int8"] or r["float_compute_inputs"])):
            rc = 1
    if a.json:
        with open(a.json, "w") as f:
            json.dump(reports if len(reports) > 1 else reports[0], f, indent=2)
    return rc


if __name__ == "__main__":
    sys.exit(main())
