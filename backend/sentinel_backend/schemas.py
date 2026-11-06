"""Wire schemas for docs/INTERFACES.md §3 (MQTT device <-> backend).

The models are strict: unknown keys are rejected, integers must be JSON integers
(not floats or booleans), and enums are closed. Anything the contract leaves open
(e.g. the `fw` string format) is left open here too, with two documented
tightenings for safety (see README "Deviations"):

* `dev` must be exactly 12 hex characters (the contract says "12 hex chars from MAC").
* `clip_id`, because it becomes an object-store key and a URL path segment, must match
  CLIP_ID_RE (1-128 chars of [A-Za-z0-9._-], not "." or "..").
"""
from __future__ import annotations

import re
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

DEV_RE = re.compile(r"^[0-9a-fA-F]{12}$")
CLIP_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

Wake = Literal["PIR", "RADAR", "BUTTON", "TIMER"]
Action = Literal["NONE", "LOG_KNOWN", "NOTIFY_UNKNOWN_WITH_CLIP", "RING"]
FeedbackLabel = Literal["false_alarm", "person", "known"]
OtaKind = Literal["fw_n6", "fw_c6", "model"]
OTA_KINDS: tuple[str, ...] = ("fw_n6", "fw_c6", "model")


def ota_topic(dev: str, kind: str) -> str:
    """INTERFACES §3: `sentinel/{dev}/ota/{kind}`, retained, one notice per kind."""
    if kind not in OTA_KINDS:
        raise ValueError(f"bad OTA kind {kind!r}")
    return f"sentinel/{dev}/ota/{kind}"



def _json_number(v):
    # JSON numbers like 0 or 1 are valid floats (StrictFloat would reject the int 1),
    # but strings and booleans are not numbers.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("must be a JSON number")
    return float(v)


Number = Annotated[float, BeforeValidator(_json_number)]


def valid_dev(dev: str) -> bool:
    return bool(DEV_RE.match(dev))


def valid_clip_id(clip_id: str) -> bool:
    return bool(CLIP_ID_RE.match(clip_id)) and clip_id not in (".", "..")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Face(_Strict):
    id: Optional[StrictInt]
    score: Number


class EventMsg(_Strict):
    """`sentinel/{dev}/event` payload."""

    v: Literal[1]
    dev: StrictStr
    ts: StrictInt = Field(ge=0)
    wake: Wake
    action: Action
    person_conf: Number = Field(ge=0.0, le=1.0)
    faces: list[Face]
    clip_id: Optional[StrictStr]
    battery_mv: StrictInt = Field(ge=0)
    soc_pct: StrictInt = Field(ge=0, le=100)
    fw: StrictStr
    model: StrictStr
    energy_mj: StrictInt = Field(ge=0)

    @field_validator("v", mode="before")
    @classmethod
    def _v_is_int(cls, v):
        # Literal[1] would accept True (bool == 1); the contract says integer 1.
        if isinstance(v, bool):
            raise ValueError("v must be the integer 1")
        return v

    @field_validator("dev")
    @classmethod
    def _dev(cls, v: str) -> str:
        if not valid_dev(v):
            raise ValueError("dev must be 12 hex characters")
        return v

    @field_validator("clip_id")
    @classmethod
    def _clip(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not valid_clip_id(v):
            raise ValueError("clip_id must match [A-Za-z0-9._-]{1,128}")
        return v


class FeedbackMsg(_Strict):
    """`sentinel/{dev}/feedback` payload (backend -> device)."""

    v: Literal[1] = 1
    event_ts: StrictInt = Field(ge=0)
    label: FeedbackLabel
    person_id: Optional[StrictInt]


class OtaNotice(_Strict):
    """`sentinel/{dev}/ota/{kind}` payload (backend -> device, retained; `kind` matches the topic)."""

    v: Literal[1] = 1
    kind: OtaKind
    version: StrictStr
    url: StrictStr
    size: StrictInt = Field(gt=0)
    sha256: StrictStr

    @field_validator("version")
    @classmethod
    def _ver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("version must be major.minor.patch")
        major, minor, patch = (int(x) for x in v.split("."))
        if major > 0xFFFF or minor > 0xFF or patch > 0xFF:
            raise ValueError("version does not fit the packed u32 semver of INTERFACES.md")
        return v

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256_RE.match(v):
            raise ValueError("sha256 must be 64 hex characters")
        return v.lower()

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("url must be http(s)")
        return v


class ClipChunkHeader(_Strict):
    """Header JSON of the MQTT clip-chunk fallback path (`sentinel/{dev}/clip/{clip_id}`)."""

    seq: StrictInt = Field(ge=0)
    last: StrictBool


# ---------------------------------------------------------------- REST bodies
class FeedbackIn(_Strict):
    event_ts: StrictInt = Field(ge=0)
    label: FeedbackLabel
    person_id: Optional[StrictInt] = None


class OtaIn(_Strict):
    kind: OtaKind
    version: StrictStr
    url: StrictStr
    size: StrictInt
    sha256: StrictStr


def pack_semver(version: str) -> int:
    """INTERFACES.md: u32 = major<<16 | minor<<8 | patch."""
    major, minor, patch = (int(x) for x in version.split("."))
    return (major << 16) | (minor << 8) | patch
