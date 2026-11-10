"""
slope_stress_detector.py
========================
HR rate-of-change stress detector.

Instead of comparing current HR against a baseline level (like
hr_stress_detector.StressDetector and calibrated_stress_detector.
CalibratedStressDetector do), this detector fits a linear-regression slope
over a recent sliding window of HR samples and triggers ELEVATED when HR is
rising fast enough.

Motivation
----------
Delta-based detectors catch *sustained elevation* — HR that is significantly
above (or below) some reference value.  A slope-based detector catches
*sudden onset* — HR that is rising quickly right now — which is typically
the first observable signal of an acute stressor.

Slope computation
-----------------
On each update() call, genuinely new BPM samples are ingested into a
timestamped internal buffer using the same positional-diff approach as
calibrated_stress_detector._ingest_new_samples — robust to a bounded rolling
deque and to repeated identical values during calm resting periods.

Each new sample is tagged with the caller-supplied `now` (monotonic poll
time) rather than the LSL arrival time.  Within slope_window_s, a
least-squares line is fitted to the (time, bpm) pairs via numpy.polyfit.
The slope coefficient (BPM/s) is the detection signal:

    slope >= +slope_thresh_bpm_per_s  ->  ELEVATED  (rising fast → defensive)
    slope <  slope_thresh_bpm_per_s   ->  BASELINE  (default → aggressive)

Once ELEVATED (defensive) is entered, the state is held for defensive_hold_s
(default 20 s) regardless of subsequent slope readings, then automatically
returns to BASELINE (aggressive).  No separate switch cooldown — the hold
duration is the only gate on mode changes.

Standalone usage (no BeamNG needed)
-------------------------------------
    python slope_stress_detector.py
    python slope_stress_detector.py --slope-window 15 --slope-thresh 1.3

Prerequisites (only needed to run this file directly):
    pip install pylsl numpy
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

from stress_detection.hr_stress_detector import StressState


# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------

SLOPE_WINDOW_S: float = 4.0             # sliding window for regression (seconds)
SLOPE_THRESH_BPM_PER_S: float = 1.3     # BPM/s rise to trigger defensive
MIN_SLOPE_SAMPLES: int = 8              # warmup — need this many timestamped samples
DEFENSIVE_HOLD_S: float = 20.0          # stay defensive this long, then auto-release


@dataclass(frozen=True)
class SlopeStressDetectorConfig:
    """Tunable thresholds. Override per-instance instead of module globals."""

    slope_window_s: float = SLOPE_WINDOW_S
    slope_thresh_bpm_per_s: float = SLOPE_THRESH_BPM_PER_S
    min_slope_samples: int = MIN_SLOPE_SAMPLES
    defensive_hold_s: float = DEFENSIVE_HOLD_S


@dataclass(frozen=True)
class SlopeStressReading:
    """Result of a single SlopeStressDetector.update() call."""

    state: StressState
    changed: bool
    warmup: bool
    current_bpm: float | None
    slope: float | None             # BPM/s; positive = rising
    n_samples: int


class SlopeStressDetector:
    """
    Maps a rolling HR (BPM) buffer to a StressState based on the *rate of
    change* of HR rather than its level vs. a baseline.

    See module docstring for full rationale and computation details.
    """

    def __init__(self, config: SlopeStressDetectorConfig | None = None) -> None:
        self.config = config or SlopeStressDetectorConfig()
        self._timestamped: list[tuple[float, float]] = []
        self._prev_snapshot: list[float] | None = None
        self.committed_state = StressState.BASELINE
        self._defensive_until_mono: float | None = None

    def _ingest_new_samples(self, hr_samples: list[float], now: float) -> None:
        if not hr_samples:
            return

        curr = list(hr_samples)
        prev = self._prev_snapshot

        if prev is None:
            new_items = curr[-1:]
        elif curr == prev:
            new_items = []
        else:
            max_check = min(len(prev), len(curr))
            overlap = 0
            for j in range(max_check, -1, -1):
                if j == 0 or prev[-j:] == curr[:j]:
                    overlap = j
                    break
            new_items = curr[overlap:]

        for value in new_items:
            self._timestamped.append((now, value))
        self._prev_snapshot = curr

    def _prune_window(self, now: float) -> None:
        cutoff = now - self.config.slope_window_s
        self._timestamped = [(t, v) for t, v in self._timestamped if t >= cutoff]

    def _compute_slope(self) -> float | None:
        if len(self._timestamped) < 2:
            return None
        times = np.array([t for t, _ in self._timestamped], dtype=float)
        bpms = np.array([v for _, v in self._timestamped], dtype=float)
        times -= times[0]
        slope, _ = np.polyfit(times, bpms, 1)
        return float(slope)

    def _desired_state(self, slope: float) -> StressState:
        if slope >= self.config.slope_thresh_bpm_per_s:
            return StressState.ELEVATED
        return StressState.BASELINE

    def update(self, hr_samples: list[float], now: float | None = None) -> SlopeStressReading:
        now = time.monotonic() if now is None else now

        self._ingest_new_samples(hr_samples, now)
        self._prune_window(now)

        current_bpm = float(hr_samples[-1]) if hr_samples else None
        n_samples = len(self._timestamped)

        if n_samples < self.config.min_slope_samples:
            return SlopeStressReading(
                state=self.committed_state,
                changed=False,
                warmup=True,
                current_bpm=current_bpm,
                slope=None,
                n_samples=n_samples,
            )

        slope = self._compute_slope()
        if slope is None:
            return SlopeStressReading(
                state=self.committed_state,
                changed=False,
                warmup=True,
                current_bpm=current_bpm,
                slope=None,
                n_samples=n_samples,
            )

        changed = False

        if self.committed_state == StressState.ELEVATED:
            if self._defensive_until_mono is not None and now >= self._defensive_until_mono:
                self.committed_state = StressState.BASELINE
                self._defensive_until_mono = None
                changed = True
        elif self._desired_state(slope) == StressState.ELEVATED:
            self.committed_state = StressState.ELEVATED
            self._defensive_until_mono = now + self.config.defensive_hold_s
            changed = True

        return SlopeStressReading(
            state=self.committed_state,
            changed=changed,
            warmup=False,
            current_bpm=current_bpm,
            slope=slope,
            n_samples=n_samples,
        )


def main() -> None:
    from stress_detection.lsl_receiver import DataStore, start_receiver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slope-window",
        type=float,
        default=SLOPE_WINDOW_S,
        help=f"Regression window in seconds (default: {SLOPE_WINDOW_S}).",
    )
    parser.add_argument(
        "--slope-thresh",
        type=float,
        default=SLOPE_THRESH_BPM_PER_S,
        help=f"Trigger threshold in BPM/s (default: {SLOPE_THRESH_BPM_PER_S}).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=MIN_SLOPE_SAMPLES,
        help=f"Minimum timestamped samples before detection starts (default: {MIN_SLOPE_SAMPLES}).",
    )
    parser.add_argument(
        "--defensive-hold",
        type=float,
        default=DEFENSIVE_HOLD_S,
        help=(
            f"Seconds to stay in defensive before auto-releasing to aggressive "
            f"(default: {DEFENSIVE_HOLD_S})."
        ),
    )
    args = parser.parse_args()

    status_interval_s = 2.0
    poll_interval_s = 0.2

    store = DataStore()
    try:
        start_receiver(store, wait_time=5.0)
    except RuntimeError as exc:
        print(
            f"\n[slope] ERROR: {exc}\n"
            "  Make sure your LSL publisher is running and the stream name\n"
            "  contains 'HR' or 'HEART RATE' (checked case-insensitively).\n",
            flush=True,
        )
        return

    config = SlopeStressDetectorConfig(
        slope_window_s=args.slope_window,
        slope_thresh_bpm_per_s=args.slope_thresh,
        min_slope_samples=args.min_samples,
        defensive_hold_s=args.defensive_hold,
    )
    detector = SlopeStressDetector(config)
    print(
        f"[slope] Receiver ready. "
        f"Window: {config.slope_window_s}s | "
        f"threshold: +{config.slope_thresh_bpm_per_s} BPM/s | "
        f"warmup: {config.min_slope_samples} samples | "
        f"defensive hold: {config.defensive_hold_s}s",
        flush=True,
    )

    last_status_mono = time.monotonic()
    try:
        while True:
            time.sleep(poll_interval_s)
            now = time.monotonic()
            hr = store.get_hr()
            reading = detector.update(hr, now=now)

            if reading.warmup:
                if now - last_status_mono >= status_interval_s:
                    last_status_mono = now
                    latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                    print(
                        f"[slope] Warming up... {reading.n_samples}/{config.min_slope_samples} samples "
                        f"(latest: {latest})",
                        flush=True,
                    )
                continue

            mode = "DEFENSIVE" if reading.state == StressState.ELEVATED else "AGGRESSIVE"
            if reading.changed:
                print(
                    f"[slope] >>> MODE CHANGE -> {mode}"
                    f"  (state={reading.state.value.upper()}  "
                    f"current={reading.current_bpm:.1f}  "
                    f"slope={reading.slope:+.4f} BPM/s  "
                    f"n={reading.n_samples})",
                    flush=True,
                )
            elif now - last_status_mono >= status_interval_s:
                last_status_mono = now
                print(
                    f"[slope] n={reading.n_samples:2d}  "
                    f"current={reading.current_bpm:.1f}  "
                    f"slope={reading.slope:+.4f} BPM/s  "
                    f"mode={mode}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\n[slope] Stopped by user.")


if __name__ == "__main__":
    main()
