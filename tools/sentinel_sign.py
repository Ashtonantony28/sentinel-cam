#!/usr/bin/env python3
"""sentinel_sign: build, sign, verify and inspect Sentinel OTA images.

Image format: docs/INTERFACES.md section 4. A 256-byte manifest followed by
the payload. The ECDSA P-256 signature (r||s, big-endian) at [192, 256) is
over SHA-256 of manifest bytes [0, 192); the manifest binds the payload via
payload_sha256.

Commands:
  keygen  --out-dir DIR [--name NAME] [--force]
  sign    --key KEY.pem --kind {fw_n6,fw_c6,model} --version X.Y.Z
          --security-counter N --payload FILE --out IMAGE
          [--load-addr ADDR] [--requires-reboot | --no-requires-reboot]
          [--op-set-hash HEX | --op-set-file FILE]
  verify  --pubkey PUB(.pem|.bin) IMAGE [--kind K] [--min-counter N]
          [--op-set-hash HEX | --op-set-file FILE]
  inspect IMAGE [--json]
  opset   FILE            print the canonical op-set hash of an op list

verify mirrors the C library's check order and error names exactly
(firmware/ota/src/ota_manifest.c), so the two can be compared directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

MANIFEST_SIZE = 256
SIGNED_LEN = 192
MAGIC = b"SOTA"
MANIFEST_VERSION = 1
KINDS = {"fw_n6": 1, "fw_c6": 2, "model": 3}
KIND_NAMES = {v: k for k, v in KINDS.items()}
FLAG_REQUIRES_REBOOT = 0x1
FLAGS_KNOWN = FLAG_REQUIRES_REBOOT
OPSET_DOMAIN = b"sentinel-opset-v1\n"

# offset table (INTERFACES.md section 4)
OFF_MAGIC, OFF_MVER, OFF_KIND, OFF_RSV0 = 0, 4, 6, 7
OFF_VERSION, OFF_SECCTR, OFF_PSIZE, OFF_PSHA = 8, 12, 16, 20
OFF_LOADADDR, OFF_FLAGS, OFF_OPHASH, OFF_RSV1, OFF_SIG = 52, 56, 60, 92, 192

# <4s H B B I I I 32s I I 32s 100s  == 192 bytes
_SIGNED_FMT = "<4sHBBIII32sII32s100s"
assert struct.calcsize(_SIGNED_FMT) == SIGNED_LEN

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


class VerifyError(Exception):
    """Raised with the same error name the C library returns (e.g. ERR_SIGNATURE)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


# --------------------------------------------------------------------- helpers
def pack_version(text: str) -> int:
    """'major.minor.patch' -> major<<16 | minor<<8 | patch."""
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"version must be major.minor.patch, got {text!r}")
    major, minor, patch = (int(p) for p in parts)
    if not (0 <= major <= 0xFFFF and 0 <= minor <= 0xFF and 0 <= patch <= 0xFF):
        raise ValueError(f"version {text!r} out of range (major<=65535, minor/patch<=255)")
    return (major << 16) | (minor << 8) | patch


def unpack_version(v: int) -> str:
    return f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}"


def canonical_op_set(ops: Iterable[str]) -> bytes:
    """Canonical op-set encoding: unique op names, stripped, lower-cased, sorted,
    each followed by a newline. '#' starts a comment."""
    names = set()
    for line in ops:
        line = line.split("#", 1)[0].strip().lower()
        if line:
            names.add(line)
    return "".join(n + "\n" for n in sorted(names)).encode("utf-8")


def op_set_hash(ops: Iterable[str]) -> bytes:
    """sha256(b'sentinel-opset-v1\\n' + canonical_op_set(ops))."""
    return hashlib.sha256(OPSET_DOMAIN + canonical_op_set(ops)).digest()


def load_private_key(path: str | os.PathLike, password: bytes | None = None) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=password)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("signing key must be an EC P-256 private key")
    return key


def load_public_key(path: str | os.PathLike) -> ec.EllipticCurvePublicKey:
    data = Path(path).read_bytes()
    if data.startswith(b"-----BEGIN"):
        key = serialization.load_pem_public_key(data)
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise ValueError("public key must be EC P-256")
        return key
    if len(data) == 64:
        data = b"\x04" + data
    if len(data) != 65 or data[0] != 4:
        raise ValueError("raw public key must be 64 bytes X||Y (or 65 with 0x04 prefix)")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)


