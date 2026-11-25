"""Render the README demo media into docs/media/.

    python3 tools/media.py

  day-reel.{gif,mp4,png}  the firmware's scripted 24 h day (firmware/core/tests/test_scenario_day.c): the real
                          brainstem + pipeline C code on the simulated HAL. The script builds and runs the test,
                          parses the EVENT/STATUS JSON it prints, and animates every wake, decision and fault
                          with the energy the firmware itself accounts for

It is a SIMULATION (no hardware exists yet; phase 2 needs dev kits). Needs cmake, a C compiler, matplotlib
and ffmpeg.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "media"

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BG, PANEL, GRID, TXT, DIM = "#0d1117", "#161b22", "#30363d", "#e6edf3", "#8b949e"
GREEN, ORANGE, PURPLE, RED, BLUE, YEL = "#3fb950", "#ffa657", "#d2a8ff", "#f85149", "#58a6ff", "#e3b341"
MONO = "DejaVu Sans Mono"
DAY_MS = 86_400_000
ACTIONS = {"NONE": ("nothing there", DIM), "LOG_KNOWN": ("known person, logged", GREEN),
           "NOTIFY_UNKNOWN_WITH_CLIP": ("stranger: notify + clip", ORANGE), "RING": ("doorbell ring + clip", PURPLE)}
FAULT_NOTE = {"LOW_BATTERY": "person refused: the cortex is never powered on a sagging cell",
              "BROWNOUT": "cold cell sagged during boot: cortex lost, 10 min lockout"}
NAMES = {1: "Alex", 2: "Sam", 3: "Jordan"}          # display names for the scenario's enrolled ids 1-3


def run_day():
    build = ROOT / "firmware" / "build" / "media"
    subprocess.run(["cmake", "-S", str(ROOT / "firmware"), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["cmake", "--build", str(build), "--target", "test_scenario_day", "-j4"], check=True,
                   stdout=subprocess.DEVNULL)
    out = subprocess.run([str(build / "core" / "test_scenario_day")], check=True, capture_output=True, text=True).stdout
    assert re.search(r"\b0 failed\b", out), out
    events = [json.loads(ln.split("EVENT ", 1)[1]) for ln in out.splitlines() if "EVENT {" in ln]
    status = [json.loads(ln.split("STATUS ", 1)[1]) for ln in out.splitlines() if "STATUS {" in ln]
    standby_mj = int(re.search(r"standby (\d+) mJ", out).group(1))
    avg_mw = float(re.search(r"average ([\d.]+) mW", out).group(1))
    days = int(re.search(r"-> (\d+) days", out).group(1))
    storm = re.search(r"storm unlimited: (\d+) wakes (\d+) mJ vs limited (\d+) wakes (\d+) mJ", out).groups()
    t0 = min(e["ts"] for e in events) // DAY_MS * DAY_MS
    for e in events + status:
        e["t"] = (e["ts"] - t0) / 3_600_000           # hours since midnight
    events.sort(key=lambda e: e["t"])
    return events, status, standby_mj, avg_mw, days, [int(v) for v in storm]


def hhmm(t):
    m = int(t * 60 + 1e-6)
    return f"{m // 60:02d}:{m % 60:02d}"


def draw(fig, ev, st, standby_mj, now, avg_mw, days, storm, final):
    fig.clf()
    fig.text(0.035, 0.935, "Sentinel: one day on the battery", fontsize=18, weight="bold", color=TXT)
    fig.text(0.035, 0.895, "FIRMWARE SIMULATION: the real brainstem + pipeline C code on the simulated HAL, replaying "
             "the scripted 24 h day (test_scenario_day). No hardware yet.", fontsize=9.5, color=DIM)
    seen = [e for e in ev if e["t"] <= now]
    # timeline
    ax = fig.add_axes([0.05, 0.62, 0.60, 0.20])
    ax.set_facecolor(PANEL)
    ax.axvspan(14, 14 + 20 / 60, color=YEL, alpha=0.12)
    ax.text(14.17, 3.35, "PIR storm", color=YEL, fontsize=7.5, ha="center")
    lanes = {"NONE": 0, "LOG_KNOWN": 1, "NOTIFY_UNKNOWN_WITH_CLIP": 2, "RING": 3}
    for e in seen:
        col = ACTIONS[e["action"]][1]
        ax.scatter(e["t"], lanes[e["action"]], s=34 if e["action"] != "NONE" else 14, color=col, zorder=3,
                   marker="D" if e["wake"] == "TIMER" else "o")
    for s in st:
        if s["t"] <= now:
            ax.axvline(s["t"], color=RED, lw=1.2)
            ax.text(s["t"] + 0.08, -0.55, s["reason"].replace("_", " ").lower(), color=RED, fontsize=7.5)
    ax.axvline(now, color=TXT, lw=0.8, alpha=0.6)
    ax.set_xlim(0, 24); ax.set_ylim(-0.8, 3.7)
    ax.set_yticks(range(4)); ax.set_yticklabels(["nothing", "known", "stranger", "ring"], color=DIM, fontsize=8.5)
    ax.set_xticks(range(0, 25, 3)); ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)], color=DIM, fontsize=8)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.set_title("wakes and on-device decisions", color=TXT, fontsize=11, loc="left")
    # energy
    ax = fig.add_axes([0.05, 0.12, 0.60, 0.40])
    ax.set_facecolor(BG)
    rate = standby_mj / 24.0 / 1000                    # J per hour
    ts = [0.0]
    sb = [0.0]
    tot = [0.0]
    acc = 0.0
    for e in seen:
        ts += [e["t"], e["t"]]
        sb += [rate * e["t"]] * 2
        tot += [rate * e["t"] + acc, rate * e["t"] + acc + e["energy_mj"] / 1000]
        acc += e["energy_mj"] / 1000
    ts.append(now); sb.append(rate * now); tot.append(rate * now + acc)
    ax.fill_between(ts, 0, sb, color=BLUE, alpha=0.35, step=None, label="standby (always-on ESP32-C6, Wi-Fi TWT)")
    ax.fill_between(ts, sb, tot, color=ORANGE, alpha=0.55, label="events (STM32N6 boot, camera, NPU, clip)")
    ax.plot(ts, tot, color=TXT, lw=1.2)
    ymax = (standby_mj + sum(e["energy_mj"] for e in ev)) / 1000 * 1.05
    ax.set_xlim(0, 24); ax.set_ylim(0, ymax)
    ax.set_xticks(range(0, 25, 3)); ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)], color=DIM, fontsize=8)
    ax.tick_params(colors=DIM, labelsize=8)
    ax.set_ylabel("battery energy used (J)", color=DIM, fontsize=9)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(GRID)
    ax.legend(loc="upper left", fontsize=8.5, facecolor=PANEL, edgecolor=GRID, labelcolor=TXT)
    ax.set_title("cumulative energy, as accounted by the firmware", color=TXT, fontsize=11, loc="left")
    # card
    x = 0.70
    fig.text(x, 0.80, hhmm(now), color=TXT, fontsize=34, family=MONO, weight="bold")
    last = seen[-1] if seen else None
    if last and now - last["t"] < 0.75:
        name, col = ACTIONS[last["action"]]
        fig.text(x, 0.735, f"{hhmm(last['t'])}  {last['wake'].lower()} wake", color=DIM, fontsize=11)
        fig.text(x, 0.69, name, color=col, fontsize=15, weight="bold")
        fig.text(x, 0.65, f"person confidence {last['person_conf']:.2f}", color=TXT, fontsize=10.5, family=MONO)
        for k, f in enumerate(last["faces"][:2]):
            who = NAMES.get(f["id"], f"id {f['id']}") if f["id"] is not None else "unknown face"
            fig.text(x, 0.615 - k * 0.033, f"face: {who:<12} cos {f['score']:.2f}", color=TXT, fontsize=10.5, family=MONO)
        fig.text(x, 0.54, f"event energy {last['energy_mj'] / 1000:.2f} J" + ("  (clip upload)" if last["clip_id"] else ""),
                 color=ORANGE, fontsize=10.5, family=MONO)
    else:
        fig.text(x, 0.69, "asleep", color=BLUE, fontsize=15, weight="bold")
        fig.text(x, 0.65, "brainstem only: PIR, button, TWT", color=DIM, fontsize=10.5)
    for s in st:
        if 0 <= now - s["t"] < 0.75:
            fig.text(x, 0.505, f"fault: {s['reason']} at {s['battery_mv']} mV", color=RED, fontsize=10.5, family=MONO)
            fig.text(x, 0.475, FAULT_NOTE.get(s["reason"], ""), color=DIM, fontsize=9)
    cnt = {a: sum(1 for e in seen if e["action"] == a) for a in ACTIONS}
    y = 0.42
    for a, (name, col) in ACTIONS.items():
        fig.text(x, y, f"{cnt[a]:>3}  {name}", color=col, fontsize=10.5, family=MONO)
        y -= 0.035
    if final:
        fig.text(x, 0.22, f"day average {avg_mw:.2f} mW", color=TXT, fontsize=13, weight="bold")
        fig.text(x, 0.185, f"≈ {days} days on 18.5 Wh (firmware model)", color=TXT, fontsize=10.5)
        fig.text(x, 0.145, f"storm limiter: {storm[2]} wakes ({storm[3] / 1000:.0f} J)", color=YEL, fontsize=10)
        fig.text(x, 0.115, f"instead of {storm[0]} ({storm[1] / 1000:.0f} J)", color=YEL, fontsize=10)
        fig.text(x, 0.06, "Battery-life number of record: hardware/README.md", color=DIM, fontsize=8.5)


def main(fps=15):
    OUT.mkdir(parents=True, exist_ok=True)
    ev, st, standby_mj, avg_mw, days, storm = run_day()
    # clock: 24 h in ~14 s, slowing down around each event
    times, t = [], 0.0
    while t < 24:
        times.append(t)
        near = min([abs(t - e["t"]) for e in ev] + [abs(t - s["t"]) for s in st])
        t += 0.02 if near < 0.25 else 0.12
    tmp = Path(tempfile.mkdtemp())
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)
    n = 0
    for t in times:
        draw(fig, ev, st, standby_mj, t, avg_mw, days, storm, False)
        fig.savefig(tmp / f"f{n:04d}.png", facecolor=BG); n += 1
    for _ in range(fps * 4):
        draw(fig, ev, st, standby_mj, 24.0 - 1 / 60, avg_mw, days, storm, True)
        fig.savefig(tmp / f"f{n:04d}.png", facecolor=BG); n += 1
    fig.savefig(OUT / "day-reel.png", facecolor=BG)
    plt.close(fig)
    ff = ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(tmp / "f%04d.png")]
    subprocess.run(ff + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(OUT / "day-reel.mp4")], check=True)
    subprocess.run(ff + ["-vf", "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];"
                         "[b][p]paletteuse=dither=none", str(OUT / "day-reel.gif")], check=True)
    shutil.rmtree(tmp)
    print(f"{len(times) + fps * 4} frames, {len(ev)} events, {len(st)} faults")


if __name__ == "__main__":
    sys.exit(main())
