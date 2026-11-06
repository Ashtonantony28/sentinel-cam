"""Device simulator: publishes realistic Sentinel event streams.

Each simulated camera has a diurnal activity profile, a household of known people, a
mix of nuisance wakes (cars, trees, headlights, pets) that end at stage 1 with
action NONE, unknown visitors that produce a clip, button rings, and timer heartbeats.
Battery state of charge falls with each event's energy plus standby drain, using the
plan §4.4 numbers (18.5 Wh pack, ~1.5 mW standby).

CLI (real broker + HTTP API):
    python -m sentinel_backend.simulator --devices 3 --events 50 \
        --mqtt-host localhost --mqtt-port 1883 --api http://localhost:8000 --device-secret s3cret
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .auth import device_tag
from .bus import Bus

PACK_MJ = 18.5 * 3600 * 1000  # 18.5 Wh in millijoules
STANDBY_MW = 1.5

Uploader = Callable[[str, str, bytes], int]  # (dev, clip_id, data) -> HTTP status


def soc_to_mv(soc: float) -> int:
    """Rough 1S Li-ion open-circuit voltage curve."""
    soc = min(max(soc, 0.0), 1.0)
    return int(3300 + 900 * (0.15 * soc + 0.85 * (1 - math.exp(-4 * soc)) / (1 - math.exp(-4))))


@dataclass
class DeviceSim:
    dev: str
    rng: random.Random
    t_ms: int
    known_people: list[int] = field(default_factory=lambda: [1, 2, 3])
    fw: str = "1.2.0"
    model: str = "pd-0.3.1"
    energy_used_mj: float = 0.0
    soc0: float = 0.95
    seq: int = 0
    ota_notices: dict = field(default_factory=dict)   # kind -> latest notice (retained, per kind)
    feedback: list = field(default_factory=list)

    def subscribe(self, bus: Bus) -> None:
        """Downlink: `sentinel/{dev}/ota/+` (retained, one per kind) and `sentinel/{dev}/feedback`."""
        def on_ota(topic: str, payload: bytes) -> None:
            kind = topic.rsplit("/", 1)[-1]
            if payload:
                self.ota_notices[kind] = json.loads(payload)
            else:
                self.ota_notices.pop(kind, None)  # retained notice cleared

        bus.subscribe(f"sentinel/{self.dev}/ota/+", on_ota, qos=1)
        bus.subscribe(f"sentinel/{self.dev}/feedback", lambda t, p: self.feedback.append(json.loads(p)), qos=1)

    def _hour(self) -> float:
        return (self.t_ms / 3_600_000.0) % 24

    def _rate_per_hour(self) -> float:
        h = self._hour()
        # Busier in the morning and evening, quiet at night.
        return 0.2 + 1.2 * math.exp(-((h - 8) ** 2) / 4) + 1.6 * math.exp(-((h - 18) ** 2) / 6)

    def soc(self) -> float:
        return max(0.0, self.soc0 - self.energy_used_mj / PACK_MJ)

    def step(self) -> tuple[dict, Optional[bytes]]:
        """Advance time to the next event and return (event JSON dict, clip bytes or None)."""
        dt_h = self.rng.expovariate(self._rate_per_hour())
        dt_ms = max(1, int(dt_h * 3_600_000))
        self.t_ms += dt_ms
        self.energy_used_mj += STANDBY_MW * dt_ms / 1000.0  # mW * s = mJ
        night = self._hour() < 6 or self._hour() > 20
        r = self.rng.random()
        faces: list[dict] = []
        clip: Optional[bytes] = None
        if r < 0.05:
            wake, action, conf, energy = "TIMER", "NONE", 0.0, 300
        elif r < 0.50:
            # Nuisance: car, tree, headlights (night), pet. Stage-1 reject.
            wake = "RADAR" if self.rng.random() < 0.2 else "PIR"
            action = "NONE"
            conf = self.rng.betavariate(1.2, 8.0) * (0.9 if night else 0.7)
            energy = int(self.rng.gauss(1000, 150))
        elif r < 0.78:
            wake, action = "PIR", "LOG_KNOWN"
            conf = 0.6 + 0.4 * self.rng.betavariate(5, 1.5)
            faces = [{"id": self.rng.choice(self.known_people), "score": round(self.rng.uniform(0.6, 0.92), 3)}]
            energy = int(self.rng.gauss(3000, 300))
        elif r < 0.95:
            wake, action = "PIR", "NOTIFY_UNKNOWN_WITH_CLIP"
            conf = 0.55 + 0.45 * self.rng.betavariate(4, 1.5)
            if self.rng.random() < 0.7:  # face visible but not matched
                faces = [{"id": None, "score": round(self.rng.uniform(0.15, 0.5), 3)}]
            energy = int(self.rng.gauss(15000, 2000))
            clip = self._clip()
        else:
            wake, action = "BUTTON", "RING"
            conf = 0.5 + 0.5 * self.rng.random()
            if conf >= 0.5 and self.rng.random() < 0.6:
                faces = [{"id": None, "score": round(self.rng.uniform(0.1, 0.5), 3)}]
            energy = int(self.rng.gauss(14000, 2000))
            clip = self._clip()
        energy = max(energy, 50)
        self.energy_used_mj += energy
        clip_id = None
        if clip is not None:
            self.seq += 1
            clip_id = f"{self.dev}-{self.t_ms}-{self.seq}"
        soc = self.soc()
        ev = {
            "v": 1, "dev": self.dev, "ts": self.t_ms, "wake": wake, "action": action,
            "person_conf": round(min(max(conf, 0.0), 1.0), 4), "faces": faces, "clip_id": clip_id,
            "battery_mv": soc_to_mv(soc), "soc_pct": int(round(soc * 100)),
            "fw": self.fw, "model": self.model, "energy_mj": energy,
        }
        return ev, clip

    def status(self, state: str = "online", reason: Optional[str] = None) -> bytes:
        """INTERFACES §3 status JSON."""
        return json.dumps({"state": state, "reason": reason, "ts": self.t_ms,
                           "battery_mv": soc_to_mv(self.soc())}, separators=(",", ":")).encode()

    def maybe_fault(self) -> Optional[str]:
        """Brainstem-side fault after an event, when it refuses or loses the N6: low battery
        below 10% SoC, otherwise rare brownouts / power faults / job timeouts."""
        if self.soc() < 0.10:
            return "LOW_BATTERY"
        r = self.rng.random()
        if r < 0.004:
            return "BROWNOUT"
        if r < 0.007:
            return "POWER_FAULT"
        if r < 0.015:
            return "JOB_TIMEOUT"
        return None

    def _clip(self) -> bytes:
        # Stand-in for an H.264 MP4: an ftyp box + random payload of realistic-ish size.
        n = self.rng.randint(2_000, 12_000)
        return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + self.rng.randbytes(n)


def make_devices(n: int, seed: int = 0, start_ms: int = 1_793_000_000_000) -> list[DeviceSim]:
    rng = random.Random(seed)
    devs = []
    for i in range(n):
        mac = "a4cf12" + f"{rng.getrandbits(24):06x}"
        devs.append(DeviceSim(dev=mac, rng=random.Random(seed * 1000 + i), t_ms=start_ms,
                              known_people=rng.sample(range(1, 17), rng.randint(1, 4))))
    return devs


def stream(devices: list[DeviceSim], events_per_device: int) -> Iterator[tuple[DeviceSim, dict, Optional[bytes]]]:
    """Interleave device streams in timestamp order."""
    pending = {d.dev: d.step() for d in devices}
    remaining = {d.dev: events_per_device for d in devices}
    by_dev = {d.dev: d for d in devices}
    while pending:
        dev = min(pending, key=lambda k: pending[k][0]["ts"])
        ev, clip = pending.pop(dev)
        yield by_dev[dev], ev, clip
        remaining[dev] -= 1
        if remaining[dev] > 0:
            pending[dev] = by_dev[dev].step()


def run(
    bus: Bus,
    uploader: Optional[Uploader],
    n_devices: int = 3,
    events_per_device: int = 20,
    seed: int = 0,
    dup_rate: float = 0.0,
    mqtt_clips: bool = False,
    chunk_size: int = 4096,
    faults: bool = True,
) -> dict:
    """Publish a full simulated session. Clips go first (HTTP PUT preferred), then the event."""
    devices = make_devices(n_devices, seed)
    for d in devices:
        d.subscribe(bus)
    rng = random.Random(seed + 7)
    stats = {"events": 0, "clips": 0, "dups": 0, "upload_failures": 0, "faults": 0}
    for d in devices:
        bus.publish(f"sentinel/{d.dev}/status", d.status("online"), qos=1, retain=True)
    for d, ev, clip in stream(devices, events_per_device):
        if clip is not None:
            if mqtt_clips or uploader is None:
                chunks = [clip[i:i + chunk_size] for i in range(0, len(clip), chunk_size)]
                for i, c in enumerate(chunks):
                    hdr = json.dumps({"seq": i, "last": i == len(chunks) - 1}).encode()
                    bus.publish(f"sentinel/{d.dev}/clip/{ev['clip_id']}", hdr + b"\n" + c, qos=1)
                stats["clips"] += 1
            else:
                code = uploader(d.dev, ev["clip_id"], clip)
                if code in (200, 201):
                    stats["clips"] += 1
                else:
                    stats["upload_failures"] += 1
        payload = json.dumps(ev, separators=(",", ":")).encode()
        bus.publish(f"sentinel/{d.dev}/event", payload, qos=1)
        stats["events"] += 1
        if dup_rate and rng.random() < dup_rate:  # QoS 1 redelivery
            bus.publish(f"sentinel/{d.dev}/event", payload, qos=1)
            stats["dups"] += 1
        reason = d.maybe_fault() if faults else None
        if reason:
            # Fault, then recovery to online on the next tick (both retained).
            bus.publish(f"sentinel/{d.dev}/status", d.status("fault", reason), qos=1, retain=True)
            stats["faults"] += 1
            if reason != "LOW_BATTERY":
                d.t_ms += 1
                bus.publish(f"sentinel/{d.dev}/status", d.status("online"), qos=1, retain=True)
    stats["devices"] = [d.dev for d in devices]
    stats["ota_notices"] = {d.dev: dict(d.ota_notices) for d in devices}
    stats["feedback"] = {d.dev: list(d.feedback) for d in devices}
    return stats


def http_uploader(api: str, secret: bytes) -> Uploader:
    import httpx

    client = httpx.Client(base_url=api, timeout=30)

    def up(dev: str, clip_id: str, data: bytes) -> int:
        r = client.put(f"/api/clips/{clip_id}", content=data,
                       headers={"Authorization": f"Device {dev}:{device_tag(secret, dev)}",
                                "Content-Type": "video/mp4"})
        return r.status_code

    return up


def main(argv: Optional[list[str]] = None) -> None:
    from .bus import PahoBus

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--devices", type=int, default=3)
    p.add_argument("--events", type=int, default=20, help="events per device")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--dup-rate", type=float, default=0.02)
    p.add_argument("--mqtt-host", default="localhost")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--api", default=None, help="API base URL for HTTP clip upload; omit to send clips over MQTT")
    p.add_argument("--device-secret", default=None)
    a = p.parse_args(argv)
    bus = PahoBus(a.mqtt_host, a.mqtt_port, client_id=f"sentinel-sim-{a.seed}")
    up = http_uploader(a.api, a.device_secret.encode()) if a.api and a.device_secret else None
    print(json.dumps(run(bus, up, a.devices, a.events, a.seed, a.dup_rate, mqtt_clips=up is None)))
    bus.close()


if __name__ == "__main__":
    main()