def public_key_raw(pub: ec.EllipticCurvePublicKey) -> bytes:
    """64-byte X||Y, the format the firmware embeds."""
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)[1:]


# -------------------------------------------------------------------- manifest
@dataclass
class Manifest:
    kind: int
    version: int
    security_counter: int
    payload_size: int
    payload_sha256: bytes
    load_addr: int = 0
    flags: int = 0
    op_set_hash: bytes = b"\x00" * 32
    manifest_version: int = MANIFEST_VERSION
    signature: bytes = field(default=b"\x00" * 64, repr=False)

    def signed_bytes(self) -> bytes:
        for name, v, hi in (
            ("version", self.version, 0xFFFFFFFF),
            ("security_counter", self.security_counter, 0xFFFFFFFF),
            ("payload_size", self.payload_size, 0xFFFFFFFF),
            ("load_addr", self.load_addr, 0xFFFFFFFF),
            ("flags", self.flags, 0xFFFFFFFF),
        ):
            if not 0 <= v <= hi:
                raise ValueError(f"{name}={v} does not fit in u32")
        if len(self.payload_sha256) != 32 or len(self.op_set_hash) != 32:
            raise ValueError("hashes must be 32 bytes")
        return struct.pack(
            _SIGNED_FMT,
            MAGIC,
            self.manifest_version,
            self.kind,
            0,
            self.version,
            self.security_counter,
            self.payload_size,
            self.payload_sha256,
            self.load_addr,
            self.flags,
            self.op_set_hash,
            b"\x00" * 100,
        )

    def to_bytes(self) -> bytes:
        return self.signed_bytes() + self.signature

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "kind": KIND_NAMES.get(self.kind, self.kind),
            "version": unpack_version(self.version),
            "version_packed": self.version,
            "security_counter": self.security_counter,
            "payload_size": self.payload_size,
            "payload_sha256": self.payload_sha256.hex(),
            "load_addr": f"0x{self.load_addr:08x}",
            "flags": self.flags,
            "requires_reboot": bool(self.flags & FLAG_REQUIRES_REBOOT),
            "op_set_hash": self.op_set_hash.hex(),
            "signature": self.signature.hex(),
        }


def parse_manifest(raw: bytes) -> Manifest:
    """Decode + structural validation, same order and error names as ota_manifest_parse()."""
    if len(raw) < MANIFEST_SIZE:
        raise VerifyError("ERR_TRUNCATED", "image shorter than the 256-byte manifest")
    raw = raw[:MANIFEST_SIZE]
    (magic, mver, kind, rsv0, version, ctr, psize, psha, load, flags, ophash, rsv1) = struct.unpack(
        _SIGNED_FMT, raw[:SIGNED_LEN]
    )
    if magic != MAGIC:
        raise VerifyError("ERR_MAGIC")
    if mver != MANIFEST_VERSION:
        raise VerifyError("ERR_MANIFEST_VERSION", str(mver))
    if kind not in KIND_NAMES:
        raise VerifyError("ERR_KIND", str(kind))
    if rsv0 != 0 or any(rsv1):
        raise VerifyError("ERR_RESERVED")
    if flags & ~FLAGS_KNOWN:
        raise VerifyError("ERR_FLAGS", hex(flags))
    if psize == 0 or psize > 0xFFFFFFFF - MANIFEST_SIZE:
        raise VerifyError("ERR_SIZE")
    if kind == KINDS["model"]:
        if load != 0:
            raise VerifyError("ERR_LOAD_ADDR", "model load_addr must be 0")
    elif load == 0:
        raise VerifyError("ERR_LOAD_ADDR", "firmware load_addr must be non-zero")
    return Manifest(kind, version, ctr, psize, psha, load, flags, ophash, mver, raw[OFF_SIG:MANIFEST_SIZE])


