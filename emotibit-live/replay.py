"""Replay a raw EmotiBit CSV log over UDP for testing stream.py."""

from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path


def expand_line(parts: list[str], streams: set[str]) -> list[str]:
    """Split multi-value PG/EA packets into single-value packets."""
    if len(parts) < 7:
        return [",".join(parts)]

    tag = parts[3]
    if tag not in streams:
        return [",".join(parts)]

    try:
        data_len = int(parts[2])
    except ValueError:
        return [",".join(parts)]

    if data_len <= 1:
        return [",".join(parts)]

    values = parts[6 : 6 + data_len]
    if len(values) < data_len:
        return [",".join(parts)]

    packets = []
    base_packet_num = parts[1]
    for index, value in enumerate(values):
        packet = [
            parts[0],
            f"{base_packet_num}_{index}",
            "1",
            tag,
            parts[4],
            parts[5],
            value,
        ]
        packets.append(",".join(packet))
    return packets


def replay(
    csv_path: Path,
    host: str,
    port: int,
    speed: float,
    max_seconds: float | None,
    expand_streams: set[str],
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (host, port)

    start_ts: int | None = None
    replay_start = time.monotonic()
    sent = 0

    print(f"Replaying {csv_path} -> {host}:{port}")
    if speed <= 0:
        print("Timing: as fast as possible")
    else:
        print(f"Timing: {speed}x real-time")
    if max_seconds is not None:
        print(f"Stopping after {max_seconds}s of recording time")

    with csv_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            ts = int(parts[0])
            if start_ts is None:
                start_ts = ts

            if max_seconds is not None and (ts - start_ts) / 1000.0 > max_seconds:
                break

            if speed > 0:
                target_elapsed = (ts - start_ts) / 1000.0 / speed
                delay = target_elapsed - (time.monotonic() - replay_start)
                if delay > 0:
                    time.sleep(delay)

            for packet in expand_line(parts, expand_streams):
                sock.sendto(packet.encode("utf-8"), target)
                sent += 1

    sock.close()
    elapsed = time.monotonic() - replay_start
    print(f"Done. Sent {sent} UDP packets in {elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay EmotiBit CSV over UDP.")
    parser.add_argument(
        "csv_file",
        type=Path,
        help="Raw EmotiBit CSV (one UDP packet per line)",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=12346)
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier (0 = no delay)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Only replay this many seconds of recording time",
    )
    parser.add_argument(
        "--expand",
        default="PG,EA",
        help="Comma-separated streams to split multi-value packets (use '' to disable)",
    )
    args = parser.parse_args()

    if not args.csv_file.exists():
        raise SystemExit(f"File not found: {args.csv_file}")

    expand_streams = set()
    if args.expand.strip():
        expand_streams = {tag.strip() for tag in args.expand.split(",") if tag.strip()}

    replay(
        csv_path=args.csv_file,
        host=args.host,
        port=args.port,
        speed=args.speed,
        max_seconds=args.max_seconds,
        expand_streams=expand_streams,
    )


if __name__ == "__main__":
    main()
