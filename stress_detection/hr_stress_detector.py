"""
hr_stress_detector.py
======================
Pure HR -> stress-state detection, decoupled from LSL wiring and from
BeamNG entirely.

This module owns exactly one concern: given a rolling buffer of already
-clean HR (BPM) samples (e.g. sourced from an LSL "HR" stream via
lsl_receiver.py, or any other transport), decide whether the driver is
currently ELEVATED (HR meaningfully above its own rolling baseline),
LOWERED (meaningfully below), or holding at BASELINE — with hysteresis
(a delta threshold) and debounce (a minimum time between transitions) so
the signal doesn't flicker.

What this file deliberately does NOT do
----------------------------------------
- No artifact filtering / outlier rejection / re-smoothing of HR values.
  That responsibility belongs upstream (e.g. emotibit-live's 20-second
  windowed feature extraction, or whatever produced the clean HR feed).
  `StressDetector` trusts its input and uses the latest sample as-is —
  it does not take a median/mean of recent samples to "clean" it, since
  that would just be re-doing a job that's already been done once.
- No LSL connection management as part of its *core* logic — the
  `StressDetector` class takes plain `list[float]` HR samples via
  `update()`. It has no idea where they came from, so it stays testable
  with synthetic data and reusable with any transport. (The `main()`
  below *does* wire up LSL, purely so this file is directly runnable for
  standalone testing — see "Standalone usage" below.)
- No BeamNG / vehicle / track / driving-mode knowledge whatsoever. The
  output is `StressState.ELEVATED` / `LOWERED` / `BASELINE` — a
  domain-agnostic signal. Mapping that to "aggressive"/"defensive" (or
  "sine"/"straight", or anything else) is a consumer's job, not this
  file's.

Standalone usage (no BeamNG needed)
------------------------------------
    python hr_stress_detector.py

    Connects to an LSL HR stream (same discovery as lsl_hr_listener.py)
    and prints every stress-state transition to the console, so you can
    validate detection behavior before wiring it into any demo.

Library usage (e.g. from a BeamNG demo)
-----------------------------------------
    from hr_stress_detector import StressDetector, StressState

    detector = StressDetector()
    ...
    reading = detector.update(store.get_hr())
    if reading.changed:
        ...  # map reading.state to whatever the consumer cares about

Prerequisites (only needed to run this file directly):
    pip install pylsl numpy
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

import numpy as np

# ---------------------------------------------------------------------------
# Tunable detection parameters (defaults mirrored in StressDetectorConfig)
# ---------------------------------------------------------------------------

HR_DELTA_THRESH_BPM: float = 8.0    # BPM above/below baseline to trigger a transition
MIN_HR_SAMPLES: int = 8             # warmup — don't judge state until we have this many samples
MIN_SWITCH_INTERVAL_S: float = 6.0  # cooldown between consecutive state transitions


class StressState(Enum):
    """Domain-agnostic HR-derived stress signal. No driving-mode concepts here."""

    ELEVATED = "elevated"   # current HR meaningfully above rolling baseline
    LOWERED = "lowered"     # current HR meaningfully below rolling baseline
    BASELINE = "baseline"   # initial/default — no transition has fired yet


@dataclass(frozen=True)
class StressDetectorConfig:
    """Tunable thresholds. Override per-instance instead of module globals."""

    delta_thresh_bpm: float = HR_DELTA_THRESH_BPM
    min_hr_samples: int = MIN_HR_SAMPLES
    min_switch_interval_s: float = MIN_SWITCH_INTERVAL_S


@dataclass(frozen=True)
class StressReading:
    """Result of a single StressDetector.update() call."""

    state: StressState
    changed: bool                  # True only on the update where a transition just committed
    warmup: bool                   # True while below min_hr_samples — metrics are None
    baseline_bpm: float | None
    current_bpm: float | None
    delta: float | None
    n_samples: int


class StressDetector:
    """
    Maps a rolling HR (BPM) buffer to a hysteresis-debounced StressState.

    Policy (mirrors mode_switch_hr_demo.py's HrModePolicy, generalized and
    with the redundant re-smoothing step removed — see module docstring):

        baseline_bpm = mean of all buffered samples
        current_bpm  = latest sample (no re-smoothing; input is already clean)
        delta        = current_bpm - baseline_bpm

        delta >= +delta_thresh_bpm  -> ELEVATED
        delta <= -delta_thresh_bpm  -> LOWERED
        otherwise                    -> hold committed state (including
                                         BASELINE, if no transition has
                                         fired yet)

    A min_switch_interval_s cooldown prevents rapid toggling. Once a
    transition away from BASELINE has fired, the detector — like the
    policy it's generalized from — only toggles between ELEVATED and
    LOWERED; it does not automatically return to BASELINE.
    """

    def __init__(
        self,
        config: StressDetectorConfig | None = None,
        initial_state: StressState = StressState.BASELINE,
    ) -> None:
        self.config = config or StressDetectorConfig()
        self.committed_state = initial_state
        self.last_transition_mono = 0.0

    @staticmethod
    def compute_metrics(hr: list[float]) -> tuple[float, float, float]:
        baseline_bpm = float(np.mean(hr))
        current_bpm = float(hr[-1])
        delta = current_bpm - baseline_bpm
        return baseline_bpm, current_bpm, delta

    def _desired_state(self, delta: float) -> StressState:
        if delta >= self.config.delta_thresh_bpm:
            return StressState.ELEVATED
        if delta <= -self.config.delta_thresh_bpm:
            return StressState.LOWERED
        return self.committed_state  # hold — within neutral band

    def _can_transition(self, now: float) -> bool:
        return (now - self.last_transition_mono) >= self.config.min_switch_interval_s

    def update(self, hr_samples: list[float], now: float | None = None) -> StressReading:
        """
        Feed the latest rolling HR buffer and get back the current stress
        reading. Call this on every poll tick with the full buffer (not
        just new samples) — mirrors how DataStore.get_hr() is used today.
        """
        now = time.monotonic() if now is None else now
        n_samples = len(hr_samples)

        if n_samples < self.config.min_hr_samples:
            return StressReading(
                state=self.committed_state,
                changed=False,
                warmup=True,
                baseline_bpm=None,
                current_bpm=None,
                delta=None,
                n_samples=n_samples,
            )

        baseline_bpm, current_bpm, delta = self.compute_metrics(hr_samples)
        target_state = self._desired_state(delta)

        changed = False
        if target_state != self.committed_state and self._can_transition(now):
            self.committed_state = target_state
            self.last_transition_mono = now
            changed = True

        return StressReading(
            state=self.committed_state,
            changed=changed,
            warmup=False,
            baseline_bpm=baseline_bpm,
            current_bpm=current_bpm,
            delta=delta,
            n_samples=n_samples,
        )


# ---------------------------------------------------------------------------
# Standalone usage — LSL wiring lives ONLY here, never inside StressDetector.
# ---------------------------------------------------------------------------

def main() -> None:
    from stress_detection.lsl_receiver import DataStore, start_receiver

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

    detector = StressDetector()
    print(
        f"[stress] Receiver ready. Warmup: {MIN_HR_SAMPLES} samples | "
        f"threshold: +/-{HR_DELTA_THRESH_BPM} BPM | cooldown: {MIN_SWITCH_INTERVAL_S}s",
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
                        f"[stress] Warming up... {reading.n_samples}/{MIN_HR_SAMPLES} samples "
                        f"(latest: {latest})",
                        flush=True,
                    )
                continue

            if reading.changed:
                print(
                    f"[stress] >>> STATE CHANGE -> {reading.state.value.upper()}  "
                    f"(current={reading.current_bpm:.1f}  baseline={reading.baseline_bpm:.1f}  "
                    f"delta={reading.delta:+.1f})",
                    flush=True,
                )
            elif now - last_status_mono >= status_interval_s:
                last_status_mono = now
                print(
                    f"[stress] n={reading.n_samples:2d}  current={reading.current_bpm:.1f}  "
                    f"baseline={reading.baseline_bpm:.1f}  delta={reading.delta:+.1f}  "
                    f"state={reading.state.value.upper()}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\n[stress] Stopped by user.")


if __name__ == "__main__":
    main()
