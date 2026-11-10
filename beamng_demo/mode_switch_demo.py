"""
mode_switch_demo.py

Drive ego on three velocity-based tracks (aggressive / defensive / neutral).
Same path, different v-profiles. Uses set_script (x, y, z, v) matching
the approach in test_switch/maintrack_npc_demo.py.

Start: neutral track.
Input (keyboard, background thread):
  1  ->  switch to aggressive
  2  ->  switch to defensive

Transition at raw-track index n:
  anchor    -- actual vehicle position   (neutral speed)
  neutral[n+1]                           (neutral speed)
  neutral[n+2]                           (neutral speed)
  target[n+3] ...                        (target speed)

Run:
    python mode_switch_demo.py
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

from beamngpy import BeamNGpy, Scenario, Vehicle, set_up_simple_logging

# ---------------------------------------------------------------------------
# BeamNG
# ---------------------------------------------------------------------------

BEAMNG_HOME = "D:/Atharva/HCILab/Software/BeamNG"
BEAMNG_BINARY = f"{BEAMNG_HOME}/BeamNG.tech.exe"

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

LEVEL_NAME = "west_coast_usa"
SCENARIO_NAME = "mode_switch_demo"

ROOT = Path(__file__).resolve().parent / "tracks"
AGGRESSIVE_TRACK_PATH = ROOT / "aggressivetrack.json"
DEFENSIVE_TRACK_PATH = ROOT / "defensivetrack.json"
NEUTRAL_TRACK_PATH = ROOT / "neutraltrack.json"

EGO_ID    = "ego"
EGO_MODEL = "etk800"

DEFAULT_SPEED_MPS = 10.0   # fallback if a node has no v field

# ---------------------------------------------------------------------------
# Debug colours per mode
# ---------------------------------------------------------------------------

TRACK_RGBA = {
    "neutral":    (0.15, 0.95, 0.35, 0.9),
    "aggressive": (0.95, 0.35, 0.15, 0.9),
    "defensive":  (0.15, 0.45, 0.95, 0.9),
}

# ---------------------------------------------------------------------------
# Transition / visualisation settings
# ---------------------------------------------------------------------------

BRIDGE_NODES       = 2     # neutral nodes inserted between old and new mode
MIN_TIMING_SPEED   = 0.5   # m/s floor when computing timed-script timestamps
NODE_SPHERE_STRIDE = 10    # draw one sphere every N raw nodes
NODE_SPHERE_RADIUS = 0.12  # metres

# ---------------------------------------------------------------------------
# Position polling
# ---------------------------------------------------------------------------

POLL_INTERVAL_S       = 0.10
POSITION_SEARCH_WINDOW = 200  # raw-node forward window for nearest-index search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def heading_between(p0: dict, p1: dict) -> float:
    return math.atan2(p1["y"] - p0["y"], p1["x"] - p0["x"])


def heading_at_index(points: list[dict], i: int) -> float:
    i = max(0, min(i, len(points) - 2))
    return heading_between(points[i], points[i + 1])

# ---------------------------------------------------------------------------
# Track loading  (mirrors maintrack_npc_demo.py)
# ---------------------------------------------------------------------------

def load_track(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    points = data["path"]
    if len(points) < 3:
        raise RuntimeError(f"{path.name} must contain at least 3 path points.")
    return points


def build_velocity_script(points: list[dict]) -> list[dict]:
    return [
        {
            "x": float(p["x"]),
            "y": float(p["y"]),
            "z": float(p["z"]),
            "v": float(p.get("v", DEFAULT_SPEED_MPS)),
        }
        for p in points
    ]


def spawn_pose(
    points: list[dict],
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    first = points[0]
    pos  = (float(first["x"]), float(first["y"]), float(first["z"]) + 0.5)
    quat = yaw_to_quat(heading_at_index(points, 0))
    return pos, quat


def track_polyline(points: list[dict]) -> list[tuple[float, float, float]]:
    return [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]

# ---------------------------------------------------------------------------
# AI path application  (mirrors maintrack_npc_demo.py)
# ---------------------------------------------------------------------------

_fallback_warned = False


def apply_ai_path(vehicle: Vehicle, points: list[dict]) -> None:
    global _fallback_warned
    script = build_velocity_script(points)

    try:
        vehicle.ai.set_script(script, cling=True)
        print(f"[AI] {vehicle.vid}: set_script nodes={len(script)}", flush=True)
        return
    except TypeError:
        try:
            vehicle.ai.set_script(script)
            print(f"[AI] {vehicle.vid}: set_script nodes={len(script)}", flush=True)
            return
        except Exception:
            pass
    except Exception:
        pass

    line = [
        {
            "pos": (float(p["x"]), float(p["y"]), float(p["z"])),
            "speed": float(p.get("v", DEFAULT_SPEED_MPS)),
        }
        for p in points
    ]
    vehicle.ai.set_line(line, cling=True)
    if not _fallback_warned:
        print("[AI] set_script failed — using set_line", flush=True)
        _fallback_warned = True

# ---------------------------------------------------------------------------
# Smooth (no-reset) application for mid-run switches
# ---------------------------------------------------------------------------

def _velocity_to_timed(points: list[dict]) -> list[dict]:
    """Convert {x,y,z,v} points to {x,y,z,t} by integrating distances / speeds."""
    if not points:
        return []
    timed = [{"x": float(points[0]["x"]), "y": float(points[0]["y"]),
               "z": float(points[0]["z"]), "t": 0.0}]
    t = 0.0
    for i in range(1, len(points)):
        prev, curr = points[i - 1], points[i]
        dx = curr["x"] - prev["x"]
        dy = curr["y"] - prev["y"]
        dz = curr["z"] - prev["z"]
        ds = math.sqrt(dx * dx + dy * dy + dz * dz)
        v_avg = max(
            0.5 * (float(prev.get("v", DEFAULT_SPEED_MPS)) +
                   float(curr.get("v", DEFAULT_SPEED_MPS))),
            MIN_TIMING_SPEED,
        )
        t += ds / v_avg
        timed.append({"x": float(curr["x"]), "y": float(curr["y"]),
                       "z": float(curr["z"]), "t": t})
    return timed


_smooth_fallback_warned = False


def apply_ai_path_smooth(vehicle: Vehicle, vel_points: list[dict]) -> None:
    """Apply a new path mid-run without stopping the car (no_reset=True)."""
    global _smooth_fallback_warned
    script = _velocity_to_timed(vel_points)

    try:
        vehicle.ai.execute_script(script, cling=True, start_delay=0.0, no_reset=True)
        return
    except TypeError:
        try:
            vehicle.ai.execute_script(script, cling=True, no_reset=True)
            return
        except Exception:
            pass
    except Exception:
        pass

    # Fallback: execute_script not available — use set_script (brief reset)
    if not _smooth_fallback_warned:
        print("[AI] execute_script unavailable — falling back to set_script", flush=True)
        _smooth_fallback_warned = True
    apply_ai_path(vehicle, vel_points)


# ---------------------------------------------------------------------------
# Transition script builder
# ---------------------------------------------------------------------------

def build_transition_script(
    n: int,
    anchor_pos: tuple[float, float, float],
    neutral: list[dict],
    target: list[dict],
) -> list[dict]:
    """
    Velocity-based transition script:
      anchor_pos           actual vehicle position  (neutral v at index n)
      neutral[n+1..n+2]    bridge nodes             (neutral speed)
      target[n+3..]        target track continues   (target speed)
    """
    total       = len(neutral)
    bridge_end  = min(n + BRIDGE_NODES, total - 1)
    target_start = min(n + BRIDGE_NODES + 1, total - 1)

    script: list[dict] = []

    # Anchor: real vehicle position with the neutral speed at this index
    v_anchor = float(neutral[min(n, total - 1)].get("v", DEFAULT_SPEED_MPS))
    ax, ay, az = anchor_pos
    script.append({"x": float(ax), "y": float(ay), "z": float(az), "v": v_anchor})

    # Bridge: neutral nodes n+1 .. n+BRIDGE_NODES
    for i in range(n + 1, bridge_end + 1):
        p = neutral[i]
        script.append({
            "x": float(p["x"]),
            "y": float(p["y"]),
            "z": float(p["z"]),
            "v": float(p.get("v", DEFAULT_SPEED_MPS)),
        })

    # Target track from n+BRIDGE_NODES+1 onwards
    for i in range(target_start, len(target)):
        p = target[i]
        script.append({
            "x": float(p["x"]),
            "y": float(p["y"]),
            "z": float(p["z"]),
            "v": float(p.get("v", DEFAULT_SPEED_MPS)),
        })

    return script

# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------

def poll_position(beamng: BeamNGpy, vehicle: Vehicle) -> tuple[float, float, float] | None:
    try:
        vehicle.sensors.poll()
        p = vehicle.sensors["state"]["pos"]
        return float(p[0]), float(p[1]), float(p[2])
    except Exception:
        pass
    try:
        vid = getattr(vehicle, "vid", None) or EGO_ID
        st  = beamng.vehicles.get_states([vid]).get(vid)
        if st and "pos" in st:
            p = st["pos"]
            return float(p[0]), float(p[1]), float(p[2])
    except Exception:
        pass
    return None


def find_nearest_index(
    pos: tuple[float, float, float],
    points: list[dict],
    hint: int = 0,
    window: int = POSITION_SEARCH_WINDOW,
) -> int:
    """Forward-biased nearest-node search; falls back to full scan if needed."""
    px, py, pz = pos
    lo = max(0, hint)
    hi = min(len(points) - 1, hint + window)

    best_i  = lo
    best_d2 = float("inf")

    for i in range(lo, hi + 1):
        p  = points[i]
        d2 = (p["x"] - px) ** 2 + (p["y"] - py) ** 2 + (p["z"] - pz) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_i  = i

    # Full scan if nearest is suspiciously far (>10 m)
    if best_d2 > 100.0:
        for i, p in enumerate(points):
            d2 = (p["x"] - px) ** 2 + (p["y"] - py) ** 2 + (p["z"] - pz) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_i  = i

    return best_i

# ---------------------------------------------------------------------------
# Input thread
# ---------------------------------------------------------------------------

class InputThread(threading.Thread):
    MODE_KEYS = {"1": "aggressive", "2": "defensive"}

    def __init__(self) -> None:
        super().__init__(daemon=True, name="InputThread")
        self._lock    = threading.Lock()
        self._pending: str | None = None

    def run(self) -> None:
        print(
            "\n[INPUT] Press ENTER after typing:\n"
            "  1  ->  aggressive\n"
            "  2  ->  defensive\n"
            "  Ctrl+C to quit\n",
            flush=True,
        )
        for line in sys.stdin:
            key = line.strip()
            if key in self.MODE_KEYS:
                with self._lock:
                    self._pending = self.MODE_KEYS[key]
                print(f"[INPUT] Queued: {self.MODE_KEYS[key]}", flush=True)
            elif key:
                print(f"[INPUT] Unknown '{key}'. Use 1 or 2.", flush=True)

    def consume(self) -> str | None:
        with self._lock:
            val           = self._pending
            self._pending = None
            return val

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    set_up_simple_logging()

    # -- Load tracks ---------------------------------------------------------
    print("Loading tracks...", flush=True)
    neutral_track    = load_track(NEUTRAL_TRACK_PATH)
    aggressive_track = load_track(AGGRESSIVE_TRACK_PATH)
    defensive_track  = load_track(DEFENSIVE_TRACK_PATH)

    print(
        f"  neutral:    {len(neutral_track)} nodes\n"
        f"  aggressive: {len(aggressive_track)} nodes\n"
        f"  defensive:  {len(defensive_track)} nodes",
        flush=True,
    )

    tracks = {
        "neutral":    neutral_track,
        "aggressive": aggressive_track,
        "defensive":  defensive_track,
    }

    # -- BeamNG + scenario ---------------------------------------------------
    beamng = BeamNGpy("localhost", 25252, home=BEAMNG_HOME, binary=BEAMNG_BINARY)
    beamng.open(launch=True)

    scenario = Scenario(LEVEL_NAME, SCENARIO_NAME)
    ego      = Vehicle(EGO_ID, model=EGO_MODEL)

    ego_pos, ego_quat = spawn_pose(neutral_track)
    scenario.add_vehicle(ego, pos=ego_pos, rot_quat=ego_quat, cling=True)
    scenario.make(beamng)

    beamng.scenario.load(scenario)
    beamng.scenario.start()
    ego.connect(beamng)

    try:
        ego.switch()
    except Exception:
        pass

    # -- Debug polylines + node spheres --------------------------------------
    for mode_name, points in tracks.items():
        rgba = TRACK_RGBA[mode_name]

        beamng.debug.add_polyline(track_polyline(points), rgba, cling=True, offset=0.12)

        coords = [
            (float(points[i]["x"]), float(points[i]["y"]), float(points[i]["z"]))
            for i in range(0, len(points), NODE_SPHERE_STRIDE)
        ]
        try:
            beamng.debug.add_spheres(
                coords,
                [NODE_SPHERE_RADIUS] * len(coords),
                [rgba] * len(coords),
                cling=True,
                offset=0.15,
            )
        except Exception:
            pass

    beamng.control.resume()
    time.sleep(1.0)

    # -- Start on neutral track ----------------------------------------------
    current_mode = "neutral"
    apply_ai_path(ego, neutral_track)

    # -- Input thread --------------------------------------------------------
    input_thread = InputThread()
    input_thread.start()

    # -- Main poll loop ------------------------------------------------------
    current_index_hint = 0
    last_pos: tuple[float, float, float] | None = None

    print(f"\n[RUNNING] Mode: {current_mode.upper()}  |  Ctrl+C to quit\n", flush=True)

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)

            pos = poll_position(beamng, ego)
            if pos is not None:
                last_pos = pos
                current_index_hint = find_nearest_index(
                    pos, tracks[current_mode], hint=current_index_hint
                )

            target_mode = input_thread.consume()
            if target_mode is None or target_mode == current_mode:
                continue

            if last_pos is None:
                print("[SWITCH] Position unknown — skipping.", flush=True)
                continue

            n = current_index_hint
            print(
                f"[SWITCH] {current_mode.upper()} -> {target_mode.upper()} "
                f"at index {n}",
                flush=True,
            )

            transition = build_transition_script(
                n=n,
                anchor_pos=last_pos,
                neutral=neutral_track,
                target=tracks[target_mode],
            )
            apply_ai_path_smooth(ego, transition)

            current_mode       = target_mode
            current_index_hint = n

            print(
                f"[SWITCH] Now: {current_mode.upper()} | script nodes={len(transition)}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("\nStopped by user.", flush=True)
    finally:
        beamng.disconnect()


if __name__ == "__main__":
    main()