def sign_manifest(m: Manifest, key: ec.EllipticCurvePrivateKey) -> Manifest:
    der = key.sign(m.signed_bytes(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    m.signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return m


def build_image(
    key: ec.EllipticCurvePrivateKey,
    kind: str,
    version: str | int,
    security_counter: int,
    payload: bytes,
    load_addr: int = 0,
    requires_reboot: bool | None = None,
    op_hash: bytes | None = None,
) -> bytes:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    if not payload:
        raise ValueError("payload must not be empty")
    k = KINDS[kind]
    if k == KINDS["model"]:
        if load_addr:
            raise ValueError("model images must have load_addr 0")
    elif not load_addr:
        raise ValueError("firmware images need a non-zero --load-addr")
    if requires_reboot is None:
        requires_reboot = k != KINDS["model"]
    if op_hash is None:
        if k == KINDS["fw_c6"]:
            op_hash = b"\x00" * 32
        else:
            raise ValueError("fw_n6 and model images need an op-set hash (--op-set-hash or --op-set-file)")
    if len(op_hash) != 32:
        raise ValueError("op-set hash must be 32 bytes")
    if security_counter < 0:
        raise ValueError("security counter must be >= 0")
    v = pack_version(version) if isinstance(version, str) else int(version)
    m = Manifest(
        kind=k,
        version=v,
        security_counter=security_counter,
        payload_size=len(payload),
        payload_sha256=hashlib.sha256(payload).digest(),
        load_addr=load_addr,
        flags=FLAG_REQUIRES_REBOOT if requires_reboot else 0,
        op_set_hash=op_hash,
    )
    sign_manifest(m, key)
    return m.to_bytes() + payload


def verify_image(
    pub: ec.EllipticCurvePublicKey,
    image: bytes,
    expect_kind: int = 0,
    min_counter: int = 0,
    required_op_hash: bytes | None = None,
    max_payload_size: int = 0,
) -> Manifest:
    """Same checks, order and error names as ota_image_verify_buffer()."""
    if len(image) < MANIFEST_SIZE:
        raise VerifyError("ERR_TRUNCATED")
    m = parse_manifest(image)
    sig = image[OFF_SIG:MANIFEST_SIZE]
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (0 < r < P256_N and 0 < s < P256_N):
        raise VerifyError("ERR_SIGNATURE", "r or s out of range")
    try:
        pub.verify(encode_dss_signature(r, s), image[:SIGNED_LEN], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise VerifyError("ERR_SIGNATURE") from None
    if expect_kind and m.kind != expect_kind:
        raise VerifyError("ERR_KIND", f"expected {expect_kind}, image is {m.kind}")
    if max_payload_size and m.payload_size > max_payload_size:
        raise VerifyError("ERR_SIZE")
    if m.security_counter < min_counter:
        raise VerifyError("ERR_ROLLBACK", f"counter {m.security_counter} < {min_counter}")
    if required_op_hash is not None and m.kind == KINDS["model"] and m.op_set_hash != required_op_hash:
        raise VerifyError("ERR_OPSET")
    have = len(image) - MANIFEST_SIZE
    if have < m.payload_size:
        raise VerifyError("ERR_TRUNCATED")
    if have > m.payload_size:
        raise VerifyError("ERR_OVERFLOW")
    if hashlib.sha256(image[MANIFEST_SIZE:]).digest() != m.payload_sha256:
        raise VerifyError("ERR_HASH")
    return m


# ------------------------------------------------------------------------- CLI
def _int(text: str) -> int:
    return int(text, 0)


def _op_hash_from_args(args) -> bytes | None:
    if getattr(args, "op_set_hash", None):
        h = bytes.fromhex(args.op_set_hash)
        if len(h) != 32:
            raise ValueError("--op-set-hash must be 64 hex characters")
        return h
    if getattr(args, "op_set_file", None):
        return op_set_hash(Path(args.op_set_file).read_text(encoding="utf-8").splitlines())
    return None


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)


def cmd_keygen(args) -> int:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {s: out / f"{args.name}{s}" for s in (".key.pem", ".pub.pem", ".pub.bin", ".pub.h")}
    existing = [str(p) for p in paths.values() if p.exists()]
    if existing and not args.force:
        print(f"refusing to overwrite {', '.join(existing)} (use --force)", file=sys.stderr)
        return 1
    key = ec.generate_private_key(ec.SECP256R1())
    _write_private(
        paths[".key.pem"],
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    )
    pub = key.public_key()
    paths[".pub.pem"].write_bytes(
        pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    raw = public_key_raw(pub)
    paths[".pub.bin"].write_bytes(raw)
    ident = "".join(c if c.isalnum() else "_" for c in args.name).upper()
    body = ",\n".join(
        "    " + ", ".join(f"0x{b:02x}" for b in raw[i : i + 8]) for i in range(0, 64, 8)
    )
    paths[".pub.h"].write_text(
        f"/* Generated by sentinel_sign.py keygen. ECDSA P-256 public key, X||Y big-endian. */\n"
        f"#pragma once\n#include <stdint.h>\n"
        f"static const uint8_t {ident}_PUBKEY[64] = {{\n{body}\n}};\n"
    )
    print(f"wrote {', '.join(str(p) for p in paths.values())}")
    print(f"pubkey sha256 (for OTP provisioning): {hashlib.sha256(raw).hexdigest()}")
    return 0


def cmd_sign(args) -> int:
    key = load_private_key(args.key)
    payload = Path(args.payload).read_bytes()
    image = build_image(
        key,
        args.kind,
        args.version,
        args.security_counter,
        payload,
        load_addr=args.load_addr,
        requires_reboot=args.requires_reboot,
        op_hash=_op_hash_from_args(args),
    )
    Path(args.out).write_bytes(image)
    m = parse_manifest(image)
    print(f"signed {args.kind} {unpack_version(m.version)} ctr={m.security_counter} "
          f"payload={m.payload_size} bytes -> {args.out}")
    return 0


def cmd_verify(args) -> int:
    pub = load_public_key(args.pubkey)
    image = Path(args.image).read_bytes()
    kind = 0
    if args.kind:
        kind = KINDS[args.kind] if args.kind in KINDS else int(args.kind, 0)
    try:
        m = verify_image(pub, image, kind, args.min_counter, _op_hash_from_args(args))
    except VerifyError as e:
        print(f"REJECT {e.code}")
        return 1
    print(f"OK kind={m.kind} version={unpack_version(m.version)} counter={m.security_counter} "
          f"payload_size={m.payload_size} flags=0x{m.flags:x}")
    return 0


def cmd_inspect(args) -> int:
    image = Path(args.image).read_bytes()
    try:
        m = parse_manifest(image)
    except VerifyError as e:
        print(f"INVALID {e.code}", file=sys.stderr)
        return 1
    d = m.to_dict()
    d["image_size"] = len(image)
    d["payload_present"] = len(image) - MANIFEST_SIZE
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        for k, v in d.items():
            print(f"{k:18} {v}")
    return 0


def cmd_opset(args) -> int:
    print(op_set_hash(Path(args.file).read_text(encoding="utf-8").splitlines()).hex())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sentinel_sign", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="generate an ECDSA P-256 signing key")
    k.add_argument("--out-dir", required=True)
    k.add_argument("--name", default="sentinel_ota")
    k.add_argument("--force", action="store_true")
    k.set_defaults(fn=cmd_keygen)

    s = sub.add_parser("sign", help="build and sign an image")
    s.add_argument("--key", required=True)
    s.add_argument("--kind", required=True, choices=sorted(KINDS))
    s.add_argument("--version", required=True)
    s.add_argument("--security-counter", required=True, type=_int)
    s.add_argument("--payload", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--load-addr", type=_int, default=0)
    rb = s.add_mutually_exclusive_group()
    rb.add_argument("--requires-reboot", dest="requires_reboot", action="store_true", default=None)
    rb.add_argument("--no-requires-reboot", dest="requires_reboot", action="store_false")
    oh = s.add_mutually_exclusive_group()
    oh.add_argument("--op-set-hash")
    oh.add_argument("--op-set-file")
    s.set_defaults(fn=cmd_sign)

    v = sub.add_parser("verify", help="verify an image")
    v.add_argument("--pubkey", required=True)
    v.add_argument("image")
    v.add_argument("--kind")
    v.add_argument("--min-counter", type=_int, default=0)
    vh = v.add_mutually_exclusive_group()
    vh.add_argument("--op-set-hash")
    vh.add_argument("--op-set-file")
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("inspect", help="print manifest fields")
    i.add_argument("image")
    i.add_argument("--json", action="store_true")
    i.set_defaults(fn=cmd_inspect)

    o = sub.add_parser("opset", help="canonical op-set hash of an op list file")
    o.add_argument("file")
    o.set_defaults(fn=cmd_opset)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
