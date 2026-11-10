"""
combined_stress_detector.py
===========================
Fuse HR slope stress with EDA SCR peak-count stress into one defensive trigger.

Uses existing single-modality detectors for metrics only; fusion policy (OR/AND)
and a single defensive hold live here so HR-only and EDA-only modules stay unchanged.

Default policy (OR):
    HR slope >= threshold  OR  scr_no_peaks >= threshold  ->  ELEVATED (defensive)

Driving demos should map ELEVATED -> defensive (same as slope / EDA modes).

Standalone usage:
    python combined_stress_detector.py
    python combined_stress_detector.py --combine-policy and --scr-min-peaks 3

Prerequisites:
    pip install pylsl numpy
    emotibit-live ``lsl_bridge.py`` (HR-Bridge + SCR-Bridge).
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Literal

from stress_detection.eda_scr_stress_detector import (
    SCR_MIN_PEAKS_FOR_STRESS,
    EdaScrStressDetector,
    EdaScrStressDetectorConfig,
)
from stress_detection.hr_stress_detector import StressState
from stress_detection.slope_stress_detector import (
    MIN_SLOPE_SAMPLES,
    SLOPE_THRESH_BPM_PER_S,
    SLOPE_WINDOW_S,
    SlopeStressDetector,
    SlopeStressDetectorConfig,
)

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------

COMBINE_POLICY: Literal["or", "and"] = "or"
DEFENSIVE_HOLD_S: float = 20.0

# Sub-detector hold is unused; fusion owns hold timing.
_SUB_HOLD_S: float = 1.0e9


@dataclass(frozen=True)
class CombinedStressDetectorConfig:
    """Tunable thresholds for fused HR (slope) + EDA (SCR count) detection."""

    slope_window_s: float = SLOPE_WINDOW_S
    slope_thresh_bpm_per_s: float = SLOPE_THRESH_BPM_PER_S
    min_slope_samples: int = MIN_SLOPE_SAMPLES
    min_peaks_for_stress: int = SCR_MIN_PEAKS_FOR_STRESS
    min_scr_samples: int = 1
    combine_policy: Literal["or", "and"] = COMBINE_POLICY
    defensive_hold_s: float = DEFENSIVE_HOLD_S


@dataclass(frozen=True)
class CombinedStressReading:
    """Result of a single CombinedStressDetector.update() call."""

    state: StressState
    changed: bool
    warmup: bool
    current_bpm: float | None
    slope: float | None
    scr_no_peaks: int | None
    scr_peaks: float | None
    hr_cue: bool
    eda_cue: bool
    fused_cue: bool


class CombinedStressDetector:
    """
    HR slope + SCR peak count fusion with one shared defensive hold timer.
    """

    def __init__(self, config: CombinedStressDetectorConfig | None = None) -> None:
        self.config = config or CombinedStressDetectorConfig()
        self._slope = SlopeStressDetector(
            SlopeStressDetectorConfig(
                slope_window_s=self.config.slope_window_s,
                slope_thresh_bpm_per_s=self.config.slope_thresh_bpm_per_s,
                min_slope_samples=self.config.min_slope_samples,
                defensive_hold_s=_SUB_HOLD_S,
            )
        )
        self._eda = EdaScrStressDetector(
            EdaScrStressDetectorConfig(
                min_peaks_for_stress=self.config.min_peaks_for_stress,
                min_scr_samples=self.config.min_scr_samples,
                defensive_hold_s=_SUB_HOLD_S,
            )
        )
        self.committed_state = StressState.BASELINE
        self._defensive_until_mono: float | None = None

    def _fuse(self, hr_cue: bool, eda_cue: bool) -> bool:
        if self.config.combine_policy == "and":
            return hr_cue and eda_cue
        return hr_cue or eda_cue

    def update(
        self,
        hr_samples: list[float],
        scr_no_peaks: int,
        scr_peaks: float | None = None,
        now: float | None = None,
    ) -> CombinedStressReading:
        now = time.monotonic() if now is None else now

        slope_r = self._slope.update(hr_samples, now=now)
        eda_r = self._eda.update(scr_no_peaks, scr_peaks=scr_peaks, now=now)

        warmup = slope_r.warmup or eda_r.warmup

        hr_cue = (
            not slope_r.warmup
            and slope_r.slope is not None
            and slope_r.slope >= self.config.slope_thresh_bpm_per_s
        )
        eda_cue = (
            not eda_r.warmup
            and scr_no_peaks >= self.config.min_peaks_for_stress
        )
        fused_cue = self._fuse(hr_cue, eda_cue)

        if warmup:
            return CombinedStressReading(
                state=self.committed_state,
                changed=False,
                warmup=True,
                current_bpm=slope_r.current_bpm,
                slope=slope_r.slope,
                scr_no_peaks=scr_no_peaks,
                scr_peaks=eda_r.scr_peaks,
                hr_cue=hr_cue,
                eda_cue=eda_cue,
                fused_cue=fused_cue,
            )

        changed = False

        if self.committed_state == StressState.ELEVATED:
            if self._defensive_until_mono is not None and now >= self._defensive_until_mono:
                self.committed_state = StressState.BASELINE
                self._defensive_until_mono = None
                changed = True
        elif fused_cue:
            self.committed_state = StressState.ELEVATED
            self._defensive_until_mono = now + self.config.defensive_hold_s
            changed = True

        return CombinedStressReading(
            state=self.committed_state,
            changed=changed,
            warmup=False,
            current_bpm=slope_r.current_bpm,
            slope=slope_r.slope,
            scr_no_peaks=scr_no_peaks,
            scr_peaks=eda_r.scr_peaks,
            hr_cue=hr_cue,
            eda_cue=eda_cue,
            fused_cue=fused_cue,
        )


def main() -> None:
    from stress_detection.lsl_receiver import DataStore, start_receiver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combine-policy",
        choices=("or", "and"),
        default=COMBINE_POLICY,
        help=f"Fuse HR and EDA cues (default: {COMBINE_POLICY}).",
    )
    parser.add_argument(
        "--slope-window",
        type=float,
        default=SLOPE_WINDOW_S,
        help=f"HR slope regression window in seconds (default: {SLOPE_WINDOW_S}).",
    )
    parser.add_argument(
        "--slope-thresh",
        type=float,
        default=SLOPE_THRESH_BPM_PER_S,
        help=f"HR slope threshold in BPM/s (default: {SLOPE_THRESH_BPM_PER_S}).",
    )
    parser.add_argument(
        "--scr-min-peaks",
        type=int,
        default=SCR_MIN_PEAKS_FOR_STRESS,
        help=f"SCR peak count threshold (default: {SCR_MIN_PEAKS_FOR_STRESS}).",
    )
    parser.add_argument(
        "--defensive-hold",
        type=float,
        default=DEFENSIVE_HOLD_S,
        help=f"Seconds in defensive before auto-release (default: {DEFENSIVE_HOLD_S}).",
    )
    parser.add_argument(
        "--lsl-wait",
        type=float,
        default=5.0,
        help="LSL discovery wait time in seconds (default: 5).",
    )
    args = parser.parse_args()

    store = DataStore()
    try:
        start_receiver(store, wait_time=args.lsl_wait, require_hr=True, require_scr=True)
    except RuntimeError as exc:
        print(f"\n[combined] ERROR: {exc}\n", flush=True)
        return

    detector = CombinedStressDetector(
        CombinedStressDetectorConfig(
            slope_window_s=args.slope_window,
            slope_thresh_bpm_per_s=args.slope_thresh,
            min_peaks_for_stress=args.scr_min_peaks,
            combine_policy=args.combine_policy,
            defensive_hold_s=args.defensive_hold,
        )
    )
    print(
        f"[combined] policy={args.combine_policy.upper()} | "
        f"HR slope>={args.slope_thresh} BPM/s | "
        f"SCR>={args.scr_min_peaks} peaks | hold={args.defensive_hold}s\n",
        flush=True,
    )

    status_interval = 2.0
    last_status = time.monotonic()
    try:
        while True:
            time.sleep(0.2)
            now = time.monotonic()
            hr = store.get_hr()
            feat = store.get_scr_features()
            if feat is None:
                if now - last_status >= status_interval:
                    last_status = now
                    print("[combined] waiting for SCR-Bridge...", flush=True)
                continue

            scr_peaks, scr_no_peaks = feat
            reading = detector.update(
                hr,
                int(round(scr_no_peaks)),
                scr_peaks=scr_peaks,
                now=now,
            )

            if reading.warmup:
                if now - last_status >= status_interval:
                    last_status = now
                    print("[combined] warming up HR + EDA...", flush=True)
                continue

            mode = "DEFENSIVE" if reading.state == StressState.ELEVATED else "AGGRESSIVE"
            slope_s = (
                f"{reading.slope:+.4f}" if reading.slope is not None else "n/a"
            )
            amp = f"{reading.scr_peaks:.4f}" if reading.scr_peaks is not None else "n/a"
            if reading.changed:
                print(
                    f"[combined] >>> MODE -> {mode}  "
                    f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  "
                    f"slope={slope_s}  scr_no_peaks={reading.scr_no_peaks}",
                    flush=True,
                )
            elif now - last_status >= status_interval:
                last_status = now
                bpm = (
                    f"{reading.current_bpm:.1f}"
                    if reading.current_bpm is not None
                    else "n/a"
                )
                print(
                    f"[combined] HR={bpm}  slope={slope_s}  "
                    f"scr_no_peaks={reading.scr_no_peaks} scr_peaks={amp}  "
                    f"hr_cue={reading.hr_cue} eda_cue={reading.eda_cue}  mode={mode}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\n[combined] Stopped.", flush=True)


if __name__ == "__main__":
    main()
