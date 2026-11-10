"""
eye_tracker_console.py
======================
Middleware consumer for eye-tracker LSL streams (no stress algorithm yet).

Prints the latest metrics row and streams discrete events to the terminal.

Run (two terminals):
    cd eye-tracker-live && python lsl_bridge.py
    cd scripts && python eye_tracker_console.py

Prerequisites:
    pip install pylsl
    eye-tracker-live/lsl_bridge.py running (camera + SPACE to calibrate for metrics)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from stress_detection.eye_lsl_receiver import EyeDataStore, start_eye_receiver

POLL_INTERVAL_S = 0.2
STATUS_INTERVAL_S = 2.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lsl-wait",
        type=float,
        default=20.0,
        help="LSL discovery wait time in seconds (default: 20).",
    )
    return parser.parse_args()


def _format_metrics(row: dict) -> str:
    parts = [
        f"eye={row.get('Eye_Openness_Pct')}% ({row.get('Eye_State')})",
        f"gaze={row.get('Gaze_Direction')}",
        f"head_yaw={row.get('Head_Yaw_Deg')}",
        f"away={row.get('Is_Looking_Away')}",
    ]
    if row.get("Proximity_Pct") is not None:
        parts.append(f"prox={row.get('Proximity_Pct')}%")
    if row.get("Brow_Furrow_Pct") is not None:
        parts.append(f"furrow={row.get('Brow_Furrow_Pct')}%")
    return "  ".join(str(p) for p in parts)


def main() -> None:
    args = _parse_args()
    store = EyeDataStore()

    print(f"[eye-console] Connecting to LSL (wait up to {args.lsl_wait}s)...", flush=True)
    try:
        start_eye_receiver(store, wait_time=args.lsl_wait, require_metrics=False)
    except RuntimeError as exc:
        print(f"\n[eye-console] ERROR: {exc}\n", flush=True)
        return

    print(
        "[eye-console] Connected. Ctrl+C to stop.\n"
        "  Calibration is in the BRIDGE window (camera preview), not here.\n"
        "  Click the preview, sit normally, press SPACE once. "
        "[metrics] lines appear ~1/s after that.\n"
        "  Events (head angle, movement, Calibration_Set, ...) print as [event].\n",
        flush=True,
    )
    last_status = time.monotonic()
    last_ts = None

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)
            now = time.monotonic()

            for ev in store.drain_events():
                print(
                    f"[event] {ev.get('timestamp')}  {ev.get('event')}: {ev.get('value')}",
                    flush=True,
                )

            row = store.get_metrics()
            if row is None:
                if now - last_status >= STATUS_INTERVAL_S:
                    last_status = now
                    print(
                        "[eye-console] waiting for metrics "
                        "(calibrate bridge with SPACE if preview is open)...",
                        flush=True,
                    )
                continue

            ts_key = row.get("Timestamp")
            if ts_key != last_ts and now - last_status >= STATUS_INTERVAL_S:
                last_status = now
                last_ts = ts_key
                print(f"[metrics] {row.get('Timestamp')}  {_format_metrics(row)}", flush=True)
    except KeyboardInterrupt:
        print("\n[eye-console] Stopped.", flush=True)


if __name__ == "__main__":
    main()
