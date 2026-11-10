"""
calibrated_stress_detector.py
==============================
Variant of hr_stress_detector.StressDetector that adds an explicit
BASELINE-RECORDING phase before any stress detection begins, instead of
computing baseline as a continuously drifting rolling mean.

Motivation
----------
hr_stress_detector.StressDetector computes baseline as the mean of
whatever HR samples happen to be in the last ~30s rolling window (see
lsl_receiver.DataStore's HR_WINDOW). That's convenient with zero setup,
but as shown in practice, "baseline" continuously drifts to follow
whatever the current HR is — a sustained stress episode gets slowly
absorbed into its own baseline after ~30s, so there is no fixed "resting
state" reference recorded before the session began.

This file adds that fixed reference:

  Phase 1 — CALIBRATING: collect a configurable NUMBER OF SAMPLES (not a
  wall-clock duration — deliberately, so replaying/simulating HR data
  faster than real time still calibrates on the same sample count and
  produces the same result). No stress state is reported during this
  phase.

  Phase 2 — ACTIVE: baseline_bpm is frozen to the mean of the samples
  collected during calibration. From then on:

      current_bpm = latest sample (no re-smoothing, same as
                    hr_stress_detector.StressDetector)
      delta       = current_bpm - baseline_bpm      (baseline no longer moves)

      delta >= +delta_thresh_bpm  -> ELEVATED
      delta <= -delta_thresh_bpm  -> LOWERED
      otherwise                    -> hold committed state

  with the same min_switch_interval_s cooldown/hysteresis behavior as
  hr_stress_detector.StressDetector.

Prerequisites (only needed to run this file directly):
    pip install pylsl numpy
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

from stress_detection.hr_stress_detector import MIN_SWITCH_INTERVAL_S, StressState

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------

CALIBRATION_SAMPLES: int = 300
HR_DELTA_THRESH_BPM: float = 8.0


@dataclass(frozen=True)
class CalibratedStressDetectorConfig:
    """Tunable thresholds — override per-instance instead of module globals."""

    calibration_samples: int = CALIBRATION_SAMPLES
    delta_thresh_bpm: float = HR_DELTA_THRESH_BPM
    min_switch_interval_s: float = MIN_SWITCH_INTERVAL_S


@dataclass(frozen=True)
class CalibratedStressReading:
    """Result of a single CalibratedStressDetector.update() call."""

    state: StressState
    changed: bool
    calibrating: bool
    calibration_progress: int
    calibration_target: int
    baseline_bpm: float | None
    current_bpm: float | None
    delta: float | None


class CalibratedStressDetector:
    """
    Two-phase HR -> StressState detector with a fixed, recorded baseline.
    """

    def __init__(self, config: CalibratedStressDetectorConfig | None = None) -> None:
        self.config = config or CalibratedStressDetectorConfig()
        self._calibration_buffer: list[float] = []
        self._prev_snapshot: list[float] | None = None
        self.baseline_bpm: float | None = None
        self.committed_state = StressState.BASELINE
        self.last_transition_mono = 0.0

    @property
    def is_calibrated(self) -> bool:
        return self.baseline_bpm is not None

    def _ingest_new_samples(self, hr_samples: list[float]) -> None:
        """
        Append genuinely new samples (since the last update() call) to the
        calibration buffer using positional diff, not value equality.
        """
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

        self._calibration_buffer.extend(new_items)
        self._prev_snapshot = curr

    def _desired_state(self, delta: float) -> StressState:
        if delta >= self.config.delta_thresh_bpm:
            return StressState.ELEVATED
        if delta <= -self.config.delta_thresh_bpm:
            return StressState.LOWERED
        return self.committed_state

    def _can_transition(self, now: float) -> bool:
        return (now - self.last_transition_mono) >= self.config.min_switch_interval_s

    def update(self, hr_samples: list[float], now: float | None = None) -> CalibratedStressReading:
        now = time.monotonic() if now is None else now

        if not self.is_calibrated:
            self._ingest_new_samples(hr_samples)
            progress = len(self._calibration_buffer)

            if progress < self.config.calibration_samples:
                return CalibratedStressReading(
                    state=StressState.BASELINE,
                    changed=False,
                    calibrating=True,
                    calibration_progress=progress,
                    calibration_target=self.config.calibration_samples,
                    baseline_bpm=None,
                    current_bpm=hr_samples[-1] if hr_samples else None,
                    delta=None,
                )

            self.baseline_bpm = float(np.mean(self._calibration_buffer))

        current_bpm = float(hr_samples[-1]) if hr_samples else None
        if current_bpm is None:
            return CalibratedStressReading(
                state=self.committed_state,
                changed=False,
                calibrating=False,
                calibration_progress=len(self._calibration_buffer),
                calibration_target=self.config.calibration_samples,
                baseline_bpm=self.baseline_bpm,
                current_bpm=None,
                delta=None,
            )

        delta = current_bpm - self.baseline_bpm
        target_state = self._desired_state(delta)

        changed = False
        if target_state != self.committed_state and self._can_transition(now):
            self.committed_state = target_state
            self.last_transition_mono = now
            changed = True

        return CalibratedStressReading(
            state=self.committed_state,
            changed=changed,
            calibrating=False,
            calibration_progress=len(self._calibration_buffer),
            calibration_target=self.config.calibration_samples,
            baseline_bpm=self.baseline_bpm,
            current_bpm=current_bpm,
            delta=delta,
        )


def main() -> None:
    from stress_detection.lsl_receiver import DataStore, start_receiver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calibration-samples",
        type=int,
        default=CALIBRATION_SAMPLES,
        help=(
            "Number of HR samples to record before freezing the baseline "
            f"(default: {CALIBRATION_SAMPLES})."
        ),
    )
    parser.add_argument("--delta-thresh-bpm", type=float, default=HR_DELTA_THRESH_BPM)
    parser.add_argument("--min-switch-interval-s", type=float, default=MIN_SWITCH_INTERVAL_S)
    args = parser.parse_args()

    status_interval_s = 2.0
    poll_interval_s = 0.2

    store = DataStore()
    try:
        start_receiver(store, wait_time=5.0)
    except RuntimeError as exc:
        print(
            f"\n[stress] ERROR: {exc}\n"
            "  Make sure your LSL publisher is running and the stream name\n"
            "  contains 'HR' or 'HEART RATE' (checked case-insensitively).\n",
            flush=True,
        )
        return

    config = CalibratedStressDetectorConfig(
        calibration_samples=args.calibration_samples,
        delta_thresh_bpm=args.delta_thresh_bpm,
        min_switch_interval_s=args.min_switch_interval_s,
    )
    detector = CalibratedStressDetector(config)
    print(
        f"[stress] Receiver ready. Calibrating over {config.calibration_samples} samples | "
        f"threshold: +/-{config.delta_thresh_bpm} BPM | cooldown: {config.min_switch_interval_s}s",
        flush=True,
    )

    last_status_mono = time.monotonic()
    was_calibrating = True
    try:
        while True:
            time.sleep(poll_interval_s)
            now = time.monotonic()
            hr = store.get_hr()
            reading = detector.update(hr, now=now)

            if reading.calibrating:
                if now - last_status_mono >= status_interval_s:
                    last_status_mono = now
                    latest = f"{hr[-1]:.1f} BPM" if hr else "no samples yet"
                    print(
                        f"[stress] Calibrating baseline... "
                        f"{reading.calibration_progress}/{reading.calibration_target} samples "
                        f"(latest: {latest})",
                        flush=True,
                    )
                continue

            if was_calibrating:
                was_calibrating = False
                print(
                    f"[stress] >>> BASELINE RECORDED: {reading.baseline_bpm:.1f} BPM "
                    f"(from {reading.calibration_progress} samples). Detection is now active.",
                    flush=True,
                )

            if reading.changed:
                print(
                    f"[stress] >>> STATE CHANGE -> {reading.state.value.upper()}  "
                    f"(current={reading.current_bpm:.1f}  baseline={reading.baseline_bpm:.1f}  "
                    f"delta={reading.delta:+.1f})",
                    flush=True,
                )
            elif now - last_status_mono >= status_interval_s:
                last_status_mono = now
                if reading.current_bpm is not None:
                    print(
                        f"[stress] current={reading.current_bpm:.1f}  "
                        f"baseline={reading.baseline_bpm:.1f} (fixed)  "
                        f"delta={reading.delta:+.1f}  state={reading.state.value.upper()}",
                        flush=True,
                    )
    except KeyboardInterrupt:
        print("\n[stress] Stopped by user.")


if __name__ == "__main__":
    main()
