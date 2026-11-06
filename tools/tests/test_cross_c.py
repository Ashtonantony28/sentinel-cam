"""Cross tests: images signed by sentinel_sign.py are verified by the C OTA
library (firmware/ota, via the ota_host_verify binary), both as a whole
buffer and streamed in chunks through the slot writer of a simulated device.
Tampered images must be rejected by C, and for every tamper the C error name
must equal the Python verifier's (differential test)."""
import random
import subprocess

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import sentinel_sign as ss

OPS = ["conv2d", "depthwise_conv2d", "relu6", "add", "avgpool"]
OPH = ss.op_set_hash(OPS)
LOAD = 0x70100000


@pytest.fixture(scope="module")
def k(keyfiles):
    return {
        "priv": ss.load_private_key(keyfiles["priv"]),
        "pub": ss.load_public_key(keyfiles["pub_pem"]),
        "pub_bin": keyfiles["pub_bin"],
    }


@pytest.fixture
def run_c(host_verify, tmp_path, k):
    counter = [0]

    def run(image: bytes, *extra, pub_bin=None, fw_image: bytes | None = None):
        counter[0] += 1
        p = tmp_path / f"img{counter[0]}.bin"
        p.write_bytes(image)
        cmd = [str(host_verify), "--pubkey", str(pub_bin or k["pub_bin"]), "--image", str(p), *map(str, extra)]
        if fw_image is not None:
            fp = tmp_path / f"fw{counter[0]}.bin"
            fp.write_bytes(fw_image)
            cmd += ["--fw-image", str(fp)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert r.returncode in (0, 1), f"usage error / crash: rc={r.returncode} {r.stdout} {r.stderr}"
        return r.returncode, r.stdout.strip()

    return run


def py_code(k, image, **kw):
    try:
        ss.verify_image(k["pub"], image, **kw)
        return "OK"
    except ss.VerifyError as e:
        return e.code


def c_code(out: str) -> str:
    return "OK" if out.startswith("OK") else out.split()[1]


def make(k, kind="fw_n6", version="1.0.0", ctr=1, payload=None, op=OPH, seed=0):
    rng = random.Random(seed)
    if payload is None:
        payload = bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 9000)))
    return ss.build_image(k["priv"], kind, version, ctr, payload,
                          load_addr=0 if kind == "model" else LOAD,
                          op_hash=None if kind == "fw_c6" else op)


# --------------------------------------------------------------- acceptance
@pytest.mark.parametrize("kind", ["fw_n6", "fw_c6", "model"])
def test_python_signed_verifies_in_c(k, run_c, kind):
    image = make(k, kind=kind, version="3.2.1", ctr=9, seed=1)
    rc, out = run_c(image, "--kind", kind)
    assert rc == 0, out
    m = ss.parse_manifest(image)
    assert out == (f"OK kind={ss.KINDS[kind]} version=3.2.1 counter=9 payload_size={m.payload_size} "
                   f"flags=0x{m.flags:x}")


@pytest.mark.parametrize("chunk", [1, 7, 255, 256, 257, 4096, 0])
@pytest.mark.parametrize("size", [1, 255, 256, 257, 4096, 70001])
def test_stream_fw_in_chunks(k, run_c, chunk, size):
    image = make(k, payload=bytes((i * 31 + size) & 0xFF for i in range(size)))
    rc, out = run_c(image, "--stream", chunk)
    assert rc == 0, out


@pytest.mark.parametrize("chunk", [3, 256, 0])
def test_stream_model_with_matching_firmware(k, run_c, chunk):
    fw = make(k, seed=2)
    model = make(k, kind="model", version="1.1.0", ctr=2, seed=3)
    rc, out = run_c(model, "--stream", chunk, fw_image=fw)
    assert rc == 0, out
    assert out.startswith("OK kind=3 version=1.1.0")


def test_high_s_signature_agreement(k, run_c):
    image = bytearray(make(k, seed=4))
    s = int.from_bytes(image[224:256], "big")
    image[224:256] = (ss.P256_N - s).to_bytes(32, "big")  # the other valid ECDSA signature
    assert py_code(k, bytes(image)) == "OK"
    assert run_c(bytes(image))[0] == 0


# ---------------------------------------------------------------- rejection
FIELDS = {
    # name: (offset, new bytes)
    "magic": (0, b"SOTB"),
    "manifest_version": (4, b"\x02\x00"),
    "kind": (6, b"\x03"),
    "reserved7": (7, b"\x01"),
    "version": (8, b"\x00\x00\x02\x00"),
    "security_counter": (12, b"\xff\x00\x00\x00"),
    "payload_size_smaller": (16, None),
    "payload_size_larger": (16, None),
    "payload_sha256": (20, b"\x00" * 32),
    "load_addr": (52, b"\x00\x00\x00\x08"),
    "load_addr_zero": (52, b"\x00\x00\x00\x00"),
    "flags_clear": (56, b"\x00\x00\x00\x00"),
    "flags_unknown": (56, b"\x03\x00\x00\x00"),
    "op_set_hash": (60, b"\x11" * 32),
    "reserved_tail": (191, b"\x01"),
    "sig_r": (192, None),
    "sig_s": (255, None),
}


