"""Run folder naming and discovery helpers."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

RUN_FOLDER_PATTERN = re.compile(r"^\d{4}_\d{2}_\d{2}_(?:.+_)?(\d+)$")


def sanitize_run_tag(tag: str) -> str:
    tag = tag.strip()
    if not tag:
        return ""
    tag = re.sub(r"[^\w\-]", "_", tag)
    return re.sub(r"_+", "_", tag).strip("_")


def build_run_folder_name(unix_ts: int, tag: str = "") -> str:
    date_part = datetime.fromtimestamp(unix_ts).strftime("%Y_%m_%d")
    tag = sanitize_run_tag(tag)
    if tag:
        return f"{date_part}_{tag}_{unix_ts}"
    return f"{date_part}_{unix_ts}"


def parse_run_unix_ts(folder_name: str) -> int | None:
    if folder_name.isdigit():
        return int(folder_name)
    match = RUN_FOLDER_PATTERN.match(folder_name)
    if match:
        return int(match.group(1))
    return None


def find_latest_run(data_dir: Path) -> Path | None:
    runs: list[tuple[int, Path]] = []
    for path in data_dir.iterdir():
        if not path.is_dir():
            continue
        unix_ts = parse_run_unix_ts(path.name)
        if unix_ts is not None:
            runs.append((unix_ts, path))
    if not runs:
        return None
    runs.sort(key=lambda item: item[0], reverse=True)
    return runs[0][1]
