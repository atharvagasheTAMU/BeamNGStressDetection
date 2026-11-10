"""EmotiBit UDP streaming with unix timestamps derived from recording timesync data."""

from __future__ import annotations

import argparse
import csv
import signal
import socket
import time
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd
import yaml

from run_utils import build_run_folder_name
from signal_utils import FEATURE_COLUMNS, extract_window_features
from timesync import (
    TimesyncTracker,
    build_time_sync_map_from_csv,
    format_map_summary,
    load_recording_timezone,
    unix_from_packet,
)

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_packet(fields: list[str], ts_map) -> dict:
    stream = fields[3]
    unix_time = unix_from_packet(fields, ts_map)
    sample = {
        "emotibit_time_ms": float(fields[0]),
        "unix_time": unix_time,
        "stream": stream,
        "reliability": float(fields[5]),
    }
    if stream == "UN":
        if len(fields) >= 8:
            sample["note"] = fields[7]
        else:
            sample["note"] = ",".join(fields[6:]).strip()
        sample["value"] = None
    else:
        sample["note"] = None
        try:
            sample["value"] = float(fields[6])
        except (ValueError, IndexError):
            sample["value"] = None
    return sample


def append_csv(path: Path, columns: list[str], row: dict, initialized: set[str], key: str) -> None:
    write_header = key not in initialized and not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    initialized.add(key)


