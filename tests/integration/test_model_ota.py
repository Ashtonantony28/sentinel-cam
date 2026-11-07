"""3c: ml export -> npu_fit op-set hash -> sentinel_sign (kind=model) -> firmware/ota verify.

A randomly initialised, tiny person detector is enough: the point is that the hash ml
computes, the hash the signing tool writes and the hash the C verifier compares agree."""
import subprocess
import sys

import pytest

from conftest import REPO

SIGN = REPO / "tools" / "sentinel_sign.py"
LOAD = 0x70000000


def _sign(*args):
    r = subprocess.run([sys.executable, str(SIGN), *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    import torch
    from sentinel_ml import npu_fit
    from sentinel_ml.models import build_person_detector
    from sentinel_ml.quant import calibrate_ptq, export_quant_onnx, prepare_quant

    torch.manual_seed(0)
    d = tmp_path_factory.mktemp("model")
    m = build_person_detector(width=0.25, input_size=64).eval()
    q = calibrate_ptq(prepare_quant(m, ptq=True), [torch.rand(2, 3, 64, 64) for _ in range(2)])
    path = d / "person_int8.onnx"
    export_quant_onnx(q, str(path), 64, m.output_names())
    rep = npu_fit.analyze(str(path))
    ops = d / "ops.txt"
    ops.write_text("".join(o + "\n" for o in rep["op_set"]))
    return {"dir": d, "onnx": path, "report": rep, "ops": ops}


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    _sign("keygen", "--out-dir", d, "--name", "it")
    return {"priv": d / "it.key.pem", "pub_bin": d / "it.pub.bin", "pub_pem": d / "it.pub.pem"}


def _image(keys, model, out, kind, op_hash, payload=None, counter=1, version="0.2.0"):
    args = ["sign", "--key", keys["priv"], "--kind", kind, "--version", version,
            "--security-counter", counter, "--payload", payload or model["onnx"], "--out", out,
            "--op-set-hash", op_hash]
    if kind != "model":
        args += ["--load-addr", hex(LOAD)]
    _sign(*args)
    return out


def _verify(fw_bin, keys, image, *extra):
    r = subprocess.run([str(fw_bin("ota/ota_host_verify")), "--pubkey", str(keys["pub_bin"]),
                        "--image", str(image), *map(str, extra)], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def test_model_is_int8_and_fits_npu(model):
    rep = model["report"]
    assert not rep["violations"], rep["violations"]
    assert {"quantizelinear", "dequantizelinear", "conv"} <= set(rep["op_set"])


def test_ml_and_tool_opset_hash_agree(model):
    assert _sign("opset", model["ops"]).strip() == model["report"]["op_set_sha256"]


def test_signed_model_inspects_with_ml_hash(keys, model):
    img = _image(keys, model, model["dir"] / "m.img", "model", model["report"]["op_set_sha256"])
    import json
    info = json.loads(_sign("inspect", img, "--json"))
    assert info["kind"] in ("model", 3)
    assert model["report"]["op_set_sha256"] in json.dumps(info)


def test_c_verify_accepts_matching_and_rejects_other_opset(fw_bin, keys, model):
    h = model["report"]["op_set_sha256"]
    img = _image(keys, model, model["dir"] / "m.img", "model", h)
    rc, out = _verify(fw_bin, keys, img, "--kind", "model", "--op-hash", h)
    assert (rc, out.split()[0]) == (0, "OK"), out
    other = "%064x" % (int(h, 16) ^ 1)
    assert _verify(fw_bin, keys, img, "--kind", "model", "--op-hash", other) == (1, "REJECT ERR_OPSET")
    # python verifier agrees
    assert _sign("verify", "--pubkey", keys["pub_pem"], img, "--kind", "model", "--op-set-hash", h).startswith("OK")


def test_device_flow_firmware_opset_gates_model(fw_bin, keys, model):
    """Stream through the simulated device: install fw_n6 (declaring its supported op set),
    then the model. Accept when the firmware's op-set hash matches, reject otherwise."""
    h = model["report"]["op_set_sha256"]
    fw_payload = model["dir"] / "fw.bin"
    fw_payload.write_bytes(bytes(range(256)) * 64)
    fw_ok = _image(keys, model, model["dir"] / "fw_ok.img", "fw_n6", h, payload=fw_payload)
    other_ops = model["dir"] / "ops_other.txt"
    other_ops.write_text(model["ops"].read_text() + "hardswish\n")
    other = _sign("opset", other_ops).strip()
    fw_bad = _image(keys, model, model["dir"] / "fw_bad.img", "fw_n6", other, payload=fw_payload)
    img = _image(keys, model, model["dir"] / "m.img", "model", h)
    rc, out = _verify(fw_bin, keys, img, "--stream", 0, "--fw-image", fw_ok)
    assert rc == 0 and out.startswith("OK kind=3"), out
    assert _verify(fw_bin, keys, img, "--stream", 0, "--fw-image", fw_bad) == (1, "REJECT ERR_OPSET")


def test_tampered_model_payload_rejected(fw_bin, keys, model):
    h = model["report"]["op_set_sha256"]
    img = bytearray(_image(keys, model, model["dir"] / "m.img", "model", h).read_bytes())
    img[300] ^= 0x01
    bad = model["dir"] / "m_bad.img"
    bad.write_bytes(bytes(img))
    rc, out = _verify(fw_bin, keys, bad, "--kind", "model", "--op-hash", h)
    assert rc == 1 and out.startswith("REJECT"), out
