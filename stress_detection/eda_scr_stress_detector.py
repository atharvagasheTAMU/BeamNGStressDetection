"""
eda_scr_stress_detector.py
==========================
Windowed EDA stress detector using SCR peak count from emotibit-live features.

Each update consumes the latest ``scr no peaks`` value (integer count of validated
SCR peaks in the processing window). When the count meets or exceeds a configurable
threshold, the detector enters ELEVATED (same StressState as HR detectors).

Motivation
----------
Sudden sympathetic arousal often produces multiple skin conductance responses within
a short window. Counting peaks in the same 20 s window used by ``lsl_bridge.py``
is a simple, interpretable stress proxy complementary to HR slope.

Policy (mirrors slope_stress_detector hold behaviour):
    scr_no_peaks >= min_peaks_for_stress  ->  ELEVATED (defensive in driving demos)
    otherwise (while not in hold)           ->  BASELINE (aggressive)

Once ELEVATED, state is held for ``defensive_hold_s`` then auto-releases to BASELINE.

Standalone usage:
    python eda_scr_stress_detector.py

Prerequisites:
    pip install pylsl
    emotibit-live ``lsl_bridge.py`` running (publishes SCR-Bridge).
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from stress_detection.hr_stress_detector import StressState

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------

SCR_MIN_PEAKS_FOR_STRESS: int = 3       # scr no peaks >= this -> stressed
MIN_SCR_SAMPLES: int = 1                # windowed updates before first decision
DEFENSIVE_HOLD_S: float = 20.0          # stay elevated this long, then auto-release
SCR_PEAKS_INVALID: float = -1.0         # scr peaks amplitude sentinel when no SCR


@dataclass(frozen=True)
class EdaScrStressDetectorConfig:
    """Tunable thresholds. Override per-instance instead of module globals."""

    min_peaks_for_stress: int = SCR_MIN_PEAKS_FOR_STRESS
    min_scr_samples: int = MIN_SCR_SAMPLES
    defensive_hold_s: float = DEFENSIVE_HOLD_S


@dataclass(frozen=True)
class EdaScrStressReading:
    """Result of a single EdaScrStressDetector.update() call."""

    state: StressState
    changed: bool
    warmup: bool
    scr_no_peaks: int | None
    scr_peaks: float | None           # mean peak amplitude; None if invalid
    n_updates: int


class EdaScrStressDetector:
    """
    Maps latest windowed SCR peak counts to StressState with defensive hold.
    """

    def __init__(self, config: EdaScrStressDetectorConfig | None = None) -> None:
        self.config = config or EdaScrStressDetectorConfig()
        self.committed_state = StressState.BASELINE
        self._defensive_until_mono: float | None = None
        self._n_updates = 0

    def _desired_state(self, scr_no_peaks: int) -> StressState:
        if scr_no_peaks >= self.config.min_peaks_for_stress:
            return StressState.ELEVATED
        return StressState.BASELINE

    def update(
        self,
        scr_no_peaks: int,
        scr_peaks: float | None = None,
        now: float | None = None,
    ) -> EdaScrStressReading:
        now = time.monotonic() if now is None else now
        self._n_updates += 1

        peaks_amp: float | None
        if scr_peaks is None or scr_peaks <= SCR_PEAKS_INVALID:
            peaks_amp = None
        else:
            peaks_amp = float(scr_peaks)

        if self._n_updates < self.config.min_scr_samples:
            return EdaScrStressReading(
                state=self.committed_state,
                changed=False,
                warmup=True,
                scr_no_peaks=scr_no_peaks,
                scr_peaks=peaks_amp,
                n_updates=self._n_updates,
            )

        changed = False

        if self.committed_state == StressState.ELEVATED:
            if self._defensive_until_mono is not None and now >= self._defensive_until_mono:
                self.committed_state = StressState.BASELINE
                self._defensive_until_mono = None
                changed = True
        elif self._desired_state(scr_no_peaks) == StressState.ELEVATED:
            self.committed_state = StressState.ELEVATED
            self._defensive_until_mono = now + self.config.defensive_hold_s
            changed = True

        return EdaScrStressReading(
            state=self.committed_state,
            changed=changed,
            warmup=False,
            scr_no_peaks=scr_no_peaks,
            scr_peaks=peaks_amp,
            n_updates=self._n_updates,
        )


def main() -> None:
    from stress_detection.lsl_receiver import DataStore, start_receiver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scr-min-peaks",
        type=int,
        default=SCR_MIN_PEAKS_FOR_STRESS,
        help=f"SCR count threshold (default: {SCR_MIN_PEAKS_FOR_STRESS}).",
    )
    parser.add_argument(
        "--defensive-hold",
        type=float,
        default=DEFENSIVE_HOLD_S,
        help=f"Seconds to hold elevated before release (default: {DEFENSIVE_HOLD_S}).",
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
        start_receiver(store, wait_time=args.lsl_wait, require_hr=False, require_scr=True)
    except RuntimeError as exc:
        print(f"\n[eda] ERROR: {exc}\n", flush=True)
        return

    detector = EdaScrStressDetector(
        EdaScrStressDetectorConfig(
            min_peaks_for_stress=args.scr_min_peaks,
            defensive_hold_s=args.defensive_hold,
        )
    )
    print(
        f"[eda] SCR receiver ready. threshold: >={args.scr_min_peaks} peaks | "
        f"hold: {args.defensive_hold}s\n",
        flush=True,
    )

    status_interval = 2.0
    last_status = time.monotonic()
    try:
        while True:
            time.sleep(0.2)
            now = time.monotonic()
            feat = store.get_scr_features()
            if feat is None:
                if now - last_status >= status_interval:
                    last_status = now
                    print("[eda] waiting for SCR-Bridge samples...", flush=True)
                continue

            scr_peaks, scr_no_peaks = feat
            no_peaks = int(round(scr_no_peaks))
            reading = detector.update(no_peaks, scr_peaks=scr_peaks, now=now)

            if reading.warmup:
                if now - last_status >= status_interval:
                    last_status = now
                    print(
                        f"[eda] warming up... {reading.n_updates}/{MIN_SCR_SAMPLES}",
                        flush=True,
                    )
                continue

            mode = "DEFENSIVE" if reading.state == StressState.ELEVATED else "AGGRESSIVE"
            if reading.changed:
                print(
                    f"[eda] >>> MODE -> {mode}  scr_no_peaks={reading.scr_no_peaks}  "
                    f"scr_peaks={reading.scr_peaks}",
                    flush=True,
                )
            elif now - last_status >= status_interval:
                last_status = now
                amp = f"{reading.scr_peaks:.4f}" if reading.scr_peaks is not None else "n/a"
                print(
                    f"[eda] scr_no_peaks={reading.scr_no_peaks}  scr_peaks={amp}  "
                    f"mode={mode}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\n[eda] Stopped.", flush=True)


if __name__ == "__main__":
    main()
