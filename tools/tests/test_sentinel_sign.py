"""Unit tests for tools/sentinel_sign.py (pure Python side)."""
import hashlib
import json
import os
import stat
import struct

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import sentinel_sign as ss

OPS = ["conv2d", "relu", "add", "maxpool"]


def img(key, kind="fw_n6", version="1.2.3", ctr=4, payload=b"\xAA" * 1000, **kw):
    if kind != "model":
        kw.setdefault("load_addr", 0x70100000)
    if kind != "fw_c6":
        kw.setdefault("op_hash", ss.op_set_hash(OPS))
    return ss.build_image(key, kind, version, ctr, payload, **kw)


# ------------------------------------------------------------------ helpers
def test_pack_version():
    assert ss.pack_version("1.2.3") == 0x010203
    assert ss.pack_version("0.0.0") == 0
    assert ss.pack_version("65535.255.255") == 0xFFFFFFFF
    assert ss.unpack_version(0x0A0B0C) == "10.11.12"
    for bad in ["1.2", "1.2.3.4", "1.256.0", "65536.0.0", "a.b.c", "-1.0.0", ""]:
        with pytest.raises(ValueError):
            ss.pack_version(bad)


def test_op_set_hash_canonical():
    h = ss.op_set_hash(OPS)
    assert h == ss.op_set_hash(["relu", "ADD", " conv2d ", "maxpool", "relu", "# comment", ""])
    assert h == hashlib.sha256(b"sentinel-opset-v1\nadd\nconv2d\nmaxpool\nrelu\n").digest()
    assert h != ss.op_set_hash(OPS + ["softmax"])
    assert len(h) == 32


# ---------------------------------------------------------------- manifest
def test_manifest_layout_matches_interfaces(key):
    payload = bytes(range(256)) * 3
    image = img(key, version="2.5.9", ctr=77, payload=payload, load_addr=0x70123400)
    m = image[:256]
    assert m[0:4] == b"SOTA"
    assert struct.unpack_from("<H", m, 4)[0] == 1
    assert m[6] == 1 and m[7] == 0
    assert struct.unpack_from("<I", m, 8)[0] == 0x020509
    assert struct.unpack_from("<I", m, 12)[0] == 77
    assert struct.unpack_from("<I", m, 16)[0] == len(payload)
    assert m[20:52] == hashlib.sha256(payload).digest()
    assert struct.unpack_from("<I", m, 52)[0] == 0x70123400
    assert struct.unpack_from("<I", m, 56)[0] == 1  # requires_reboot default for fw
    assert m[60:92] == ss.op_set_hash(OPS)
    assert m[92:192] == b"\x00" * 100
    assert image[256:] == payload
    assert len(image) == 256 + len(payload)


def test_roundtrip_all_kinds(key):
    pub = key.public_key()
    for kind in ss.KINDS:
        image = img(key, kind=kind)
        m = ss.verify_image(pub, image, expect_kind=ss.KINDS[kind])
        assert m.kind == ss.KINDS[kind]
        assert m.flags == (0 if kind == "model" else 1)
    m = ss.parse_manifest(img(key, kind="fw_c6"))
    assert m.op_set_hash == b"\x00" * 32


