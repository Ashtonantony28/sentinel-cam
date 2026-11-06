"""Reference implementation of INTERFACES.md §1: int8 face-embedding quantisation,
int8 cosine matching against an enrolled gallery, and the SGAL flash blob.

This is the executable spec the C firmware (firmware/core gallery matcher) is
tested against through the golden vectors in ``ml/golden/``.

Clarifications of points INTERFACES.md §1 leaves open (all are pinned by golden
vectors; see ml/README.md "Contract clarifications"):

1. ``round`` is round-half-AWAY-from-zero (C ``roundf``/``lroundf``), computed on
   the float32 product ``x * 127.0f``; NaN inputs are treated as 0.
2. Cosine of an all-zero int8 vector with anything is 0.0.
3. Scores are compared in float64 here; C may use float32. Golden decisions are
   kept >= 1e-3 away from every threshold/margin boundary, except the exact-tie
   case (margin exactly 0 → unknown), which is precision-independent.
4. With fewer than two enrolled people, "second best" is -1.0 (the minimum cosine),
   so the margin test reduces to ``best + 1 >= 0.05`` (always true).
5. Best-person ties are broken by gallery order (first wins); a tie always fails
   the margin test, so the decision does not depend on the tie-break.
6. threshold_q15 = clamp(round_half_away(t * 32768), -32768, 32767); the effective
   threshold is threshold_q15 / 32768 (default 0.55 → 18022 → 0.54998779...).
7. Blob: n_protos must be 1..20, n_people 0..16, ids unique, name is UTF-8,
   NUL-padded; the writer requires <= 23 bytes so it is always NUL-terminated.
   Validation order (first failure wins): TRUNCATED (len < 12) → BAD_CRC → BAD_MAGIC
   → BAD_VERSION → TOO_MANY_PEOPLE → per person TRUNCATED / BAD_PROTO_COUNT →
   TRAILING_BYTES → DUPLICATE_ID.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

EMB_DIM = 128
MAX_PEOPLE = 16
MAX_PROTOS = 20
DEFAULT_THRESHOLD = 0.55
MARGIN = 0.05
MAGIC = b"SGAL"
VERSION = 1
HEADER = struct.Struct("<4sHH")          # magic, version, n_people            (8 bytes)
PERSON = struct.Struct("<HBBh24s")       # id, n_protos, reserved, thr_q15, name (30 bytes)
NAME_LEN = 24


class GalleryError(ValueError):
    def __init__(self, code: str, msg: str = ""):
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code = code


# ------------------------------------------------------------------ quantisation

def round_half_away(x: np.ndarray) -> np.ndarray:
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def quantize_embedding(x) -> np.ndarray:
    """fp32 embedding (L2-normalised) → int8, ``round(clamp(x*127, -127, 127))``."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float32), nan=0.0)
    with np.errstate(over="ignore", invalid="ignore"):
        y = np.clip(x * np.float32(127.0), np.float32(-127.0), np.float32(127.0))
    return round_half_away(y).astype(np.int8)


def l2_normalize(x) -> np.ndarray:
    x = np.asarray(x, np.float32)
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return (x / np.maximum(n, 1e-12)).astype(np.float32)


