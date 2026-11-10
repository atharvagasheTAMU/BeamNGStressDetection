"""
eye_lsl_receiver.py
===================
LSL ingestion for EyeMetrics-Bridge and EyeEvent-Bridge (eye-tracker-live).

No stress inference — stores the latest metrics row and a rolling event buffer
for a future eye-based detector or fusion with HR/EDA.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

# Must match eye-tracker-live/lsl_bridge.py
METRICS_STREAM_NAME = "EyeMetrics-Bridge"
EVENTS_STREAM_NAME = "EyeEvent-Bridge"


class EyeDataStore:
    """Latest eye metrics JSON row plus recent discrete events."""

    def __init__(self, event_maxlen: int = 200) -> None:
        self._metrics: dict | None = None
        self._metrics_ts: float | None = None
        self._events: deque[dict] = deque(maxlen=event_maxlen)
        self._lock = threading.Lock()

    def set_metrics(self, row: dict, *, lsl_ts: float | None = None) -> None:
        with self._lock:
            self._metrics = row
            self._metrics_ts = lsl_ts

    def add_event(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    def get_metrics(self) -> dict | None:
        with self._lock:
            return None if self._metrics is None else dict(self._metrics)

    def get_metrics_timestamp(self) -> float | None:
        with self._lock:
            return self._metrics_ts

    def drain_events(self) -> list[dict]:
        """Return and clear queued events (for console / detector hooks)."""
        with self._lock:
            out = list(self._events)
            self._events.clear()
            return out

    def peek_events(self) -> list[dict]:
        with self._lock:
            return list(self._events)


def _apply_lsl_api_cfg() -> None:
    if os.environ.get("LSLAPICFG"):
        return
    package_root = Path(__file__).resolve().parent.parent
    for cfg in (package_root / "lsl_api.cfg", Path(__file__).resolve().parent / "lsl_api.cfg"):
        if cfg.is_file():
            os.environ["LSLAPICFG"] = str(cfg)
            break


def _norm_stream_name(name: str) -> str:
    return name.upper().replace("_", "").replace("-", "").replace(" ", "")


def _match_metrics_stream(name: str) -> bool:
    return _norm_stream_name(name) == _norm_stream_name(METRICS_STREAM_NAME)


def _match_events_stream(name: str) -> bool:
    return _norm_stream_name(name) == _norm_stream_name(EVENTS_STREAM_NAME)


def _json_from_sample(sample) -> dict | None:
    if sample is None:
        return None
    try:
        if isinstance(sample, (list, tuple)) and sample:
            raw = sample[0]
        else:
            raw = sample
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _receiver_loop(inlets: list) -> None:
    """Poll metrics + event inlets on one thread (Windows-friendly)."""
    while True:
        got_any = False
        for inlet, kind in inlets:
            sample, ts = inlet.pull_sample(timeout=0.0)
            row = _json_from_sample(sample)
            if row is None:
                continue
            got_any = True
            store, push = kind
            if push == "metrics":
                store.set_metrics(row, lsl_ts=ts)
            else:
                store.add_event(row)
        if not got_any:
            time.sleep(0.001)


def start_eye_receiver(
    store: EyeDataStore,
    *,
    wait_time: float = 5.0,
    require_metrics: bool = True,
    require_events: bool = False,
) -> None:
    """
    Resolve eye LSL streams and start the background polling thread.

    Raises RuntimeError if required streams are missing.
    """
    _apply_lsl_api_cfg()

    try:
        from pylsl import StreamInlet, resolve_streams
    except ImportError as exc:
        print("Failed to import pylsl. pip install pylsl (+ native liblsl).", file=sys.stderr)
        raise SystemExit(1) from exc

    print("Resolving eye LSL streams...")
    streams = resolve_streams(wait_time=wait_time)

    if not streams:
        raise RuntimeError(
            "No LSL streams found. Start eye-tracker-live/lsl_bridge.py first."
        )

    inlets: list = []
    has_metrics = False
    has_events = False

    for info in streams:
        name = info.name()
        if _match_metrics_stream(name):
            inlet = StreamInlet(info, max_buflen=360, max_chunklen=0)
            print(f"  Eye metrics: {name}")
            inlets.append((inlet, (store, "metrics")))
            has_metrics = True
            continue
        if _match_events_stream(name):
            inlet = StreamInlet(info, max_buflen=360, max_chunklen=0)
            print(f"  Eye events:  {name}")
            inlets.append((inlet, (store, "event")))
            has_events = True
            continue

    if not inlets:
        names = [info.name() for info in streams]
        raise RuntimeError(
            "No EyeMetrics-Bridge or EyeEvent-Bridge streams matched. "
            f"Resolved {len(streams)} stream(s): {names}"
        )
    if require_metrics and not has_metrics:
        raise RuntimeError(
            "No EyeMetrics-Bridge stream found. Start eye-tracker-live/lsl_bridge.py."
        )
    if require_events and not has_events:
        raise RuntimeError(
            "No EyeEvent-Bridge stream found. Start eye-tracker-live/lsl_bridge.py."
        )

    threading.Thread(target=_receiver_loop, args=(inlets,), daemon=True).start()
