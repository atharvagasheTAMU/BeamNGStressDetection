"""
mode_switch_stress_npc_demo.py

mode_switch_stress_demo.py + pooled NPC traffic on the Southern Highway map.

What it does
  - Stress detection / aggressive-defensive-neutral switching: identical to
    mode_switch_stress_demo.py (detector, --baseline-mode flags, HUD toasts,
    smooth path switching are all reused from that file).
  - NPC traffic: a fixed pool of reusable vehicles (npc_traffic/config.py:
    POOL_SIZE / POOL_MODELS) is created once at startup and parked off-road.
    When the ego reaches an NPC's trigger node, a free pool vehicle is
    teleported to that NPC's spawn point and given its AI route. When the NPC
    reaches its despawn node it is held + teleported back to its parking spot
    and the slot is reused. No spawn / delete / connect calls during the drive.

Run (from the BeamNGStressDetection folder, venv active):
    python scripts/mode_switch_stress_npc_demo.py --baseline-mode slope
    python scripts/mode_switch_stress_npc_demo.py --baseline-mode combined --combine-policy or

All flags are the same as mode_switch_stress_demo.py.
Prerequisites: same as mode_switch_stress_demo.py (LSL bridge running).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from beamngpy import BeamNGpy, Scenario, Vehicle, set_up_simple_logging

from beamng_demo.mode_switch_demo import (
    BEAMNG_BINARY,
    BEAMNG_HOME,
    NODE_SPHERE_RADIUS,
    NODE_SPHERE_STRIDE,
    POLL_INTERVAL_S,
    POSITION_SEARCH_WINDOW,
    TRACK_RGBA,
    apply_ai_path,
    apply_ai_path_smooth,
    build_transition_script,
    find_nearest_index,
    load_track,
    poll_position,
    spawn_pose,
    track_polyline,
)
from beamng_demo.npc_traffic.config import (
    LEVEL_NAME,
    MAIN_TRACK_PATH,
    MAX_ASSIGNS_PER_TICK,
    MAX_RELEASES_PER_TICK,
    NODE_REACH_RADIUS,
    NPC_SPAWNS,
    PARK_LATERAL_OFFSET_M,
    PARK_SPACING_M,
    POOL_CONNECT_DELAY_S,
    POOL_SIZE,
    SCENARIO_NAME,
    VERBOSE_DEBUG,
)
from beamng_demo.npc_traffic.pool_setup import (
    compute_park_positions,
    print_pool_distribution,
    validate_pool_models,
    validate_spawns,
)
from beamng_demo.npc_traffic.vehicle_pool import NpcVehiclePool
from beamng_demo.npc_traffic.vehicle_pool_runtime import NpcVehiclePoolMonitor
from stress_detection.lsl_receiver import DataStore, start_receiver

# Reuse the stress-detection plumbing from the existing demo unchanged.
from mode_switch_stress_demo import (
    HR_POLL_INTERVAL_S,
    HR_STATUS_INTERVAL_S,
    SLOPE_STATE_TO_MODE,
    STATE_TO_MODE,
    _make_detector,
    _parse_args,
    _show_toast,
)

# ===========================================================================
# ===== DRIVING TRACKS — CHANGE THESE WHEN THE NEW SCENARIO TRACKS ARRIVE =====
# ===========================================================================
#
# The new Southern Highway scenario has no dedicated aggressive / defensive
# tracks yet (a colleague is producing them). Until then ALL three driving
# modes use the same default path, so switching modes is currently a no-op on
# the road (the switching machinery still runs and logs).
#
# TODO(new-tracks): when the real tracks are delivered:
#   1. Drop the JSON files into  beamng_demo/tracks/southern_highway/
#   2. Point the two lines below at them, e.g.
#          AGGRESSIVE_TRACK_PATH = SCENARIO_TRACKS_DIR / "aggressive_southern.json"
#          DEFENSIVE_TRACK_PATH  = SCENARIO_TRACKS_DIR / "defensive_southern.json"
#   3. (Optional) point NEUTRAL_TRACK_PATH at a neutral variant too. Neutral is
#      used for the bridge nodes when switching and as the start track for the
#      'rolling' / 'calibrated' baseline modes.
#   4. Keep the new tracks on the SAME road with the SAME node ordering as the
#      default track (mode switching maps the current node index across tracks).
#   5. Leave TRIGGER_TRACK_PATH alone unless NPC ego_trigger_node values in
#      npc_traffic/config.py are re-numbered against a new file: NPC triggers
#      are position-based against that track, independent of which driving
#      track the ego is currently on.

DEFAULT_PATH = MAIN_TRACK_PATH   # beamng_demo/tracks/southern_highway/TNmapedited7.json

NEUTRAL_TRACK_PATH    = DEFAULT_PATH
AGGRESSIVE_TRACK_PATH = DEFAULT_PATH   # <-- CHANGE to the new aggressive track
DEFENSIVE_TRACK_PATH  = DEFAULT_PATH   # <-- CHANGE to the new defensive track

# Track that NPC ego_trigger_node numbers refer to (see note 5 above).
TRIGGER_TRACK_PATH = MAIN_TRACK_PATH

EGO_ID = "ego"
EGO_MODEL = "bastion"

# Draw mode tracks + NPC routes in-game (slow on long tracks; debug only).
DRAW_DEBUG_TRACKS = False


# ---------------------------------------------------------------------------
# Reading -> console / HUD text (same wording as mode_switch_stress_demo.py)
# ---------------------------------------------------------------------------

def _status_text(baseline_mode: str, reading, current_mode: str) -> tuple[str, str]:
    """(console line, HUD toast) for the periodic status update."""
    mode = current_mode.upper()
    if baseline_mode == "eda":
        amp = f"{reading.baseline_bpm:.4f}" if reading.baseline_bpm is not None else "n/a"
        n = int(reading.delta or 0)
        return (
            f"[EDA] scr_no_peaks={n}  scr_peaks={amp}  mode={mode}",
            f"Mode: {mode}\nSCR peaks: {n}",
        )
    if baseline_mode == "combined":
        slope_s = f"{reading.delta:+.4f}" if reading.delta is not None else "n/a"
        bpm = f"{reading.current_bpm:.1f}" if reading.current_bpm is not None else "n/a"
        return (
            f"[HR+EDA] HR={bpm} slope={slope_s}  scr_no_peaks={reading.scr_no_peaks}  "
            f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  mode={mode}",
            f"Mode: {mode}\nSCR n={reading.scr_no_peaks}  slope {slope_s}",
        )
    if reading.baseline_bpm is not None and reading.current_bpm is not None:
        return (
            f"[HR] current={reading.current_bpm:.1f}  baseline={reading.baseline_bpm:.1f}  "
            f"delta={reading.delta:+.1f} BPM  mode={mode}",
            f"Mode: {mode}\n{reading.current_bpm:.0f} BPM  (\u0394{reading.delta:+.1f})",
        )
    return (
        f"[HR] current={reading.current_bpm:.1f}  slope={reading.delta:+.4f} BPM/s  mode={mode}",
        f"Mode: {mode}\n{reading.current_bpm:.0f} BPM  slope {reading.delta:+.2f}",
    )


def _signal_text(baseline_mode: str, reading) -> str:
    """Short description of the signal that triggered a mode switch."""
    if baseline_mode == "eda":
        return f"scr_no_peaks={int(reading.delta or 0)}"
    if baseline_mode == "combined":
        slope_part = f"{reading.delta:+.4f}" if reading.delta is not None else "n/a"
        return (
            f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  "
            f"slope={slope_part} BPM/s  scr_no_peaks={reading.scr_no_peaks}"
        )
    if reading.baseline_bpm is not None and reading.current_bpm is not None:
        return f"delta={reading.delta:+.1f} BPM"
    return f"slope={reading.delta:+.4f} BPM/s"


def _switch_toast(baseline_mode: str, reading, new_mode: str) -> str | None:
    mode = new_mode.upper()
    if baseline_mode == "eda":
        return f"Mode: {mode}\nSCR peaks: {int(reading.delta or 0)}"
    if baseline_mode == "combined":
        return f"Mode: {mode}\nSCR n={reading.scr_no_peaks}"
    if reading.current_bpm is not None:
        return f"Mode: {mode}\n{reading.current_bpm:.0f} BPM  (\u0394{reading.delta:+.1f})"
    return None


# ---------------------------------------------------------------------------
# Stress -> driving mode controller
# ---------------------------------------------------------------------------

class StressModeController:
    """Evaluates the stress detector and switches the ego's driving path."""

    def __init__(
        self,
        *,
        args,
        beamng: BeamNGpy,
        ego: Vehicle,
        tracks: dict[str, list[dict]],
        start_mode: str,
        detector,
        normalize_reading,
        state_to_mode: dict,
        store: DataStore,
        sensor_label: str,
    ) -> None:
        self.args = args
        self.beamng = beamng
        self.ego = ego
        self.tracks = tracks
        self.current_mode = start_mode
        self.detector = detector
        self.normalize_reading = normalize_reading
        self.state_to_mode = state_to_mode
        self.store = store
        self.sensor_label = sensor_label

        self.index_hint = 0
        self.last_pos: tuple[float, float, float] | None = None
        self.last_status_mono = time.monotonic()

    def update_position(self, pos: tuple[float, float, float] | None) -> None:
        if pos is None:
            return
        self.last_pos = pos
        self.index_hint = find_nearest_index(
            pos,
            self.tracks[self.current_mode],
            hint=self.index_hint,
            window=POSITION_SEARCH_WINDOW,
        )

    def _status_due(self, now: float) -> bool:
        if now - self.last_status_mono >= HR_STATUS_INTERVAL_S:
            self.last_status_mono = now
            return True
        return False

    def _read_detector(self, now: float):
        """Run the detector once. Returns a normalized reading, or None if
        required sensor data is not available yet."""
        mode = self.args.baseline_mode
        hr = self.store.get_hr()

        if mode in ("eda", "combined"):
            scr_feat = self.store.get_scr_features()
            if scr_feat is None:
                if self._status_due(now):
                    label = "EDA" if mode == "eda" else "HR+EDA"
                    print(f"[{label}] waiting for SCR-Bridge samples...", flush=True)
                return None
            scr_peaks, scr_no_peaks = scr_feat
            if mode == "eda":
                raw = self.detector.update(
                    int(round(scr_no_peaks)), scr_peaks=scr_peaks, now=now
                )
                return self.normalize_reading(raw, [])
            raw = self.detector.update(
                hr, int(round(scr_no_peaks)), scr_peaks=scr_peaks, now=now
            )
            return self.normalize_reading(raw, hr)

        raw = self.detector.update(hr, now=now)
        return self.normalize_reading(raw, hr)

    def _progress_latest(self, hr) -> str:
        mode = self.args.baseline_mode
        if mode == "eda":
            return "waiting for SCR window"
        if mode == "combined":
            return (f"{hr[-1]:.1f} BPM" if hr else "no HR") + " + SCR window"
        return f"{hr[-1]:.1f} BPM" if hr else "no samples yet"

    def evaluate(self, now: float) -> None:
        reading = self._read_detector(now)
        if reading is None:
            return

        if reading.in_progress:
            if self._status_due(now):
                latest = self._progress_latest(self.store.get_hr())
                print(
                    f"[{self.sensor_label}] {reading.progress_label}... "
                    f"{reading.progress_current}/{reading.progress_target} samples"
                    f"  (latest: {latest})",
                    flush=True,
                )
                _show_toast(
                    self.beamng,
                    f"{reading.progress_label}\n"
                    f"({reading.progress_current}/{reading.progress_target})\n{latest}",
                )
            return

        baseline_mode = self.args.baseline_mode
        target_mode = self.state_to_mode[reading.state]

        if self._status_due(now):
            console, toast = _status_text(baseline_mode, reading, self.current_mode)
            print(console, flush=True)
            _show_toast(self.beamng, toast)

        if not reading.changed or target_mode == self.current_mode:
            return

        if self.last_pos is None:
            print("[SWITCH] Position unknown — skipping.", flush=True)
            return

        n = self.index_hint
        print(
            f"[SWITCH] {self.current_mode.upper()} -> {target_mode.upper()} "
            f"at index {n}  ({_signal_text(baseline_mode, reading)})",
            flush=True,
        )

        transition = build_transition_script(
            n=n,
            anchor_pos=self.last_pos,
            neutral=self.tracks["neutral"],
            target=self.tracks[target_mode],
        )
        apply_ai_path_smooth(self.ego, transition)

        self.current_mode = target_mode
        self.index_hint = n
        print(
            f"[SWITCH] Now: {self.current_mode.upper()} | script nodes={len(transition)}",
            flush=True,
        )
        toast = _switch_toast(baseline_mode, reading, self.current_mode)
        if toast is not None:
            _show_toast(self.beamng, toast)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _draw_debug(beamng: BeamNGpy, tracks: dict[str, list[dict]], npc_tracks: dict) -> None:
    try:
        for mode_name, points in tracks.items():
            rgba = TRACK_RGBA[mode_name]
            beamng.debug.add_polyline(track_polyline(points), rgba, cling=True, offset=0.12)
            coords = [
                (float(points[i]["x"]), float(points[i]["y"]), float(points[i]["z"]))
                for i in range(0, len(points), NODE_SPHERE_STRIDE)
            ]
            beamng.debug.add_spheres(
                coords,
                [NODE_SPHERE_RADIUS] * len(coords),
                [rgba] * len(coords),
                cling=True,
                offset=0.15,
            )
        for config in NPC_SPAWNS:
            beamng.debug.add_polyline(
                track_polyline(npc_tracks[config.vid]),
                config.track_rgba,
                cling=True,
                offset=0.12,
            )
    except Exception as exc:
        print(f"  [DEBUG] Could not draw debug lines: {exc}", flush=True)