def test_signature_is_raw_r_s_over_first_192_bytes(key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    image = img(key)
    r = int.from_bytes(image[192:224], "big")
    s = int.from_bytes(image[224:256], "big")
    key.public_key().verify(encode_dss_signature(r, s), image[:192], ec.ECDSA(hashes.SHA256()))


def test_build_rejects_bad_arguments(key):
    oh = ss.op_set_hash(OPS)
    with pytest.raises(ValueError):
        ss.build_image(key, "model", "1.0.0", 1, b"x", load_addr=0x1000, op_hash=oh)
    with pytest.raises(ValueError):
        ss.build_image(key, "fw_n6", "1.0.0", 1, b"x", load_addr=0, op_hash=oh)
    with pytest.raises(ValueError):
        ss.build_image(key, "fw_n6", "1.0.0", 1, b"x", load_addr=0x1000)  # no op hash
    with pytest.raises(ValueError):
        ss.build_image(key, "fw_n6", "1.0.0", 1, b"", load_addr=0x1000, op_hash=oh)
    with pytest.raises(ValueError):
        ss.build_image(key, "fw_n6", "1.0.0", -1, b"x", load_addr=0x1000, op_hash=oh)
    with pytest.raises(ValueError):
        ss.build_image(key, "fw_n6", "1.0.0", 2**32, b"x", load_addr=0x1000, op_hash=oh)
    with pytest.raises(ValueError):
        ss.build_image(key, "bootloader", "1.0.0", 1, b"x", load_addr=0x1000, op_hash=oh)


# ------------------------------------------------------------------ verify
def test_verify_rejections(key):
    pub = key.public_key()
    good = img(key, ctr=5)
    other = ec.generate_private_key(ec.SECP256R1()).public_key()

    def code(image, **kw):
        with pytest.raises(ss.VerifyError) as e:
            ss.verify_image(kw.pop("pub", pub), image, **kw)
        return e.value.code

    assert code(good, pub=other) == "ERR_SIGNATURE"
    assert code(good, expect_kind=3) == "ERR_KIND"
    assert code(good, min_counter=6) == "ERR_ROLLBACK"
    ss.verify_image(pub, good, min_counter=5)
    assert code(good[:-1]) == "ERR_TRUNCATED"
    assert code(good + b"\x00") == "ERR_OVERFLOW"
    assert code(good[:100]) == "ERR_TRUNCATED"
    model = img(key, kind="model")
    assert code(model, required_op_hash=ss.op_set_hash(["relu"])) == "ERR_OPSET"
    ss.verify_image(pub, model, required_op_hash=ss.op_set_hash(OPS))
    # op-set requirement does not apply to firmware
    ss.verify_image(pub, good, required_op_hash=ss.op_set_hash(["relu"]))
    bad = bytearray(good)
    bad[-1] ^= 1
    assert code(bytes(bad)) == "ERR_HASH"
    # every manifest byte is covered
    for i in range(256):
        bad = bytearray(good)
        bad[i] ^= 0x01
        with pytest.raises(ss.VerifyError):
            ss.verify_image(pub, bytes(bad))
    # r = 0 and s = n are rejected before the library call
    bad = bytearray(good)
    bad[192:224] = b"\x00" * 32
    assert code(bytes(bad)) == "ERR_SIGNATURE"
    bad = bytearray(good)
    bad[224:256] = ss.P256_N.to_bytes(32, "big")
    assert code(bytes(bad)) == "ERR_SIGNATURE"


def test_parse_errors(key):
    good = bytearray(img(key))
    cases = {
        0: (b"X", "ERR_MAGIC"),
        4: (b"\x02", "ERR_MANIFEST_VERSION"),
        6: (b"\x09", "ERR_KIND"),
        7: (b"\x01", "ERR_RESERVED"),
        150: (b"\x01", "ERR_RESERVED"),
        56: (b"\x03", "ERR_FLAGS"),
    }
    for off, (val, want) in cases.items():
        b = bytearray(good)
        b[off : off + 1] = val
        with pytest.raises(ss.VerifyError) as e:
            ss.parse_manifest(bytes(b))
        assert e.value.code == want, off


# --------------------------------------------------------------------- CLI
def test_keygen_files(keyfiles):
    assert stat.S_IMODE(os.stat(keyfiles["priv"]).st_mode) == 0o600
    raw = keyfiles["pub_bin"].read_bytes()
    assert len(raw) == 64
    pub = ss.load_public_key(keyfiles["pub_pem"])
    assert ss.public_key_raw(pub) == raw
    assert ss.public_key_raw(ss.load_public_key(keyfiles["pub_bin"])) == raw
    h = keyfiles["pub_h"].read_text()
    assert "T_PUBKEY[64]" in h and f"0x{raw[0]:02x}" in h
    # refuses to overwrite
    assert ss.main(["keygen", "--out-dir", str(keyfiles["priv"].parent), "--name", "t"]) == 1


def test_cli_sign_verify_inspect(tmp_path, keyfiles, capsys):
    payload = tmp_path / "fw.bin"
    payload.write_bytes(os.urandom(5000))
    ops = tmp_path / "ops.txt"
    ops.write_text("conv2d\nrelu\n# fused\nadd\n")
    out = tmp_path / "fw.sota"
    rc = ss.main(["sign", "--key", str(keyfiles["priv"]), "--kind", "fw_n6", "--version", "1.4.2",
                  "--security-counter", "3", "--payload", str(payload), "--out", str(out),
                  "--load-addr", "0x70100000", "--op-set-file", str(ops)])
    assert rc == 0
    capsys.readouterr()
    assert ss.main(["verify", "--pubkey", str(keyfiles["pub_pem"]), str(out)]) == 0
    assert "OK kind=1 version=1.4.2 counter=3" in capsys.readouterr().out
    assert ss.main(["verify", "--pubkey", str(keyfiles["pub_bin"]), str(out), "--min-counter", "4"]) == 1
    assert "REJECT ERR_ROLLBACK" in capsys.readouterr().out
    assert ss.main(["verify", "--pubkey", str(keyfiles["pub_bin"]), str(out), "--kind", "model"]) == 1
    assert "REJECT ERR_KIND" in capsys.readouterr().out
    assert ss.main(["inspect", str(out), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["version"] == "1.4.2" and d["kind"] == "fw_n6" and d["payload_size"] == 5000
    assert d["load_addr"] == "0x70100000" and d["requires_reboot"] is True
    assert d["op_set_hash"] == ss.op_set_hash(["add", "conv2d", "relu"]).hex()
    assert ss.main(["opset", str(ops)]) == 0
    assert capsys.readouterr().out.strip() == d["op_set_hash"]
    # model with a load address is a usage error
    rc = ss.main(["sign", "--key", str(keyfiles["priv"]), "--kind", "model", "--version", "1.0.0",
                  "--security-counter", "1", "--payload", str(payload), "--out", str(out),
                  "--load-addr", "0x1000", "--op-set-file", str(ops)])
    assert rc == 2
    # inspect of garbage
    junk = tmp_path / "junk"
    junk.write_bytes(b"\x00" * 300)
    assert ss.main(["inspect", str(junk)]) == 1