def cosine_int8(a, b) -> float:
    a = np.asarray(a, np.int8).astype(np.int32)
    b = np.asarray(b, np.int8).astype(np.int32)
    dot = int(np.dot(a, b))
    na, nb = int(np.dot(a, a)), int(np.dot(b, b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (np.sqrt(na) * np.sqrt(nb))


def threshold_to_q15(t: float) -> int:
    return int(np.clip(round_half_away(np.float64(t) * 32768.0), -32768, 32767))


def q15_to_threshold(q: int) -> float:
    return q / 32768.0


# ------------------------------------------------------------------ gallery

@dataclass
class Person:
    id: int
    name: str
    protos: np.ndarray                       # int8 [n, 128]
    threshold_q15: int = field(default_factory=lambda: threshold_to_q15(DEFAULT_THRESHOLD))

    @property
    def threshold(self) -> float:
        return q15_to_threshold(self.threshold_q15)


@dataclass
class MatchResult:
    person_id: int | None            # None = unknown
    best_id: int | None              # best-scoring person even if not accepted
    best_score: float
    second_score: float
    scores: dict                     # person id → score


@dataclass
class Gallery:
    people: list = field(default_factory=list)

    def add(self, pid: int, name: str, embeddings_f32, threshold: float = DEFAULT_THRESHOLD) -> Person:
        e = np.atleast_2d(np.asarray(embeddings_f32, np.float32))
        p = Person(pid, name, quantize_embedding(e), threshold_to_q15(threshold))
        self.people.append(p)
        validate(self)
        return p

    def match(self, query) -> MatchResult:
        return match(self, query)


def validate(g: Gallery):
    if len(g.people) > MAX_PEOPLE:
        raise GalleryError("TOO_MANY_PEOPLE", f"{len(g.people)} > {MAX_PEOPLE}")
    ids = [p.id for p in g.people]
    if len(set(ids)) != len(ids):
        raise GalleryError("DUPLICATE_ID")
    for p in g.people:
        if not (1 <= len(p.protos) <= MAX_PROTOS):
            raise GalleryError("BAD_PROTO_COUNT", f"person {p.id}: {len(p.protos)}")
        if p.protos.shape[1:] != (EMB_DIM,) or p.protos.dtype != np.int8:
            raise GalleryError("BAD_PROTO_SHAPE", str(p.protos.shape))
        if not (0 <= p.id <= 0xFFFF):
            raise GalleryError("BAD_ID", str(p.id))
        if len(p.name.encode("utf-8")) > NAME_LEN - 1:
            raise GalleryError("NAME_TOO_LONG", p.name)


def match(g: Gallery, query) -> MatchResult:
    """Query may be int8 (device form) or float (quantised first)."""
    q = np.asarray(query)
    if q.dtype != np.int8:
        q = quantize_embedding(q)
    scores = {}
    for p in g.people:
        scores[p.id] = max(cosine_int8(q, proto) for proto in p.protos)
    if not g.people:
        return MatchResult(None, None, -1.0, -1.0, scores)
    order = sorted(range(len(g.people)), key=lambda i: -scores[g.people[i].id])  # stable: gallery order on ties
    best_p = g.people[order[0]]
    best = scores[best_p.id]
    second = scores[g.people[order[1]].id] if len(order) > 1 else -1.0
    ok = best >= best_p.threshold and (best - second) >= MARGIN
    return MatchResult(best_p.id if ok else None, best_p.id, best, second, scores)


# ------------------------------------------------------------------ SGAL blob

def write_blob(g: Gallery) -> bytes:
    validate(g)
    out = bytearray(HEADER.pack(MAGIC, VERSION, len(g.people)))
    for p in g.people:
        name = p.name.encode("utf-8").ljust(NAME_LEN, b"\0")
        out += PERSON.pack(p.id, len(p.protos), 0, p.threshold_q15, name)
        out += np.ascontiguousarray(p.protos, np.int8).tobytes()
    out += struct.pack("<I", zlib.crc32(bytes(out)) & 0xFFFFFFFF)
    return bytes(out)


def read_blob(blob: bytes) -> Gallery:
    if len(blob) < HEADER.size + 4:
        raise GalleryError("TRUNCATED", f"{len(blob)} bytes")
    body, (crc,) = blob[:-4], struct.unpack("<I", blob[-4:])
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        raise GalleryError("BAD_CRC")
    magic, ver, n = HEADER.unpack_from(body, 0)
    if magic != MAGIC:
        raise GalleryError("BAD_MAGIC", repr(magic))
    if ver != VERSION:
        raise GalleryError("BAD_VERSION", str(ver))
    if n > MAX_PEOPLE:
        raise GalleryError("TOO_MANY_PEOPLE", str(n))
    off, people = HEADER.size, []
    for _ in range(n):
        if off + PERSON.size > len(body):
            raise GalleryError("TRUNCATED", "person header")
        pid, k, _res, thr, name = PERSON.unpack_from(body, off)
        off += PERSON.size
        if not (1 <= k <= MAX_PROTOS):
            raise GalleryError("BAD_PROTO_COUNT", f"person {pid}: {k}")
        if off + k * EMB_DIM > len(body):
            raise GalleryError("TRUNCATED", "prototypes")
        protos = np.frombuffer(body, np.int8, k * EMB_DIM, off).reshape(k, EMB_DIM).copy()
        off += k * EMB_DIM
        people.append(Person(pid, name.split(b"\0", 1)[0].decode("utf-8", "replace"), protos, thr))
    if off != len(body):
        raise GalleryError("TRAILING_BYTES", f"{len(body) - off}")
    if len({p.id for p in people}) != len(people):
        raise GalleryError("DUPLICATE_ID")
    return Gallery(people)