@pytest.mark.parametrize("field", sorted(FIELDS))
@pytest.mark.parametrize("mode", ["buffer", "stream"])
def test_each_manifest_field_tamper_rejected(k, run_c, field, mode):
    image = bytearray(make(k, ctr=5, seed=5))
    off, val = FIELDS[field]
    psize = ss.parse_manifest(bytes(image)).payload_size
    if field == "payload_size_smaller":
        val = (psize - 1).to_bytes(4, "little")
    elif field == "payload_size_larger":
        val = (psize + 1).to_bytes(4, "little")
    elif field in ("sig_r", "sig_s"):
        val = bytes([image[off] ^ 0x80])
    image[off : off + len(val)] = val
    image = bytes(image)
    want = py_code(k, image)
    assert want != "OK"
    rc, out = run_c(image, *(["--stream", 0, "--kind", "fw_n6"] if mode == "stream" else []))
    assert rc == 1, f"C accepted tampered {field}: {out}"
    if mode == "buffer":
        assert c_code(out) == want, f"{field}: C {out} vs Python {want}"


def test_every_manifest_byte_differential(k, run_c):
    """Flip one random bit in each of the 256 manifest bytes (signature bytes
    included): C rejects every one with the same error name as Python."""
    rng = random.Random(1234)
    good = make(k, seed=6)
    for i in range(256):
        image = bytearray(good)
        image[i] ^= 1 << rng.randrange(8)
        image = bytes(image)
        want = py_code(k, image)
        rc, out = run_c(image)
        assert rc == 1 and want != "OK", f"byte {i}: {out}"
        assert c_code(out) == want, f"byte {i}: C {out} vs Python {want}"


def test_payload_tamper_rejected(k, run_c):
    rng = random.Random(99)
    good = make(k, payload=bytes(rng.getrandbits(8) for _ in range(20000)))
    for n, pos in enumerate(rng.sample(range(256, len(good)), 24)):
        image = bytearray(good)
        image[pos] ^= 1 << rng.randrange(8)
        image = bytes(image)
        assert py_code(k, image) == "ERR_HASH"
        rc, out = run_c(image, *(["--stream", rng.choice([1, 100, 0])] if n % 4 == 0 else []))
        assert (rc, c_code(out)) == (1, "ERR_HASH"), pos


def test_wrong_key_rejected(k, run_c, tmp_path):
    other = ec.generate_private_key(ec.SECP256R1())
    image = ss.build_image(other, "fw_n6", "1.0.0", 1, b"payload", load_addr=LOAD, op_hash=OPH)
    assert run_c(image) == (1, "REJECT ERR_SIGNATURE")
    assert run_c(image, "--stream", 64) == (1, "REJECT ERR_SIGNATURE")
    # and an image from our key is rejected under the other key
    pb = tmp_path / "other.bin"
    pb.write_bytes(ss.public_key_raw(other.public_key()))
    assert run_c(make(k, seed=7), pub_bin=pb) == (1, "REJECT ERR_SIGNATURE")


def test_rollback_rejected(k, run_c):
    image = make(k, ctr=7, seed=8)
    assert run_c(image, "--min-counter", 7)[0] == 0
    assert run_c(image, "--min-counter", 8) == (1, "REJECT ERR_ROLLBACK")
    assert run_c(image, "--min-counter", 8, "--stream", 300) == (1, "REJECT ERR_ROLLBACK")
    assert py_code(k, image, min_counter=8) == "ERR_ROLLBACK"


def test_model_opset_mismatch_rejected(k, run_c):
    model = make(k, kind="model", seed=9)
    other = ss.op_set_hash(OPS + ["gelu"])
    assert run_c(model, "--op-hash", OPH.hex())[0] == 0
    assert run_c(model, "--op-hash", other.hex()) == (1, "REJECT ERR_OPSET")
    fw_other = make(k, op=other, seed=10)
    assert run_c(model, "--stream", 0, fw_image=fw_other) == (1, "REJECT ERR_OPSET")
    assert py_code(k, model, required_op_hash=other) == "ERR_OPSET"


def test_kind_mismatch_rejected(k, run_c):
    model = make(k, kind="model", seed=11)
    assert run_c(model, "--kind", "fw_n6") == (1, "REJECT ERR_KIND")
    # the N6 slot writer does not accept C6 firmware (ESP-IDF applies it)
    assert run_c(make(k, kind="fw_c6", seed=12), "--stream", 256) == (1, "REJECT ERR_KIND")


@pytest.mark.parametrize("mode", ["buffer", "stream"])
def test_truncated_and_overlong(k, run_c, mode):
    good = make(k, seed=13)
    extra = ["--stream", 333] if mode == "stream" else []
    for cut in (0, 1, 100, 255, 256, 257, len(good) - 1):
        image = good[:cut]
        rc, out = run_c(image, *extra)
        assert (rc, c_code(out)) == (1, "ERR_TRUNCATED"), cut
    assert run_c(good + b"\x00", *extra) == (1, "REJECT ERR_OVERFLOW")