def buffer_to_df(buffer: deque, start: float, end: float) -> pd.DataFrame:
    rows = [{"time": t, "value": v} for t, v in buffer if start <= t <= end]
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream EmotiBit UDP data with unix timestamps from recording timesyncs.",
    )
    parser.add_argument(
        "--timesync-csv",
        type=Path,
        default=None,
        help="Raw EmotiBit CSV used to pre-build the emotibit->unix map (recommended for replay)",
    )
    parser.add_argument(
        "--recording-tz",
        default=None,
        help="IANA timezone where data was recorded (e.g. America/Chicago for Texas). "
        "Defaults to config.yaml timesync.recording_timezone.",
    )
    args = parser.parse_args()

    cfg = load_config()
    stream_cfg = cfg["streaming"]
    proc_cfg = cfg["processing"]
    recording_tz = load_recording_timezone(override=args.recording_tz)

    tracker = TimesyncTracker(recording_timezone=recording_tz)
    map_frozen = False
    if args.timesync_csv:
        if not args.timesync_csv.exists():
            raise SystemExit(f"Timesync CSV not found: {args.timesync_csv}")
        tracker.ts_map = build_time_sync_map_from_csv(args.timesync_csv, recording_tz)
        tracker.emotibit_start = tracker.ts_map.emotibit_start
        tracker.emotibit_end = tracker.ts_map.emotibit_end
        map_frozen = True
        print(f"Loaded timesync map from {args.timesync_csv}")
        print(format_map_summary(tracker.ts_map))
    else:
        print("No --timesync-csv provided; building map from incoming RD/TL/AK packets.")

    data_dir = Path(stream_cfg.get("data_dir", "data"))
    unix_ts = int(tracker.ts_map.c0) if tracker.ts_map.is_ready else int(time.time())
    run_id = build_run_folder_name(unix_ts, stream_cfg.get("run_tag", "") or "")
    run_dir = data_dir / run_id
    raw_dir = run_dir / "raw"
    processed_dir = run_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run folder: {run_dir}")

    skipped = set(stream_cfg["skipped_streams"])
    timesync_streams = {"RD", "TL", "AK"}
    ppg_stream = stream_cfg["ppg_stream"]
    eda_stream = stream_cfg["eda_stream"]
    save_raw = stream_cfg.get("save_raw", True)
    live_processing = proc_cfg.get("live_processing", True)

    buffers: dict[str, deque] = defaultdict(deque)
    csv_initialized: set[str] = set()
    session_start_unix: float | None = None
    last_window_end = -1.0
    window_count = 0
    dropped_without_unix = 0

    live_output = processed_dir / "processed_data_live.csv"
    live_output_initialized = False

    window_size = proc_cfg["window_size_sec"]
    stride = proc_cfg["stride_sec"]
    edge_buffer = proc_cfg["edge_buffer_sec"]
    min_reliability = proc_cfg["min_reliability"]
    warmup = edge_buffer + window_size

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((stream_cfg["udp_host"], stream_cfg["udp_port"]))
    sock.settimeout(1.0)
    print(f"Listening on {stream_cfg['udp_host']}:{stream_cfg['udp_port']}")
    print("Press Ctrl+C to stop.")

    running = True

    def request_stop(_signum, _frame) -> None:
        nonlocal running
        if not running:
            return
        running = False
        print("\nStopping...", flush=True)
        sock.close()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    try:
        while running:
            try:
                binary_data, _ = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break

            fields = binary_data.decode("utf-8").split(",")
            if len(fields) < 6:
                continue

            stream = fields[3]
            if stream in timesync_streams and not map_frozen:
                tracker.ingest_packet_bytes(binary_data)

            sample = parse_packet(fields, tracker.ts_map)

            if sample["stream"] in skipped or sample["stream"] in timesync_streams:
                continue

            if sample["unix_time"] is None:
                dropped_without_unix += 1
                continue

            if sample["stream"] == "UN":
                if save_raw:
                    append_csv(
                        raw_dir / "stream_UN.csv",
                        ["emotibit time", "unix time", "note", "reliability"],
                        {
                            "emotibit time": sample["emotibit_time_ms"],
                            "unix time": sample["unix_time"],
                            "note": sample["note"],
                            "reliability": sample["reliability"],
                        },
                        csv_initialized,
                        "UN",
                    )
                print(f"[UN] {sample['note']}")
                continue

            if sample["value"] is None:
                continue

            if save_raw:
                append_csv(
                    raw_dir / f"stream_{sample['stream']}.csv",
                    ["emotibit time", "unix time", "value", "reliability"],
                    {
                        "emotibit time": sample["emotibit_time_ms"],
                        "unix time": sample["unix_time"],
                        "value": sample["value"],
                        "reliability": sample["reliability"],
                    },
                    csv_initialized,
                    sample["stream"],
                )

            if sample["reliability"] < min_reliability:
                continue
            if sample["stream"] not in (ppg_stream, eda_stream):
                continue

            if session_start_unix is None:
                session_start_unix = sample["unix_time"]
            relative_time = sample["unix_time"] - session_start_unix
            buffers[sample["stream"]].append((relative_time, sample["value"]))

            keep = window_size + edge_buffer + stride + 5
            for buf_stream in list(buffers):
                while buffers[buf_stream] and buffers[buf_stream][0][0] < relative_time - keep:
                    buffers[buf_stream].popleft()

            if not live_processing or relative_time < warmup:
                continue

            while running:
                if last_window_end < 0:
                    window_end = warmup
                else:
                    window_end = last_window_end + stride
                if relative_time < window_end:
                    break

                window_start = window_end - window_size
                ppg_df = buffer_to_df(buffers[ppg_stream], window_start, window_end)
                eda_df = buffer_to_df(buffers[eda_stream], window_start, window_end)

                features = extract_window_features(
                    ppg_df,
                    eda_df,
                    window_start=window_start,
                    window_end=window_end,
                    window_size_sec=window_size,
                    visualize=False,
                )
                if features is not None:
                    features["unix time"] = session_start_unix + features["time"]
                    write_header = not live_output_initialized
                    with live_output.open("a", newline="", encoding="utf-8") as handle:
                        writer = csv.DictWriter(handle, fieldnames=FEATURE_COLUMNS)
                        if write_header:
                            writer.writeheader()
                        writer.writerow(features)
                    live_output_initialized = True
                    print(f"[features @ {features['time']:.1f}s] LF/HF={features['lf/hf']:.3f}")

                last_window_end = window_end
                window_count += 1

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        running = False
        if dropped_without_unix:
            print(f"Dropped {dropped_without_unix} packets before timesync map was ready.")
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
