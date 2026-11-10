"""EmotiBit unix timestamp logic ported from EmotiBitDataParser."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

EMOTIBIT_START_UNSET = 2**31 - 1

HEADER_LEN = 6

LOCAL_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d+$")


@dataclass
class TimestampData:
    rd: int = 0
    ts_sent: str = ""
    ts_received: int = 0
    ak: int = 0
    round_trip: int = -1


@dataclass
class TimeSyncMap:
    emotibit_start: int = 0
    emotibit_end: int = 0
    e0: float = 0.0
    e1: float = 1.0
    c0: float = 0.0
    c1: float = 1.0
    timesync_count: int = 0
    recording_timezone: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.timesync_count > 0 and self.e1 != self.e0


def linterp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y0
    return y0 + (x - x0) * ((y1 - y0) / (x1 - x0))


def parse_local_timestamp(ts_string: str, recording_tz: str | None = None) -> float:
    """Convert EmotiBit TL strings to unix seconds.

    TL strings are wall-clock times from the recording PC. When ``recording_tz``
    is set, parse in that IANA timezone (e.g. America/Chicago for Texas).
    Otherwise fall back to this machine's local timezone (mktime).
    """
    last_dash = ts_string.rfind("-")
    frac_digits = len(ts_string) - last_dash - 1
    base = datetime.strptime(ts_string[:last_dash], "%Y-%m-%d_%H-%M-%S")
    frac = int(ts_string[last_dash + 1 :]) / (10**frac_digits)
    if recording_tz:
        aware = base.replace(tzinfo=ZoneInfo(recording_tz))
        return aware.timestamp() + frac
    return time.mktime(base.timetuple()) + frac


def _quartile_boundaries(n: int) -> tuple[int, int, int]:
    """Match C++ ceil(size / k) where / is integer division."""
    return n // 4, n // 2, (n * 3) // 4


def looks_like_local_timestamp(value: str) -> bool:
    return bool(LOCAL_TIMESTAMP_RE.match(value))


def _get_shortest_rt_index(entries: list[tuple[int, int]]) -> int:
    return min(entries, key=lambda item: item[0])[1]


def get_best_timestamp_indexes(
    timestamp_data: list[TimestampData],
) -> tuple[TimestampData, TimestampData]:
    n = len(timestamp_data)
    if n == 0:
        raise ValueError("No timestamp data")
    if n == 1:
        return timestamp_data[0], timestamp_data[0]

    q2, q3, q4 = _quartile_boundaries(n)

    q1 = [(timestamp_data[i].round_trip, i) for i in range(0, q2)]
    q2v = [(timestamp_data[i].round_trip, i) for i in range(q2, q3)]
    q3v = [(timestamp_data[i].round_trip, i) for i in range(q3, q4)]
    q4v = [(timestamp_data[i].round_trip, i) for i in range(q4, n)]

    pairs = [
        (q1, q4v),
        (q1, q3v),
        (q2v, q4v),
        (q2v, q3v),
        (q1, q2v),
        (q3v, q4v),
    ]
    for left, right in pairs:
        if left and right:
            return (
                timestamp_data[_get_shortest_rt_index(left)],
                timestamp_data[_get_shortest_rt_index(right)],
            )

    for bucket in (q2v, q3v, q1, q4v):
        if len(bucket) > 1:
            bucket = sorted(bucket)
            return timestamp_data[bucket[0][1]], timestamp_data[bucket[1][1]]

    return timestamp_data[0], timestamp_data[n - 1]


def _computer_time_from_sync(entry: TimestampData, recording_tz: str | None = None) -> float:
    c = parse_local_timestamp(entry.ts_sent, recording_tz)
    return c + entry.round_trip / 2.0 / 1000.0


def calculate_time_sync_map(
    timestamp_data: list[TimestampData],
    emotibit_start: int,
    emotibit_end: int,
    recording_tz: str | None = None,
) -> TimeSyncMap:
    valid = [entry for entry in timestamp_data if entry.round_trip >= 0]
    ts_map = TimeSyncMap(
        emotibit_start=emotibit_start,
        emotibit_end=emotibit_end,
        timesync_count=len(valid),
        recording_timezone=recording_tz,
    )

    if not valid:
        ts_map.e0 = float(emotibit_start)
        ts_map.e1 = float(emotibit_end)
        ts_map.c1 = (ts_map.e1 - ts_map.e0) / 1000.0
        return ts_map

    if len(valid) == 1:
        entry = valid[0]
        e_x = float(entry.ts_received)
        c_x = _computer_time_from_sync(entry, recording_tz)

        if e_x - emotibit_start >= emotibit_end - e_x:
            ts_map.e1, ts_map.c1 = e_x, c_x
            ts_map.e0 = float(emotibit_start)
            ts_map.c0 = ts_map.c1 - (ts_map.e1 - ts_map.e0) / 1000.0
        else:
            ts_map.e0, ts_map.c0 = e_x, c_x
            ts_map.e1 = float(emotibit_end)
            ts_map.c1 = ts_map.c0 + (ts_map.e1 - ts_map.e0) / 1000.0
        return ts_map

    first, second = get_best_timestamp_indexes(valid)
    ts_map.e0 = float(first.ts_received)
    ts_map.c0 = _computer_time_from_sync(first, recording_tz)
    ts_map.e1 = float(second.ts_received)
    ts_map.c1 = _computer_time_from_sync(second, recording_tz)
    return ts_map


@dataclass
class TimesyncTracker:
    """Incrementally collect RD/TL/AK packets and maintain a sync map."""

    all_ts: list[TimestampData] = field(default_factory=list)
    last_rd_packet_number: int = -1
    emotibit_start: int = EMOTIBIT_START_UNSET
    emotibit_end: int = 0
    recording_timezone: str | None = None
    ts_map: TimeSyncMap = field(default_factory=TimeSyncMap)

    def ingest_line(self, line: str) -> TimeSyncMap:
        parts = line.strip().split(",")
        if len(parts) < HEADER_LEN:
            return self.ts_map

        emotibit_ts = int(parts[0])
        packet_number = int(parts[1])
        type_tag = parts[3]

        if emotibit_ts < self.emotibit_start:
            self.emotibit_start = emotibit_ts
        if emotibit_ts > self.emotibit_end:
            self.emotibit_end = emotibit_ts

        if type_tag == "RD":
            for payload in parts[HEADER_LEN:]:
                if payload == "TL":
                    if self.all_ts and self.all_ts[-1].round_trip == -1:
                        self.all_ts.pop()
                    self.all_ts.append(TimestampData(rd=emotibit_ts))
                    self.last_rd_packet_number = packet_number
                    break

        elif self.last_rd_packet_number > -1 and type_tag == "TL":
            if self.all_ts:
                self.all_ts[-1].ts_received = emotibit_ts
                if len(parts) > HEADER_LEN:
                    self.all_ts[-1].ts_sent = parts[HEADER_LEN]

        elif self.last_rd_packet_number > -1 and type_tag == "AK":
            if len(parts) > HEADER_LEN and int(parts[HEADER_LEN]) == self.last_rd_packet_number:
                if self.all_ts and self.all_ts[-1].ts_received != 0:
                    self.all_ts[-1].ak = emotibit_ts
                    self.all_ts[-1].round_trip = self.all_ts[-1].ts_received - self.all_ts[-1].rd
                    self.last_rd_packet_number = -1
            elif self.all_ts:
                self.all_ts[-1].ts_received = 0
                self.all_ts[-1].ts_sent = ""

        self.ts_map = calculate_time_sync_map(
            self.all_ts,
            self.emotibit_start,
            self.emotibit_end,
            self.recording_timezone,
        )
        return self.ts_map

    def ingest_packet_bytes(self, raw_bytes: bytes) -> TimeSyncMap:
        return self.ingest_line(raw_bytes.decode("utf-8"))


def parse_timesyncs_from_lines(
    lines: Iterable[str],
    recording_tz: str | None = None,
) -> tuple[list[TimestampData], int, int]:
    tracker = TimesyncTracker(recording_timezone=recording_tz)
    for line in lines:
        tracker.ingest_line(line)
    return tracker.all_ts, tracker.emotibit_start, tracker.emotibit_end


def build_time_sync_map_from_csv(
    csv_path: Path,
    recording_tz: str | None = None,
) -> TimeSyncMap:
    with csv_path.open(encoding="utf-8") as handle:
        all_ts, start, end = parse_timesyncs_from_lines(handle, recording_tz)
    return calculate_time_sync_map(all_ts, start, end, recording_tz)


def load_recording_timezone(
    config_path: Path | None = None,
    override: str | None = None,
) -> str | None:
    if override:
        return override
    path = config_path or Path(__file__).parent / "config.yaml"
    if not path.exists():
        return None
    import yaml

    with path.open(encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    tz = (cfg.get("timesync") or {}).get("recording_timezone")
    return str(tz) if tz else None


def emotibit_ms_to_unix(emotibit_ms: float, ts_map: TimeSyncMap) -> float:
    return linterp(emotibit_ms, ts_map.e0, ts_map.e1, ts_map.c0, ts_map.c1)


def interp_packet_emotibit_ms(
    sample_index: int,
    data_length: int,
    prev_packet_ts: float,
    packet_ts: float,
) -> float:
    """Match DataParser per-sample emotibit interpolation in sensor payloads."""
    return linterp(sample_index + 1, 0, data_length, prev_packet_ts, packet_ts)


def unix_for_sensor_sample(
    emotibit_ms: float,
    ts_map: TimeSyncMap,
) -> float | None:
    if not ts_map.is_ready:
        return None
    return emotibit_ms_to_unix(emotibit_ms, ts_map)


def format_map_summary(ts_map: TimeSyncMap) -> str:
    c0_local = datetime.fromtimestamp(ts_map.c0)
    c1_local = datetime.fromtimestamp(ts_map.c1)
    tz_note = ts_map.recording_timezone or "this machine's local timezone"
    return (
        f"timesyncs={ts_map.timesync_count}, "
        f"recording_tz={tz_note}, "
        f"e0={ts_map.e0:.0f}, e1={ts_map.e1:.0f}, "
        f"c0={ts_map.c0:.6f} ({c0_local}), "
        f"c1={ts_map.c1:.6f} ({c1_local})"
    )


def unix_from_packet(parts: list[str], ts_map: TimeSyncMap) -> float | None:
    if not ts_map.is_ready:
        return None

    stream = parts[3]
    emotibit_ms = float(parts[0])

    if stream == "UN" and len(parts) >= 7 and looks_like_local_timestamp(parts[6]):
        return parse_local_timestamp(parts[6], ts_map.recording_timezone)

    if stream == "TL" and len(parts) >= 7 and looks_like_local_timestamp(parts[6]):
        return parse_local_timestamp(parts[6], ts_map.recording_timezone)

    return emotibit_ms_to_unix(emotibit_ms, ts_map)


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Inspect EmotiBit timesync map for a raw CSV.")
    cli.add_argument("csv_file", type=Path)
    cli.add_argument("--emotibit-ms", type=float, default=None, help="Convert one EmotiBit ms value")
    cli.add_argument(
        "--recording-tz",
        default=None,
        help="IANA timezone where data was recorded (e.g. America/Chicago). "
        "Defaults to config.yaml timesync.recording_timezone.",
    )
    args = cli.parse_args()

    recording_tz = load_recording_timezone(override=args.recording_tz)
    ts_map = build_time_sync_map_from_csv(args.csv_file, recording_tz)
    print(format_map_summary(ts_map))
    print(f"emotibit range: {ts_map.emotibit_start} .. {ts_map.emotibit_end}")
    if args.emotibit_ms is not None:
        unix = emotibit_ms_to_unix(args.emotibit_ms, ts_map)
        print(f"unix @ {args.emotibit_ms}: {unix:.6f} ({datetime.fromtimestamp(unix)})")