def main() -> None:
    args = _parse_args()
    set_up_simple_logging()

    # -- Load tracks ---------------------------------------------------------
    print("Loading tracks...", flush=True)
    tracks = {
        "neutral": load_track(NEUTRAL_TRACK_PATH),
        "aggressive": load_track(AGGRESSIVE_TRACK_PATH),
        "defensive": load_track(DEFENSIVE_TRACK_PATH),
    }
    for name, pts in tracks.items():
        print(f"  {name:<10}: {len(pts)} nodes", flush=True)

    trigger_track = load_track(TRIGGER_TRACK_PATH)
    npc_tracks = {config.vid: load_track(config.npc_track_path) for config in NPC_SPAWNS}
    validate_spawns(trigger_track, npc_tracks)
    model_counts = validate_pool_models()
    print(f"  Loaded {len(NPC_SPAWNS)} NPC configs", flush=True)
    print_pool_distribution(model_counts)

    # -- BeamNG + scenario (ego + parked NPC pool) ---------------------------
    beamng = BeamNGpy("localhost", 25252, home=BEAMNG_HOME, binary=BEAMNG_BINARY)
    beamng.open(launch=True)

    scenario = Scenario(LEVEL_NAME, SCENARIO_NAME)
    ego = Vehicle(EGO_ID, model=EGO_MODEL)

    aggressive_start = args.baseline_mode in ("slope", "eda", "combined")
    start_mode = "aggressive" if aggressive_start else "neutral"

    ego_pos, ego_quat = spawn_pose(tracks[start_mode])
    scenario.add_vehicle(ego, pos=ego_pos, rot_quat=ego_quat, cling=True)

    park_positions = compute_park_positions(
        trigger_track, POOL_SIZE, PARK_LATERAL_OFFSET_M, PARK_SPACING_M
    )
    pool = NpcVehiclePool(model_counts)
    pool.add_to_scenario(scenario, park_positions, park_quat=ego_quat)
    print(f"  Pre-placed {pool.pool_size} reusable NPC vehicles at parking spots", flush=True)

    scenario.set_initial_focus(EGO_ID)
    scenario.make(beamng)
    # connect_existing_vehicles=False: loading would otherwise connect every
    # pool vehicle synchronously (~1 s each), which can exceed beamngpy's
    # timeout and wedge BeamNG. Pool vehicles are connected afterwards.
    # connect_player_vehicle=True is required so the scenario briefing screen
    # dismisses correctly.
    beamng.scenario.load(
        scenario, connect_player_vehicle=True, connect_existing_vehicles=False
    )
    beamng.scenario.start()

    try:
        ego.switch()
    except Exception:
        pass

    print("\nConnecting and holding the NPC pool (one-time cost)...", flush=True)
    pool.connect_and_hold(beamng, batch_delay_s=POOL_CONNECT_DELAY_S)
    print(f"  All {pool.pool_size} pool vehicles connected and held", flush=True)

    if DRAW_DEBUG_TRACKS:
        _draw_debug(beamng, tracks, npc_tracks)

    beamng.control.resume()
    time.sleep(1.0)

    apply_ai_path(ego, tracks[start_mode])
    print(f"[AI] Started on {start_mode} track ({len(tracks[start_mode])} nodes)", flush=True)

    # -- LSL receiver (started only after the scenario is live) --------------
    sensor_label = {"eda": "EDA", "combined": "HR+EDA"}.get(args.baseline_mode, "HR")
    print(f"[{sensor_label}] Scenario running — connecting to LSL streams...", flush=True)
    store = DataStore()
    try:
        start_receiver(
            store,
            wait_time=5.0,
            require_hr=args.baseline_mode != "eda",
            require_scr=args.baseline_mode in ("eda", "combined"),
        )
    except RuntimeError as exc:
        print(
            f"\n[{sensor_label}] ERROR: {exc}\n"
            "  Start emotibit-live/lsl_bridge.py (UDP + LSL publisher).\n"
            "  Currently found streams are printed above.\n",
            flush=True,
        )
        beamng.disconnect()
        return

    detector, normalize_reading, ready_message = _make_detector(args)
    state_to_mode = (
        SLOPE_STATE_TO_MODE
        if args.baseline_mode in ("slope", "eda", "combined")
        else STATE_TO_MODE
    )
    print(
        f"[{sensor_label}] Receiver ready. Baseline mode: {args.baseline_mode.upper()} | "
        f"{ready_message}",
        flush=True,
    )

    controller = StressModeController(
        args=args,
        beamng=beamng,
        ego=ego,
        tracks=tracks,
        start_mode=start_mode,
        detector=detector,
        normalize_reading=normalize_reading,
        state_to_mode=state_to_mode,
        store=store,
        sensor_label=sensor_label,
    )

    monitor = NpcVehiclePoolMonitor(
        beamng=beamng,
        ego=ego,
        pool=pool,
        main_track=trigger_track,
        npc_tracks=npc_tracks,
        npc_config_by_vid={config.vid: config for config in NPC_SPAWNS},
        poll_interval_s=POLL_INTERVAL_S,
        node_reach_radius=NODE_REACH_RADIUS,
        max_assigns_per_tick=MAX_ASSIGNS_PER_TICK,
        max_releases_per_tick=MAX_RELEASES_PER_TICK,
        verbose=VERBOSE_DEBUG,
    )

    # -- Main loop -----------------------------------------------------------
    # Ticks at POLL_INTERVAL_S (NPC triggers / despawns need the finer rate);
    # the stress detector is evaluated every HR_POLL_INTERVAL_S.
    print(f"\n[RUNNING] Mode: {controller.current_mode.upper()}  |  Ctrl+C to quit\n", flush=True)
    last_eval_mono = 0.0
    try:
        while True:
            time.sleep(POLL_INTERVAL_S)

            pos = poll_position(beamng, ego)
            controller.update_position(pos)
            monitor.tick(pos)

            now = time.monotonic()  # after tick: NPC assigns can take a while
            if now - last_eval_mono >= HR_POLL_INTERVAL_S:
                last_eval_mono = now
                controller.evaluate(now)

    except KeyboardInterrupt:
        print("\nStopped by user.", flush=True)
    finally:
        print("Disconnecting from BeamNG...", flush=True)
        beamng.disconnect()


if __name__ == "__main__":
    main()
