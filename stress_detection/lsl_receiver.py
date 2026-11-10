"""
lsl_receiver.py
===============
LSL stream discovery, ingestion, and thread-safe storage.

No signal-processing or NeuroKit2 dependency — purely responsible for
getting samples off the wire and into DataStore.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HR_WINDOW: int = 30    # rolling HR sample buffer (~30 s at 1 Hz)
EDA_WINDOW: int = 500  # rolling EDA sample buffer (~33 s at 15 Hz)
EDA_SAMPLE_RATE: int = 15  # approximate Hz of the EDA stream

# Must match emotibit-live/lsl_bridge.py SCR_STREAM_NAME (substring match).
SCR_BRIDGE_NAME_TOKEN: str = "SCR-BRIDGE"


# ---------------------------------------------------------------------------
# Thread-safe data store
# ---------------------------------------------------------------------------

class DataStore:
    """Rolling buffers for HR (BPM), raw EDA, and windowed SCR features."""

    def __init__(self) -> None:
        self.hr: deque[float] = deque(maxlen=HR_WINDOW)
        self.eda: deque[float] = deque(maxlen=EDA_WINDOW)
        self._scr_features: tuple[float, float] | None = None  # scr_peaks, scr_no_peaks
        self._lock = threading.Lock()
        # Optional hook: called with (new_bpm, current_buffer_snapshot) on
        # every HR sample, before the value is appended.  Runs on the receiver
        # thread — keep it fast (print only, no heavy computation).
        self._hr_artifact_hook: "Callable[[float, list[float]], None] | None" = None

    def set_hr_artifact_hook(
        self, hook: "Callable[[float, list[float]], None] | None"
    ) -> None:
        """Register (or clear) a per-sample artifact check callback."""
        self._hr_artifact_hook = hook

    def add_hr(self, value: float) -> None:
        with self._lock:
            if self._hr_artifact_hook is not None:
                self._hr_artifact_hook(value, list(self.hr))
            self.hr.append(value)

    def add_eda(self, value: float) -> None:
        with self._lock:
            self.eda.append(value)

    def get_hr(self) -> list[float]:
        with self._lock:
            return list(self.hr)

    def get_eda(self) -> list[float]:
        with self._lock:
            return list(self.eda)

    def set_scr_features(self, scr_peaks: float, scr_no_peaks: float) -> None:
        with self._lock:
            self._scr_features = (float(scr_peaks), float(scr_no_peaks))

    def get_scr_features(self) -> tuple[float, float] | None:
        with self._lock:
            return self._scr_features


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_lsl_api_cfg() -> None:
    """Point liblsl at the bundled lsl_api.cfg if not already set."""
    if os.environ.get("LSLAPICFG"):
        return
    package_root = Path(__file__).resolve().parent.parent
    for cfg in (package_root / "lsl_api.cfg", Path(__file__).resolve().parent / "lsl_api.cfg"):
        if cfg.is_file():
            os.environ["LSLAPICFG"] = str(cfg)
            break


def _metric_route(stream_name: str) -> str | None:
    """
    Map a stream name to 'HR' or 'EA' (EDA), or None to skip.

    IBI streams are skipped here because we derive IBI from HR BPM ourselves,
    applying our own artifact filtering before any HRV computation.
    """
    n = stream_name.upper()
    if "EDA" in n or "GSR" in n:
        return "EA"
    if n == "EA" or " EA" in n or n.endswith(" EA") or ":EA:" in n:
        return "EA"
    if "IBI" in n:
        return None
    if "HR" in n:
        return "HR"
    if "HEART" in n and "RATE" in n:
        return "HR"
    return None


def _is_scr_feature_stream(stream_name: str) -> bool:
    """Windowed SCR metrics from emotibit-live lsl_bridge (2 channels)."""
    return SCR_BRIDGE_NAME_TOKEN in stream_name.upper()


def _scalar_from_sample(sample) -> float | None:
    """Extract the first numeric channel from whatever pylsl hands back."""
    if sample is None:
        return None
    try:
        if isinstance(sample, np.ndarray):
            return float(sample.reshape(-1)[0]) if sample.size >= 1 else None
        if isinstance(sample, (list, tuple)):
            return float(sample[0]) if len(sample) >= 1 else None
        return float(sample)
    except (TypeError, ValueError):
        return None


def _pair_from_sample(sample) -> tuple[float, float] | None:
    if sample is None:
        return None
    try:
        if isinstance(sample, np.ndarray):
            flat = sample.reshape(-1)
            if flat.size < 2:
                return None
            return float(flat[0]), float(flat[1])
        if isinstance(sample, (list, tuple)) and len(sample) >= 2:
            return float(sample[0]), float(sample[1])
    except (TypeError, ValueError):
        return None
    return None


def _receiver_loop(inlets_push: list[tuple]) -> None:
    """
    Single-thread poll across all inlets.

    Running every inlet on a single thread avoids the silent-zero-sample bug
    that appears when multiple pylsl inlets each run on their own thread on
    Windows.

    Each entry is (inlet, push, kind) with kind ``"scalar"`` or ``"scr"``.
    """
    while True:
        got_any = False
        for inlet, push, kind in inlets_push:
            sample, _ts = inlet.pull_sample(timeout=0.0)
            if kind == "scr":
                pair = _pair_from_sample(sample)
                if pair is not None:
                    push(pair)
                    got_any = True
                continue
            val = _scalar_from_sample(sample)
            if val is not None:
                push(val)
                got_any = True
        if not got_any:
            time.sleep(0.001)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_receiver(
    store: DataStore,
    *,
    wait_time: float = 2.0,
    require_hr: bool = True,
    require_scr: bool = False,
) -> None:
    """
    Resolve LSL streams, open inlets, and start the background polling thread.

    By default attaches HR and raw EDA streams. Set ``require_scr=True`` for
    windowed SCR features (SCR-Bridge from emotibit-live ``lsl_bridge.py``).
    When ``require_hr=False``, HR streams are still attached if present.

    Raises RuntimeError if required streams are missing or nothing matched.
    """
    _apply_lsl_api_cfg()

    try:
        from pylsl import StreamInlet, resolve_streams
    except ImportError as exc:
        print("Failed to import pylsl. pip install pylsl (+ native liblsl).", exc, file=sys.stderr)
        raise SystemExit(1) from exc

    print("Resolving LSL streams...")
    streams = resolve_streams(wait_time=wait_time)

    if not streams:
        raise RuntimeError(
            "No LSL streams found. "
            "Start a publisher (e.g. EmotiBit Oscilloscope with LSL enabled)."
        )

    inlets_push: list[tuple] = []
    has_hr = False
    has_scr = False

    def _push_scr(pair: tuple[float, float]) -> None:
        store.set_scr_features(pair[0], pair[1])

    for info in streams:
        name = info.name()

        if _is_scr_feature_stream(name):
            inlet = StreamInlet(info, max_buflen=360, max_chunklen=0)
            print(f"  SCR stream: {name}")
            inlets_push.append((inlet, _push_scr, "scr"))
            has_scr = True
            continue

        kind = _metric_route(name)

        if kind == "HR":
            inlet = StreamInlet(info, max_buflen=360, max_chunklen=0)
            print(f"  HR  stream: {name}")
            inlets_push.append((inlet, store.add_hr, "scalar"))
            has_hr = True

        elif kind == "EA":
            inlet = StreamInlet(info, max_buflen=360, max_chunklen=0)
            print(f"  EDA stream: {name}")
            inlets_push.append((inlet, store.add_eda, "scalar"))

        else:
            print(f"  skip: {name}")

    if not inlets_push:
        names = [info.name() for info in streams]
        raise RuntimeError(
            "No HR, EDA (EA/GSR), or SCR-Bridge streams matched filters. "
            f"Resolved {len(streams)} stream(s): {names}"
        )
    if require_hr and not has_hr:
        raise RuntimeError(
            "No HR stream found (name must contain 'HR' or 'HEART RATE'). "
            "Start emotibit-live lsl_bridge.py or pass require_hr=False for SCR-only."
        )
    if require_scr and not has_scr:
        raise RuntimeError(
            f"No SCR feature stream found (name must contain '{SCR_BRIDGE_NAME_TOKEN}'). "
            "Start emotibit-live lsl_bridge.py."
        )

    threading.Thread(target=_receiver_loop, args=(inlets_push,), daemon=True).start()
