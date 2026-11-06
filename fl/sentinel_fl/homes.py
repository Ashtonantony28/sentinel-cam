"""Simulated non-IID "homes" in the 64-d false-alert feature space.

This is a synthetic stand-in for the projected person-detector neck features of
INTERFACES.md §5, built so that the federated-vs-local question is non-trivial:

* A shared "world" has a person prototype and four nuisance prototypes (car, tree,
  pet, headlights). Pets and headlights sit closer to the person class (hard negatives).
  Every home sees the same physics, so pooling data helps.
* Each home differs (non-IID):
  - its own nuisance mix, Dirichlet(alpha) over the four types (a street-facing door
    sees cars and headlights; a garden door sees trees and pets);
  - a camera/lighting offset applied to all of its features (covariate shift);
  - with probability `p_quirk`, a home-specific recurring nuisance (the neighbour's
    flag, a reflective sign) placed partly along the person direction, which only
    that home ever sees;
  - its own class balance (fraction of events that are real people);
  - its own amount of labelled data, log-normal, so some homes have only a handful
    of labels (possibly of one class only).
* Each home also gets a large held-out test set from its own distribution, used only
  for evaluation (false-alert rate on nuisance events, recall on person events).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .head import DIM

NUISANCE = ("car", "tree", "pet", "headlights")


@dataclass(frozen=True)
class WorldConfig:
    dim: int = DIM
    sep: float = 5.0            # distance person prototype <-> nuisance centroid, in noise units
    hard_neg: float = 0.45      # how far pets/headlights are pulled towards the person prototype
    noise: float = 1.0          # isotropic within-class noise
    lowrank: int = 4            # extra anisotropic within-class variation (pose, distance)
    lowrank_scale: float = 1.0
    shift_scale: float = 0.6    # per-home camera/lighting offset (std per coordinate)
    dirichlet_alpha: float = 0.4
    p_quirk: float = 0.5
    quirk_toward_person: float = 0.7
    person_rate: tuple[float, float] = (0.05, 0.5)
    n_train_lognorm: tuple[float, float] = (3.6, 1.0)   # median ~37 labels
    n_train_clip: tuple[int, int] = (4, 400)
    n_test: int = 400


@dataclass
class Home:
    idx: int
    X: np.ndarray
    y: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    test_kind: np.ndarray       # nuisance type per test example ("person" for positives)
    mix: np.ndarray
    person_rate: float
    has_quirk: bool

    @property
    def n(self) -> int:
        return len(self.y)


@dataclass
class World:
    cfg: WorldConfig
    mu_person: np.ndarray
    mu_nuis: np.ndarray          # (4, dim)
    basis: np.ndarray            # (lowrank, dim) shared within-class variation directions
    homes: list[Home] = field(default_factory=list)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def make_world(n_homes: int, seed: int, cfg: WorldConfig = WorldConfig()) -> World:
    rng = np.random.default_rng(seed)
    d = cfg.dim
    axis = _unit(rng.normal(size=d))
    mu_person = 0.5 * cfg.sep * axis
    mu_nuis = np.empty((len(NUISANCE), d))
    for k, name in enumerate(NUISANCE):
        off = _unit(rng.normal(size=d)) * 1.2
        c = -0.5 * cfg.sep * axis + off
        if name in ("pet", "headlights"):
            c = (1 - cfg.hard_neg) * c + cfg.hard_neg * mu_person + 0.4 * off
        mu_nuis[k] = c
    # Orthonormal directions of extra within-class variation, std `lowrank_scale` along each.
    basis = np.linalg.qr(rng.normal(size=(d, cfg.lowrank)))[0].T * cfg.lowrank_scale
    world = World(cfg, mu_person, mu_nuis, basis)
    for h in range(n_homes):
        world.homes.append(_make_home(world, h, rng))
    return world


def _sample(world: World, rng: np.random.Generator, n: int, center: np.ndarray, shift: np.ndarray) -> np.ndarray:
    cfg = world.cfg
    z = rng.normal(size=(n, cfg.lowrank))
    return center + shift + z @ world.basis + cfg.noise * rng.normal(size=(n, cfg.dim))


def _draw(world: World, rng: np.random.Generator, n: int, person_rate: float, mix: np.ndarray,
          shift: np.ndarray, quirk: np.ndarray | None, quirk_share: float, balanced: bool = False):
    if balanced:
        n_pos = n // 2
    else:
        n_pos = rng.binomial(n, person_rate)
    n_neg = n - n_pos
    Xp = _sample(world, rng, n_pos, world.mu_person, shift)
    kinds_neg: list[str] = []
    Xn_parts = []
    n_quirk = rng.binomial(n_neg, quirk_share) if quirk is not None else 0
    if n_quirk:
        Xn_parts.append(_sample(world, rng, n_quirk, quirk, shift))
        kinds_neg += ["quirk"] * n_quirk
    counts = rng.multinomial(n_neg - n_quirk, mix)
    for k, c in enumerate(counts):
        if c:
            Xn_parts.append(_sample(world, rng, c, world.mu_nuis[k], shift))
            kinds_neg += [NUISANCE[k]] * c
    Xn = np.concatenate(Xn_parts) if Xn_parts else np.empty((0, world.cfg.dim))
    X = np.concatenate([Xp, Xn])
    y = np.concatenate([np.ones(n_pos), np.zeros(n_neg)])
    kinds = np.array(["person"] * n_pos + kinds_neg)
    perm = rng.permutation(n)
    return X[perm], y[perm], kinds[perm]


def _make_home(world: World, idx: int, rng: np.random.Generator) -> Home:
    cfg = world.cfg
    mix = rng.dirichlet([cfg.dirichlet_alpha] * len(NUISANCE))
    shift = cfg.shift_scale * rng.normal(size=cfg.dim)
    person_rate = float(rng.uniform(*cfg.person_rate))
    has_quirk = bool(rng.random() < cfg.p_quirk)
    quirk = None
    quirk_share = 0.0
    if has_quirk:
        q = cfg.quirk_toward_person * world.mu_person + (1 - cfg.quirk_toward_person) * world.mu_nuis[rng.integers(4)]
        quirk = q + _unit(rng.normal(size=cfg.dim)) * 1.5
        quirk_share = float(rng.uniform(0.2, 0.5))
    n_train = int(np.clip(rng.lognormal(*cfg.n_train_lognorm), *cfg.n_train_clip))
    X, y, _ = _draw(world, rng, n_train, person_rate, mix, shift, quirk, quirk_share)
    Xt, yt, kt = _draw(world, rng, cfg.n_test, person_rate, mix, shift, quirk, quirk_share, balanced=True)
    return Home(idx, X, y, Xt, yt, kt, mix, person_rate, has_quirk)
