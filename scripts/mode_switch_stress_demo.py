"""
mode_switch_stress_demo.py

Same tracks, switching mechanics, and in-game behavior as
mode_switch_hr_demo.py, but the HR -> stress logic is delegated entirely
to a standalone detector (hr_stress_detector.StressDetector or
calibrated_stress_detector.CalibratedStressDetector) instead of an
embedded policy class.

This file is responsible ONLY for:
  - BeamNG scenario/vehicle/track setup (reused, unmodified, from
    mode_switch_demo.py — same as mode_switch_hr_demo.py does today)
  - Choosing which detector implementation to use (--baseline-mode)
  - Mapping a domain-agnostic StressState to a driving mode name
  - Acting on that mode (AI path switching, HUD toasts)

It is NOT responsible for (and does not contain any code for):
  - Computing baseline/current/delta from HR samples
  - Thresholding, hysteresis, or cooldown/debounce logic
  - Anything related to what counts as "clean" HR data
  (all of the above live in hr_stress_detector.py / calibrated_stress_detector.py)

StressState -> mode mapping (rolling / calibrated):
    ELEVATED  -> aggressive
    LOWERED   -> defensive
    BASELINE  -> neutral

Slope mode (--baseline-mode slope):
    Starts on aggressive track.
    slope >= threshold -> defensive (held for 20 s, then auto-releases to aggressive)

Baseline modes (--baseline-mode):
    rolling     (default) -- hr_stress_detector.StressDetector
    calibrated  -- calibrated_stress_detector.CalibratedStressDetector
    slope       -- slope_stress_detector.SlopeStressDetector
    eda         -- eda_scr_stress_detector.EdaScrStressDetector (SCR-Bridge)
    combined    -- combined_stress_detector (HR slope OR/AND SCR peaks)

Run:
    python mode_switch_stress_demo.py
    python mode_switch_stress_demo.py --baseline-mode calibrated
    python mode_switch_stress_demo.py --baseline-mode slope
    python mode_switch_stress_demo.py --baseline-mode eda --scr-min-peaks 3
    python mode_switch_stress_demo.py --baseline-mode combined --combine-policy or

Prerequisites:
    pip install pylsl numpy beamngpy
    LSL publisher running (emotibit-live/lsl_bridge.py in this package)
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from beamngpy import BeamNGpy, Scenario, Vehicle, set_up_simple_logging

# Re-use everything from beamng_demo.mode_switch_demo
from beamng_demo.mode_switch_demo import (
    BEAMNG_BINARY,
    BEAMNG_HOME,
    BRIDGE_NODES,  # noqa: F401 – re-exported for completeness
    DEFAULT_SPEED_MPS,  # noqa: F401
    EGO_ID,
    EGO_MODEL,
    LEVEL_NAME,
    NODE_SPHERE_RADIUS,
    NODE_SPHERE_STRIDE,
    AGGRESSIVE_TRACK_PATH,
    DEFENSIVE_TRACK_PATH,
    NEUTRAL_TRACK_PATH,
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
from stress_detection.lsl_receiver import DataStore, start_receiver
from stress_detection.hr_stress_detector import (
    HR_DELTA_THRESH_BPM,
    MIN_HR_SAMPLES,
    MIN_SWITCH_INTERVAL_S,
    StressDetector,
    StressState,
)
from stress_detection.calibrated_stress_detector import (
    CALIBRATION_SAMPLES,
    CalibratedStressDetector,
    CalibratedStressDetectorConfig,
)
from stress_detection.slope_stress_detector import (
    DEFENSIVE_HOLD_S,
    MIN_SLOPE_SAMPLES,
    SLOPE_THRESH_BPM_PER_S,
    SLOPE_WINDOW_S,
    SlopeStressDetector,
    SlopeStressDetectorConfig,
)
from stress_detection.eda_scr_stress_detector import (
    MIN_SCR_SAMPLES,
    SCR_MIN_PEAKS_FOR_STRESS,
    EdaScrStressDetector,
    EdaScrStressDetectorConfig,
)
from stress_detection.combined_stress_detector import (
    COMBINE_POLICY,
    CombinedStressDetector,
    CombinedStressDetectorConfig,
)

# ---------------------------------------------------------------------------
# Status/poll tuning (mirrors mode_switch_hr_demo.py)
# ---------------------------------------------------------------------------

HR_POLL_INTERVAL_S: float = 0.2     # how often to evaluate HR
HR_STATUS_INTERVAL_S: float = 2.0   # how often to print HR status line

# ---------------------------------------------------------------------------
# StressState -> driving mode mapping (the ONLY place this mapping exists)
# ---------------------------------------------------------------------------

STATE_TO_MODE = {
    StressState.ELEVATED: "aggressive",
    StressState.LOWERED: "defensive",
    StressState.BASELINE: "neutral",
}

SLOPE_STATE_TO_MODE = {
    StressState.ELEVATED: "defensive",
    StressState.LOWERED: "aggressive",
    StressState.BASELINE: "aggressive",
}

# ---------------------------------------------------------------------------
# UI toast helper
# ---------------------------------------------------------------------------

def _show_toast(beamng: BeamNGpy, text: str) -> None:
    """Display a message on the BeamNG HUD (silently ignored if unavailable)."""
    ui = getattr(beamng, "ui", None)
    if ui is not None:
        try:
            ui.display_message(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-mode",
        choices=["rolling", "calibrated", "slope", "eda", "combined"],
        default="rolling",
        help=(
            "'rolling' (default): hr_stress_detector.StressDetector. "
            "'calibrated': fixed baseline over --calibration-samples. "
            "'slope': rate-of-change detector; see --slope-window and --slope-thresh. "
            "'eda': SCR peak count from SCR-Bridge; see --scr-min-peaks. "
            "'combined': HR slope + SCR fusion; see --combine-policy."
        ),
    )
    parser.add_argument(
        "--calibration-samples",
        type=int,
        default=CALIBRATION_SAMPLES,
        help=(
            f"Only used with --baseline-mode calibrated (default: "
            f"{CALIBRATION_SAMPLES}). Number of HR samples to record before "
            "freezing the baseline."
        ),
    )
    parser.add_argument(
        "--slope-window",
        type=float,
        default=SLOPE_WINDOW_S,
        help=f"[slope/combined] Regression window in seconds (default: {SLOPE_WINDOW_S}).",
    )
    parser.add_argument(
        "--slope-thresh",
        type=float,
        default=SLOPE_THRESH_BPM_PER_S,
        help=f"[slope/combined] Trigger threshold in BPM/s (default: {SLOPE_THRESH_BPM_PER_S}).",
    )
    parser.add_argument(
        "--defensive-hold",
        type=float,
        default=DEFENSIVE_HOLD_S,
        help=(
            f"[slope/eda/combined] Seconds in defensive before auto-release "
            f"(default: {DEFENSIVE_HOLD_S})."
        ),
    )
    parser.add_argument(
        "--scr-min-peaks",
        type=int,
        default=SCR_MIN_PEAKS_FOR_STRESS,
        help=(
            f"[eda/combined] SCR peak count threshold (default: {SCR_MIN_PEAKS_FOR_STRESS})."
        ),
    )
    parser.add_argument(
        "--combine-policy",
        choices=("or", "and"),
        default=COMBINE_POLICY,
        help=f"[combined] Fuse HR slope with SCR count (default: {COMBINE_POLICY}).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Detector adapter — the ONLY place that knows both detector implementations'
# slightly different reading shapes (warmup/n_samples vs.
# calibrating/calibration_progress). Keeps the main loop below fully
# detector-agnostic.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _NormalizedReading:
    state: StressState
    changed: bool
    in_progress: bool       # still warming up / still calibrating — no decision to act on yet
    progress_label: str     # "Warming up" or "Calibrating baseline"
    progress_current: int
    progress_target: int
    baseline_bpm: float | None
    current_bpm: float | None
    delta: float | None
    scr_no_peaks: int | None = None
    hr_cue: bool | None = None
    eda_cue: bool | None = None


def _make_detector(args: argparse.Namespace):
    """Build the configured detector plus a function to normalize its readings."""
    if args.baseline_mode == "combined":
        return _make_combined_detector(args)
    if args.baseline_mode == "eda":
        return _make_eda_detector(args)
    if args.baseline_mode == "slope":
        return _make_slope_detector(args)

    if args.baseline_mode == "calibrated":
        detector = CalibratedStressDetector(
            CalibratedStressDetectorConfig(
                calibration_samples=args.calibration_samples,
                delta_thresh_bpm=HR_DELTA_THRESH_BPM,
                min_switch_interval_s=MIN_SWITCH_INTERVAL_S,
            )
        )

        def _normalize(reading, hr) -> _NormalizedReading:
            return _NormalizedReading(
                state=reading.state,
                changed=reading.changed,
                in_progress=reading.calibrating,
                progress_label="Calibrating baseline",
                progress_current=reading.calibration_progress,
                progress_target=reading.calibration_target,
                baseline_bpm=reading.baseline_bpm,
                current_bpm=reading.current_bpm,
                delta=reading.delta,
            )

        ready_message = (
            f"Calibrating: {args.calibration_samples} samples | "
            f"threshold: \u00b1{HR_DELTA_THRESH_BPM} BPM | "
            f"cooldown: {MIN_SWITCH_INTERVAL_S} s"
        )
        return detector, _normalize, ready_message

    detector = StressDetector()

    def _normalize(reading, hr) -> _NormalizedReading:
        return _NormalizedReading(
            state=reading.state,
            changed=reading.changed,
            in_progress=reading.warmup,
            progress_label="Warming up",
            progress_current=reading.n_samples,
            progress_target=MIN_HR_SAMPLES,
            baseline_bpm=reading.baseline_bpm,
            current_bpm=reading.current_bpm,
            delta=reading.delta,
        )

    ready_message = (
        f"Warmup: {MIN_HR_SAMPLES} samples | "
        f"threshold: \u00b1{HR_DELTA_THRESH_BPM} BPM | "
        f"cooldown: {MIN_SWITCH_INTERVAL_S} s"
    )
    return detector, _normalize, ready_message


def _make_slope_detector(args: argparse.Namespace):
    detector = SlopeStressDetector(
        SlopeStressDetectorConfig(
            slope_window_s=args.slope_window,
            slope_thresh_bpm_per_s=args.slope_thresh,
            min_slope_samples=MIN_SLOPE_SAMPLES,
            defensive_hold_s=args.defensive_hold,
        )
    )

    def _normalize(reading, hr) -> _NormalizedReading:
        return _NormalizedReading(
            state=reading.state,
            changed=reading.changed,
            in_progress=reading.warmup,
            progress_label="Warming up (slope)",
            progress_current=reading.n_samples,
            progress_target=MIN_SLOPE_SAMPLES,
            baseline_bpm=None,
            current_bpm=reading.current_bpm,
            delta=reading.slope,
        )

    ready_message = (
        f"Warmup: {MIN_SLOPE_SAMPLES} samples | "
        f"threshold: +{args.slope_thresh} BPM/s | "
        f"window: {args.slope_window}s | "
        f"defensive hold: {args.defensive_hold}s | "
        f"default mode: AGGRESSIVE"
    )
    return detector, _normalize, ready_message


def _make_eda_detector(args: argparse.Namespace):
    hold = args.defensive_hold
    detector = EdaScrStressDetector(
        EdaScrStressDetectorConfig(
            min_peaks_for_stress=args.scr_min_peaks,
            min_scr_samples=MIN_SCR_SAMPLES,
            defensive_hold_s=hold,
        )
    )

    def _normalize(reading, _hr) -> _NormalizedReading:
        return _NormalizedReading(
            state=reading.state,
            changed=reading.changed,
            in_progress=reading.warmup,
            progress_label="Warming up (EDA)",
            progress_current=reading.n_updates,
            progress_target=MIN_SCR_SAMPLES,
            baseline_bpm=reading.scr_peaks,
            current_bpm=None,
            delta=float(reading.scr_no_peaks) if reading.scr_no_peaks is not None else None,
        )

    ready_message = (
        f"SCR threshold: >={args.scr_min_peaks} peaks/window | "
        f"defensive hold: {hold}s | default mode: AGGRESSIVE"
    )
    return detector, _normalize, ready_message


def _make_combined_detector(args: argparse.Namespace):
    hold = args.defensive_hold
    detector = CombinedStressDetector(
        CombinedStressDetectorConfig(
            slope_window_s=args.slope_window,
            slope_thresh_bpm_per_s=args.slope_thresh,
            min_peaks_for_stress=args.scr_min_peaks,
            combine_policy=args.combine_policy,
            defensive_hold_s=hold,
        )
    )

    def _normalize(reading, _hr) -> _NormalizedReading:
        progress = MIN_SLOPE_SAMPLES
        if reading.warmup and reading.slope is None:
            progress = MIN_SCR_SAMPLES
        return _NormalizedReading(
            state=reading.state,
            changed=reading.changed,
            in_progress=reading.warmup,
            progress_label="Warming up (HR+EDA)",
            progress_current=progress,
            progress_target=max(MIN_SLOPE_SAMPLES, MIN_SCR_SAMPLES),
            baseline_bpm=reading.scr_peaks,
            current_bpm=reading.current_bpm,
            delta=reading.slope,
            scr_no_peaks=reading.scr_no_peaks,
            hr_cue=reading.hr_cue,
            eda_cue=reading.eda_cue,
        )

    ready_message = (
        f"policy: {args.combine_policy.upper()} | "
        f"HR slope>={args.slope_thresh} BPM/s | "
        f"SCR>={args.scr_min_peaks} peaks | "
        f"defensive hold: {hold}s | default mode: AGGRESSIVE"
    )
    return detector, _normalize, ready_message


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
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

    scenario = Scenario(LEVEL_NAME, "mode_switch_stress_demo")
    ego      = Vehicle(EGO_ID, model=EGO_MODEL)

    aggressive_start = args.baseline_mode in ("slope", "eda", "combined")
    spawn_track = aggressive_track if aggressive_start else neutral_track
    start_mode = "aggressive" if aggressive_start else "neutral"

    ego_pos, ego_quat = spawn_pose(spawn_track)
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

    current_mode = start_mode
    apply_ai_path(ego, tracks[start_mode])
    print(f"[AI] Started on {start_mode} track ({len(tracks[start_mode])} nodes)", flush=True)

    # -- LSL HR receiver (started only after scenario is live) ---------------
    if args.baseline_mode == "eda":
        sensor_label = "EDA"
    elif args.baseline_mode == "combined":
        sensor_label = "HR+EDA"
    else:
        sensor_label = "HR"
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

    # -- Main loop -----------------------------------------------------------
    current_index_hint  = 0
    last_pos: tuple[float, float, float] | None = None
    last_status_mono    = time.monotonic()

    print(f"\n[RUNNING] Mode: {current_mode.upper()}  |  Ctrl+C to quit\n", flush=True)

    try:
        while True:
            time.sleep(HR_POLL_INTERVAL_S)
            now = time.monotonic()  # capture AFTER sleep so comparisons are accurate

            # -- Update position ---------------------------------------------
            pos = poll_position(beamng, ego)
            if pos is not None:
                last_pos = pos
                current_index_hint = find_nearest_index(
                    pos, tracks[current_mode], hint=current_index_hint,
                    window=POSITION_SEARCH_WINDOW,
                )

            # -- Evaluate stress detector -------------------------------------
            hr = store.get_hr()
            if args.baseline_mode == "eda":
                scr_feat = store.get_scr_features()
                if scr_feat is None:
                    if now - last_status_mono >= HR_STATUS_INTERVAL_S:
                        last_status_mono = now
                        print("[EDA] waiting for SCR-Bridge samples...", flush=True)
                    continue
                scr_peaks, scr_no_peaks = scr_feat
                raw_reading = detector.update(
                    int(round(scr_no_peaks)),
                    scr_peaks=scr_peaks,
                    now=now,
                )
                reading = normalize_reading(raw_reading, [])
            elif args.baseline_mode == "combined":
                scr_feat = store.get_scr_features()
                if scr_feat is None:
                    if now - last_status_mono >= HR_STATUS_INTERVAL_S:
                        last_status_mono = now
                        print("[HR+EDA] waiting for SCR-Bridge samples...", flush=True)
                    continue
                scr_peaks, scr_no_peaks = scr_feat
                raw_reading = detector.update(
                    hr,
                    int(round(scr_no_peaks)),
                    scr_peaks=scr_peaks,
                    now=now,
                )
                reading = normalize_reading(raw_reading, hr)
            else:
                raw_reading = detector.update(hr, now=now)
                reading = normalize_reading(raw_reading, hr)

            if reading.in_progress:
                if now - last_status_mono >= HR_STATUS_INTERVAL_S:
                    last_status_mono = now
                    if args.baseline_mode == "eda":
                        latest = "waiting for SCR window"
                    elif args.baseline_mode == "combined":
                        latest = (
                            f"{hr[-1]:.1f} BPM" if hr else "no HR"
                        ) + " + SCR window"
                    else:
                        latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                    print(
                        f"[{sensor_label}] {reading.progress_label}... "
                        f"{reading.progress_current}/{reading.progress_target} samples"
                        f"  (latest: {latest})",
                        flush=True,
                    )
                    _show_toast(
                        beamng,
                        f"{reading.progress_label}\n"
                        f"({reading.progress_current}/{reading.progress_target})\n{latest}",
                    )
                continue

            target_mode = state_to_mode[reading.state]

            if now - last_status_mono >= HR_STATUS_INTERVAL_S:
                last_status_mono = now
                if args.baseline_mode == "eda":
                    amp = (
                        f"{reading.baseline_bpm:.4f}"
                        if reading.baseline_bpm is not None
                        else "n/a"
                    )
                    metric_str = (
                        f"scr_no_peaks={int(reading.delta or 0)}  scr_peaks={amp}"
                    )
                    print(
                        f"[EDA] {metric_str}  mode={current_mode.upper()}",
                        flush=True,
                    )
                    _show_toast(
                        beamng,
                        f"Mode: {current_mode.upper()}\n"
                        f"SCR peaks: {int(reading.delta or 0)}",
                    )
                elif args.baseline_mode == "combined":
                    slope_s = (
                        f"{reading.delta:+.4f}"
                        if reading.delta is not None
                        else "n/a"
                    )
                    bpm = (
                        f"{reading.current_bpm:.1f}"
                        if reading.current_bpm is not None
                        else "n/a"
                    )
                    metric_str = (
                        f"HR={bpm} slope={slope_s}  "
                        f"scr_no_peaks={reading.scr_no_peaks}  "
                        f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}"
                    )
                    print(
                        f"[HR+EDA] {metric_str}  mode={current_mode.upper()}",
                        flush=True,
                    )
                    _show_toast(
                        beamng,
                        f"Mode: {current_mode.upper()}\n"
                        f"SCR n={reading.scr_no_peaks}  slope {slope_s}",
                    )
                elif reading.baseline_bpm is not None and reading.current_bpm is not None:
                    metric_str = (
                        f"baseline={reading.baseline_bpm:.1f}  "
                        f"delta={reading.delta:+.1f} BPM"
                    )
                    print(
                        f"[HR] current={reading.current_bpm:.1f}  "
                        f"{metric_str}  mode={current_mode.upper()}",
                        flush=True,
                    )
                    _show_toast(
                        beamng,
                        f"Mode: {current_mode.upper()}\n"
                        f"{reading.current_bpm:.0f} BPM  (\u0394{reading.delta:+.1f})",
                    )
                else:
                    metric_str = f"slope={reading.delta:+.4f} BPM/s"
                    print(
                        f"[HR] current={reading.current_bpm:.1f}  "
                        f"{metric_str}  mode={current_mode.upper()}",
                        flush=True,
                    )
                    _show_toast(
                        beamng,
                        f"Mode: {current_mode.upper()}\n"
                        f"{reading.current_bpm:.0f} BPM  slope {reading.delta:+.2f}",
                    )

            # -- Switch if the detector just committed a new state -----------
            if not reading.changed or target_mode == current_mode:
                continue

            if last_pos is None:
                print("[SWITCH] Position unknown — skipping.", flush=True)
                continue

            n = current_index_hint
            if args.baseline_mode == "eda":
                signal_str = f"scr_no_peaks={int(reading.delta or 0)}"
            elif args.baseline_mode == "combined":
                slope_part = (
                    f"{reading.delta:+.4f}"
                    if reading.delta is not None
                    else "n/a"
                )
                signal_str = (
                    f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  "
                    f"slope={slope_part} BPM/s  "
                    f"scr_no_peaks={reading.scr_no_peaks}"
                )
            elif reading.baseline_bpm is not None and reading.current_bpm is not None:
                signal_str = f"delta={reading.delta:+.1f} BPM"
            else:
                signal_str = f"slope={reading.delta:+.4f} BPM/s"
            print(
                f"[SWITCH] {current_mode.upper()} -> {target_mode.upper()} "
                f"at index {n}  ({signal_str})",
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
                f"[SWITCH] Now: {current_mode.upper()} | "
                f"script nodes={len(transition)}",
                flush=True,
            )
            # Show the new mode immediately on the HUD without waiting for the
            # next status interval.
            if args.baseline_mode == "eda":
                _show_toast(
                    beamng,
                    f"Mode: {current_mode.upper()}\n"
                    f"SCR peaks: {int(reading.delta or 0)}",
                )
            elif args.baseline_mode == "combined":
                _show_toast(
                    beamng,
                    f"Mode: {current_mode.upper()}\n"
                    f"SCR n={reading.scr_no_peaks}",
                )
            elif reading.current_bpm is not None:
                _show_toast(
                    beamng,
                    f"Mode: {current_mode.upper()}\n"
                    f"{reading.current_bpm:.0f} BPM  (\u0394{reading.delta:+.1f})",
                )

    except KeyboardInterrupt:
        print("\nStopped by user.", flush=True)
    finally:
        beamng.disconnect()


if __name__ == "__main__":
    main()
