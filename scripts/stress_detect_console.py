"""
stress_detect_console.py
========================
Standalone HR stress detection console — no BeamNG required.

Connects to an LSL HR stream and runs whichever detector is selected via
--baseline-mode, printing live state readings to the terminal.  Useful for
calibrating thresholds and validating detection behaviour before wiring
into a BeamNG session.

Supported modes (mirrors mode_switch_stress_demo.py):
    rolling     (default) — rolling-mean baseline, ±8 BPM delta threshold
    calibrated  — fixed baseline recorded over N samples, then frozen
    slope       — linear-regression rate-of-change in BPM/s
    eda         — SCR peak count from SCR-Bridge (emotibit-live lsl_bridge.py)
    combined    — HR slope fused with SCR peaks (OR/AND)

Slope / EDA / combined mode behaviour:
    Default mode: AGGRESSIVE
    slope >= threshold -> DEFENSIVE (held for defensive_hold_s, then auto-release)

Run:
    python stress_detect_console.py
    python stress_detect_console.py --baseline-mode calibrated --calibration-samples 30
    python stress_detect_console.py --baseline-mode slope --slope-window 4 --slope-thresh 1.3
    python stress_detect_console.py --baseline-mode eda --scr-min-peaks 3
    python stress_detect_console.py --baseline-mode combined --combine-policy or

Prerequisites:
    pip install pylsl numpy
    emotibit-live lsl_bridge.py running (HR-Bridge and/or SCR-Bridge)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from stress_detection.lsl_receiver import DataStore, start_receiver
from stress_detection.hr_stress_detector import (
    HR_DELTA_THRESH_BPM,
    MIN_HR_SAMPLES,
    MIN_SWITCH_INTERVAL_S,
    StressDetector,
    StressState,
)
from calibrated_stress_detector import (
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

POLL_INTERVAL_S: float = 0.2
STATUS_INTERVAL_S: float = 2.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--baseline-mode",
        choices=["rolling", "calibrated", "slope", "eda", "combined"],
        default="rolling",
        help=(
            "Detection strategy: 'rolling' (default) uses a drifting rolling "
            "mean as baseline; 'calibrated' records a fixed baseline first; "
            "'slope' triggers on BPM/s rate of change; "
            "'eda' uses SCR peak count from SCR-Bridge; "
            "'combined' fuses HR slope with SCR count."
        ),
    )
    parser.add_argument(
        "--calibration-samples",
        type=int,
        default=CALIBRATION_SAMPLES,
        help=f"[calibrated] Samples to collect before freezing baseline (default: {CALIBRATION_SAMPLES}).",
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
            f"[slope/eda/combined] Seconds in defensive before auto-release to aggressive "
            f"(default: {DEFENSIVE_HOLD_S})."
        ),
    )
    parser.add_argument(
        "--scr-min-peaks",
        type=int,
        default=SCR_MIN_PEAKS_FOR_STRESS,
        help=f"[eda/combined] SCR peak count threshold (default: {SCR_MIN_PEAKS_FOR_STRESS}).",
    )
    parser.add_argument(
        "--combine-policy",
        choices=("or", "and"),
        default=COMBINE_POLICY,
        help=f"[combined] Fuse HR slope with SCR count (default: {COMBINE_POLICY}).",
    )
    parser.add_argument(
        "--lsl-wait",
        type=float,
        default=5.0,
        help="LSL stream discovery wait time in seconds (default: 5).",
    )
    return parser.parse_args()


def _run_rolling(store: DataStore) -> None:
    detector = StressDetector()
    print(
        f"\n[rolling] Warmup: {MIN_HR_SAMPLES} samples | "
        f"threshold: +/-{HR_DELTA_THRESH_BPM} BPM | "
        f"cooldown: {MIN_SWITCH_INTERVAL_S}s\n",
        flush=True,
    )

    last_status = time.monotonic()
    while True:
        time.sleep(POLL_INTERVAL_S)
        now = time.monotonic()
        hr = store.get_hr()
        reading = detector.update(hr, now=now)

        if reading.warmup:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                print(
                    f"[rolling] Warming up... {reading.n_samples}/{MIN_HR_SAMPLES} "
                    f"(latest: {latest})",
                    flush=True,
                )
            continue

        if reading.changed:
            print(
                f"[rolling] >>> STATE CHANGE -> {reading.state.value.upper()}"
                f"  current={reading.current_bpm:.1f}  "
                f"baseline={reading.baseline_bpm:.1f}  "
                f"delta={reading.delta:+.1f} BPM",
                flush=True,
            )
        elif now - last_status >= STATUS_INTERVAL_S:
            last_status = now
            print(
                f"[rolling] n={reading.n_samples:2d}  "
                f"current={reading.current_bpm:.1f}  "
                f"baseline={reading.baseline_bpm:.1f}  "
                f"delta={reading.delta:+.1f} BPM  "
                f"state={reading.state.value.upper()}",
                flush=True,
            )


def _run_calibrated(store: DataStore, calibration_samples: int) -> None:
    detector = CalibratedStressDetector(
        CalibratedStressDetectorConfig(
            calibration_samples=calibration_samples,
            delta_thresh_bpm=HR_DELTA_THRESH_BPM,
            min_switch_interval_s=MIN_SWITCH_INTERVAL_S,
        )
    )
    print(
        f"\n[calibrated] Collecting {calibration_samples} samples for baseline | "
        f"threshold: +/-{HR_DELTA_THRESH_BPM} BPM | "
        f"cooldown: {MIN_SWITCH_INTERVAL_S}s\n",
        flush=True,
    )

    last_status = time.monotonic()
    baseline_announced = False
    while True:
        time.sleep(POLL_INTERVAL_S)
        now = time.monotonic()
        hr = store.get_hr()
        reading = detector.update(hr, now=now)

        if reading.calibrating:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                print(
                    f"[calibrated] Calibrating... "
                    f"{reading.calibration_progress}/{reading.calibration_target} "
                    f"(latest: {latest})",
                    flush=True,
                )
            continue

        if not baseline_announced:
            baseline_announced = True
            print(
                f"[calibrated] >>> BASELINE LOCKED: {reading.baseline_bpm:.1f} BPM  "
                f"(from {reading.calibration_progress} samples). Detection active.\n",
                flush=True,
            )

        if reading.changed:
            print(
                f"[calibrated] >>> STATE CHANGE -> {reading.state.value.upper()}"
                f"  current={reading.current_bpm:.1f}  "
                f"baseline={reading.baseline_bpm:.1f} (fixed)  "
                f"delta={reading.delta:+.1f} BPM",
                flush=True,
            )
        elif now - last_status >= STATUS_INTERVAL_S:
            last_status = now
            print(
                f"[calibrated] current={reading.current_bpm:.1f}  "
                f"baseline={reading.baseline_bpm:.1f} (fixed)  "
                f"delta={reading.delta:+.1f} BPM  "
                f"state={reading.state.value.upper()}",
                flush=True,
            )


def _slope_driving_mode(state: StressState) -> str:
    return "DEFENSIVE" if state == StressState.ELEVATED else "AGGRESSIVE"


def _run_slope(
    store: DataStore,
    slope_window: float,
    slope_thresh: float,
    defensive_hold: float,
) -> None:
    detector = SlopeStressDetector(
        SlopeStressDetectorConfig(
            slope_window_s=slope_window,
            slope_thresh_bpm_per_s=slope_thresh,
            min_slope_samples=MIN_SLOPE_SAMPLES,
            defensive_hold_s=defensive_hold,
        )
    )
    print(
        f"\n[slope] Warmup: {MIN_SLOPE_SAMPLES} samples | "
        f"threshold: +{slope_thresh} BPM/s | "
        f"window: {slope_window}s | "
        f"defensive hold: {defensive_hold}s | "
        f"default mode: AGGRESSIVE\n",
        flush=True,
    )

    last_status = time.monotonic()
    while True:
        time.sleep(POLL_INTERVAL_S)
        now = time.monotonic()
        hr = store.get_hr()
        reading = detector.update(hr, now=now)

        if reading.warmup:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                print(
                    f"[slope] Warming up... {reading.n_samples}/{MIN_SLOPE_SAMPLES} "
                    f"(latest: {latest})",
                    flush=True,
                )
            continue

        mode = _slope_driving_mode(reading.state)
        if reading.changed:
            print(
                f"[slope] >>> MODE CHANGE -> {mode}"
                f"  (state={reading.state.value.upper()}  "
                f"current={reading.current_bpm:.1f}  "
                f"slope={reading.slope:+.4f} BPM/s  "
                f"n={reading.n_samples})",
                flush=True,
            )
        elif now - last_status >= STATUS_INTERVAL_S:
            last_status = now
            print(
                f"[slope] n={reading.n_samples:2d}  "
                f"current={reading.current_bpm:.1f}  "
                f"slope={reading.slope:+.4f} BPM/s  "
                f"mode={mode}",
                flush=True,
            )


def _run_eda(store: DataStore, scr_min_peaks: int, defensive_hold: float) -> None:
    detector = EdaScrStressDetector(
        EdaScrStressDetectorConfig(
            min_peaks_for_stress=scr_min_peaks,
            defensive_hold_s=defensive_hold,
        )
    )
    print(
        f"\n[eda] Warmup: {MIN_SCR_SAMPLES} window(s) | "
        f"threshold: >={scr_min_peaks} scr_no_peaks | "
        f"defensive hold: {defensive_hold}s | "
        f"default mode: AGGRESSIVE\n",
        flush=True,
    )

    last_status = time.monotonic()
    while True:
        time.sleep(POLL_INTERVAL_S)
        now = time.monotonic()
        feat = store.get_scr_features()
        if feat is None:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                print("[eda] waiting for SCR-Bridge samples...", flush=True)
            continue

        scr_peaks, scr_no_peaks = feat
        reading = detector.update(
            int(round(scr_no_peaks)),
            scr_peaks=scr_peaks,
            now=now,
        )

        if reading.warmup:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                print(
                    f"[eda] Warming up... {reading.n_updates}/{MIN_SCR_SAMPLES}",
                    flush=True,
                )
            continue

        mode = "DEFENSIVE" if reading.state == StressState.ELEVATED else "AGGRESSIVE"
        amp = f"{reading.scr_peaks:.4f}" if reading.scr_peaks is not None else "n/a"
        if reading.changed:
            print(
                f"[eda] >>> MODE CHANGE -> {mode}"
                f"  scr_no_peaks={reading.scr_no_peaks}  scr_peaks={amp}",
                flush=True,
            )
        elif now - last_status >= STATUS_INTERVAL_S:
            last_status = now
            print(
                f"[eda] scr_no_peaks={reading.scr_no_peaks}  scr_peaks={amp}  mode={mode}",
                flush=True,
            )


def _run_combined(
    store: DataStore,
    slope_window: float,
    slope_thresh: float,
    scr_min_peaks: int,
    combine_policy: str,
    defensive_hold: float,
) -> None:
    detector = CombinedStressDetector(
        CombinedStressDetectorConfig(
            slope_window_s=slope_window,
            slope_thresh_bpm_per_s=slope_thresh,
            min_peaks_for_stress=scr_min_peaks,
            combine_policy=combine_policy,  # type: ignore[arg-type]
            defensive_hold_s=defensive_hold,
        )
    )
    print(
        f"\n[combined] policy={combine_policy.upper()} | "
        f"HR slope>={slope_thresh} BPM/s (window {slope_window}s) | "
        f"SCR>={scr_min_peaks} peaks | "
        f"defensive hold: {defensive_hold}s | "
        f"default mode: AGGRESSIVE\n",
        flush=True,
    )

    last_status = time.monotonic()
    while True:
        time.sleep(POLL_INTERVAL_S)
        now = time.monotonic()
        hr = store.get_hr()
        feat = store.get_scr_features()
        if feat is None:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                print("[combined] waiting for SCR-Bridge samples...", flush=True)
            continue

        scr_peaks, scr_no_peaks = feat
        reading = detector.update(
            hr,
            int(round(scr_no_peaks)),
            scr_peaks=scr_peaks,
            now=now,
        )

        if reading.warmup:
            if now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                print("[combined] Warming up HR + EDA...", flush=True)
            continue

        mode = "DEFENSIVE" if reading.state == StressState.ELEVATED else "AGGRESSIVE"
        slope_s = f"{reading.slope:+.4f}" if reading.slope is not None else "n/a"
        bpm = f"{reading.current_bpm:.1f}" if reading.current_bpm is not None else "n/a"
        if reading.changed:
            print(
                f"[combined] >>> MODE CHANGE -> {mode}"
                f"  hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  "
                f"HR={bpm} slope={slope_s}  scr_no_peaks={reading.scr_no_peaks}",
                flush=True,
            )
        elif now - last_status >= STATUS_INTERVAL_S:
            last_status = now
            print(
                f"[combined] HR={bpm} slope={slope_s}  "
                f"scr_no_peaks={reading.scr_no_peaks}  "
                f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  mode={mode}",
                flush=True,
            )


def main() -> None:
    args = _parse_args()

    if args.baseline_mode == "eda":
        label = "SCR"
    elif args.baseline_mode == "combined":
        label = "HR+SCR"
    else:
        label = "HR"
    print(f"[console] Connecting to LSL ({label}, wait up to {args.lsl_wait}s)...", flush=True)
    store = DataStore()
    try:
        start_receiver(
            store,
            wait_time=args.lsl_wait,
            require_hr=args.baseline_mode != "eda",
            require_scr=args.baseline_mode in ("eda", "combined"),
        )
    except RuntimeError as exc:
        print(
            f"\n[console] ERROR: {exc}\n"
            "  Start emotibit-live/lsl_bridge.py.\n",
            flush=True,
        )
        return

    print(
        f"[console] LSL stream connected. Mode: {args.baseline_mode.upper()}\n"
        "  Ctrl+C to stop.\n",
        flush=True,
    )

    try:
        if args.baseline_mode == "calibrated":
            _run_calibrated(store, args.calibration_samples)
        elif args.baseline_mode == "slope":
            _run_slope(store, args.slope_window, args.slope_thresh, args.defensive_hold)
        elif args.baseline_mode == "eda":
            _run_eda(store, args.scr_min_peaks, args.defensive_hold)
        elif args.baseline_mode == "combined":
            _run_combined(
                store,
                args.slope_window,
                args.slope_thresh,
                args.scr_min_peaks,
                args.combine_policy,
                args.defensive_hold,
            )
        else:
            _run_rolling(store)
    except KeyboardInterrupt:
        print("\n[console] Stopped by user.", flush=True)


if __name__ == "__main__":
    main()
