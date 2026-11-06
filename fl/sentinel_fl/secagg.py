"""Secure aggregation with pairwise additive masks (Bonawitz et al., CCS 2017, "Practical
Secure Aggregation for Privacy-Preserving Machine Learning"), semi-honest server.

Vectors are fixed-point encoded into the ring Z_{2^64} (numpy uint64 arithmetic wraps).
Each surviving client u sends

    y_u = enc(x_u) + PRG(b_u) + sum_{v>u} PRG(s_uv) - sum_{v<u} PRG(s_uv)      (mod 2^64)

where s_uv is an X25519 Diffie-Hellman agreement between u's and v's masking keys,
and b_u is a fresh self-mask seed. Pairwise masks cancel in the sum. To survive
dropouts, every client t-of-n Shamir-shares both its masking secret key s_sk_u and its
self-mask seed b_u to the others (encrypted end-to-end with AES-GCM under a second
X25519 key, so the server only relays ciphertext). In the unmasking round, each
survivor reveals, *per peer*, either the share of b_v (v survived) or the share of
s_sk_v (v dropped after sharing keys), never both. The server then:

* reconstructs b_u for survivors and removes their self-masks;
* reconstructs s_sk_v for dropped clients and removes the pairwise masks they left
  in the survivors' inputs.

What the server learns: the sum over survivors, plus the public keys, ciphertexts
and masked vectors, which are uniformly distributed given the sum (tests check
this). Out of scope here (noted in the README): an actively malicious server
(that needs the signature/consistency-check rounds of the paper) and client-server
key authentication (the device identity key of PLAN §6 would sign the
AdvertiseKeys message).

Rounds (names follow the paper):
  0 AdvertiseKeys   client -> server: (c_pk, s_pk)
  1 ShareKeys       client -> server: encrypted shares for each peer
  2 MaskedInput     client -> server: y_u
  3 Unmasking       client -> server: shares of b (survivors) / s_sk (dropped)
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import numpy as np
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FRAC_BITS = 24
SCALE = float(1 << FRAC_BITS)
PRIME = (1 << 521) - 1  # Mersenne prime field for Shamir sharing of 256-bit secrets


class SecAggError(RuntimeError):
    pass


# ---------------------------------------------------------------- fixed point
def encode(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    lim = 2.0 ** (63 - FRAC_BITS - 1)
    if np.any(np.abs(x) >= lim):
        raise SecAggError("value out of fixed-point range")
    return np.round(x * SCALE).astype(np.int64).view(np.uint64)


def decode(v: np.ndarray) -> np.ndarray:
    return v.view(np.int64).astype(np.float64) / SCALE


# ---------------------------------------------------------------- primitives
def _raw(pk: X25519PublicKey) -> bytes:
    return pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _sk_raw(sk: X25519PrivateKey) -> bytes:
    return sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def _kdf(shared: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(shared)


def prg(seed: bytes, n: int, round_id: int = 0) -> np.ndarray:
    """AES-256-CTR keystream as n uint64 values."""
    nonce = hashlib.sha256(b"sentinel-secagg-prg" + round_id.to_bytes(8, "little")).digest()[:16]
    enc = Cipher(algorithms.AES(seed), modes.CTR(nonce)).encryptor()
    return np.frombuffer(enc.update(b"\x00" * (8 * n)), dtype="<u8").copy()


def pair_seed(sk: X25519PrivateKey, peer_pk: bytes, u: int, v: int) -> bytes:
    lo, hi = min(u, v), max(u, v)
    return _kdf(sk.exchange(X25519PublicKey.from_public_bytes(peer_pk)),
                b"mask" + lo.to_bytes(4, "little") + hi.to_bytes(4, "little"))


# ---------------------------------------------------------------- Shamir over GF(PRIME)
def shamir_split(secret: int, ids: list[int], t: int) -> dict[int, int]:
    """Shares at x = id + 1 (x must be non-zero)."""
    if not (0 <= secret < PRIME):
        raise ValueError("secret out of field")
    coeffs = [secret] + [secrets.randbelow(PRIME) for _ in range(t - 1)]
    out = {}
    for i in ids:
        x = i + 1
        acc = 0
        for c in reversed(coeffs):
            acc = (acc * x + c) % PRIME
        out[i] = acc
    return out


@lru_cache(maxsize=256)
def _lagrange_at_zero(xs: tuple[int, ...]) -> tuple[int, ...]:
    out = []
    for j, xj in enumerate(xs):
        num, den = 1, 1
        for m, xm in enumerate(xs):
            if m != j:
                num = num * (-xm) % PRIME
                den = den * (xj - xm) % PRIME
        out.append(num * pow(den, PRIME - 2, PRIME) % PRIME)
    return tuple(out)


def shamir_combine(shares: dict[int, int], t: int) -> int:
    if len(shares) < t:
        raise SecAggError(f"need {t} shares, have {len(shares)}")
    pts = sorted(shares.items())[:t]
    coeffs = _lagrange_at_zero(tuple(i + 1 for i, _ in pts))
    return sum(c * y for c, (_, y) in zip(coeffs, pts)) % PRIME


# ---------------------------------------------------------------- client
@dataclass
class ShareMsg:
    src: int
    dst: int
    nonce: bytes
    ct: bytes


class Client:
    def __init__(self, cid: int, dim: int, t: int, round_id: int = 0) -> None:
        self.id, self.dim, self.t, self.round_id = cid, dim, t, round_id
        self.c_sk = X25519PrivateKey.generate()
        self.s_sk = X25519PrivateKey.generate()
        self.b = os.urandom(32)
        self.peers: dict[int, tuple[bytes, bytes]] = {}
        self.inbox: dict[int, tuple[int, int]] = {}   # src -> (share of s_sk_src, share of b_src)
        self.u2: Optional[set[int]] = None
        self.revealed: dict[int, str] = {}            # peer -> "b" | "s"

    # round 0
    def advertise(self) -> tuple[bytes, bytes]:
        return _raw(self.c_sk.public_key()), _raw(self.s_sk.public_key())

    def _aead(self, peer: int) -> AESGCM:
        key = _kdf(self.c_sk.exchange(X25519PublicKey.from_public_bytes(self.peers[peer][0])), b"share-enc")
        return AESGCM(key)

    # round 1
    def share_keys(self, pks: dict[int, tuple[bytes, bytes]]) -> list[ShareMsg]:
        if self.id not in pks or len(pks) < self.t:
            raise SecAggError("not enough clients advertised keys")
        if len({pk for pk in pks.values()}) != len(pks):
            raise SecAggError("duplicate public keys")
        self.peers = dict(pks)
        ids = sorted(pks)
        s_shares = shamir_split(int.from_bytes(_sk_raw(self.s_sk), "little"), ids, self.t)
        b_shares = shamir_split(int.from_bytes(self.b, "little"), ids, self.t)
        out = []
        for v in ids:
            pt = (self.id.to_bytes(4, "little") + v.to_bytes(4, "little")
                  + s_shares[v].to_bytes(66, "little") + b_shares[v].to_bytes(66, "little"))
            if v == self.id:
                self.inbox[v] = (s_shares[v], b_shares[v])
                continue
            nonce = os.urandom(12)
            out.append(ShareMsg(self.id, v, nonce, self._aead(v).encrypt(nonce, pt, b"secagg")))
        return out

    # round 2
    def masked_input(self, x: np.ndarray, shares_for_me: list[ShareMsg]) -> np.ndarray:
        for m in shares_for_me:
            if m.dst != self.id or m.src not in self.peers:
                raise SecAggError("misrouted share")
            pt = self._aead(m.src).decrypt(m.nonce, m.ct, b"secagg")
            if int.from_bytes(pt[0:4], "little") != m.src or int.from_bytes(pt[4:8], "little") != self.id:
                raise SecAggError("share header mismatch")
            self.inbox[m.src] = (int.from_bytes(pt[8:74], "little"), int.from_bytes(pt[74:140], "little"))
        self.u2 = set(self.inbox)  # clients that completed ShareKeys (incl. self)
        if len(self.u2) < self.t:
            raise SecAggError("too few peers completed ShareKeys")
        x = np.asarray(x, dtype=np.float64)
        if x.shape != (self.dim,):
            raise SecAggError("bad input shape")
        y = encode(x) + prg(self.b, self.dim, self.round_id)
        for v in self.u2:
            if v == self.id:
                continue
            m = prg(pair_seed(self.s_sk, self.peers[v][1], self.id, v), self.dim, self.round_id)
            y = y + m if v > self.id else y - m
        return y

    # round 3
    def unmask(self, survivors: set[int], dropped: set[int]) -> dict[int, tuple[str, int]]:
        if self.u2 is None or self.id not in survivors:
            raise SecAggError("not a survivor")
        if survivors & dropped:
            raise SecAggError("a client cannot be both survivor and dropped")
        if not (survivors | dropped) <= self.u2:
            raise SecAggError("unknown client in unmask request")
        if len(survivors) < self.t:
            raise SecAggError("fewer survivors than threshold")
        out = {}
        for v in survivors:
            if self.revealed.get(v, "b") != "b":
                raise SecAggError("refusing to reveal both secrets of one client")
            self.revealed[v] = "b"
            out[v] = ("b", self.inbox[v][1])
        for v in dropped:
            if self.revealed.get(v, "s") != "s":
                raise SecAggError("refusing to reveal both secrets of one client")
            self.revealed[v] = "s"
            out[v] = ("s", self.inbox[v][0])
        return out


# ---------------------------------------------------------------- server
@dataclass
class ServerView:
    """Everything the server sees, for the privacy tests."""
    pks: dict[int, tuple[bytes, bytes]] = field(default_factory=dict)
    ciphertexts: list[ShareMsg] = field(default_factory=list)
    masked: dict[int, np.ndarray] = field(default_factory=dict)
    b_seeds: dict[int, bytes] = field(default_factory=dict)
    s_keys: dict[int, bytes] = field(default_factory=dict)


def server_unmask(view: ServerView, dim: int, t: int, survivors: set[int], dropped: set[int],
                  responses: list[dict[int, tuple[str, int]]], round_id: int = 0) -> np.ndarray:
    if len(responses) < t:
        raise SecAggError("not enough unmasking responses")
    b_sh: dict[int, dict[int, int]] = {}
    s_sh: dict[int, dict[int, int]] = {}
    for resp_from, resp in zip(sorted(survivors), responses):
        for v, (kind, share) in resp.items():
            (b_sh if kind == "b" else s_sh).setdefault(v, {})[resp_from] = share
    total = np.zeros(dim, dtype=np.uint64)
    for u in survivors:
        total = total + view.masked[u]
    for u in survivors:
        b = shamir_combine(b_sh[u], t).to_bytes(32, "little")
        view.b_seeds[u] = b
        total = total - prg(b, dim, round_id)
    for v in dropped:
        sk_bytes = shamir_combine(s_sh[v], t).to_bytes(32, "little")
        view.s_keys[v] = sk_bytes
        sk = X25519PrivateKey.from_private_bytes(sk_bytes)
        for u in survivors:
            m = prg(pair_seed(sk, view.pks[u][1], u, v), dim, round_id)
            # y_u contains +m if v > u else -m
            total = total - m if v > u else total + m
    return decode(total)


def run_protocol(inputs: dict[int, np.ndarray], t: int, drop_after_share: frozenset[int] = frozenset(),
                 drop_before_share: frozenset[int] = frozenset(), round_id: int = 0,
                 view: Optional[ServerView] = None) -> tuple[np.ndarray, ServerView, set[int]]:
    """Run all four rounds in process. Returns (sum over survivors, server view, survivors)."""
    if t < 2:
        raise SecAggError("threshold must be >= 2 (t = 1 reveals a lone survivor's input)")
    dim = len(next(iter(inputs.values())))
    view = view or ServerView()
    clients = {i: Client(i, dim, t, round_id) for i in inputs}
    view.pks = {i: c.advertise() for i, c in clients.items()}
    u1 = {i for i in clients if i not in drop_before_share}
    pks = {i: view.pks[i] for i in u1}
    if len(u1) < t:
        raise SecAggError("too few clients")
    msgs: list[ShareMsg] = []
    for i in sorted(u1):
        msgs += clients[i].share_keys(pks)
    view.ciphertexts = msgs
    u2 = set(u1)
    survivors = u2 - set(drop_after_share)
    if len(survivors) < t:
        raise SecAggError("fewer survivors than threshold")
    for i in sorted(survivors):
        view.masked[i] = clients[i].masked_input(inputs[i], [m for m in msgs if m.dst == i])
    dropped = u2 - survivors
    responses = [clients[i].unmask(survivors, dropped) for i in sorted(survivors)]
    return server_unmask(view, dim, t, survivors, dropped, responses, round_id), view, survivors
