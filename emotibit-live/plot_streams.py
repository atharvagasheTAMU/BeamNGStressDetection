"""Plot HR and BI from raw stream CSVs for a recorded run."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from run_utils import find_latest_run

CONFIG_PATH = Path(__file__).parent / "config.yaml"

STREAMS = {
    "HR": {"ylabel": "Heart Rate (BPM)", "title": "Heart Rate (HR)"},
    "BI": {"ylabel": "Beat Interval (ms)", "title": "Beat Interval (BI)"},
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def plot_raw_streams(raw_dir: Path, run_dir: Path, streams: dict[str, dict]) -> None:
    for tag, labels in streams.items():
        path = raw_dir / f"stream_{tag}.csv"
        if not path.exists():
            print(f"No raw data for {tag}, skipping plot.")
            continue

        df = pd.read_csv(path)
        if df.empty:
            print(f"{tag} CSV is empty, skipping plot.")
            continue

        t0 = df["emotibit time"].iloc[0]
        time_sec = (df["emotibit time"] - t0) / 1000.0

        plt.figure(figsize=(10, 4))
        plt.plot(time_sec, df["value"], linewidth=1.5)
        plt.xlabel("Time (s)")
        plt.ylabel(labels["ylabel"])
        plt.title(labels["title"])
        plt.grid(True, alpha=0.3)

        out_path = run_dir / f"{tag}.png"
        plt.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Saved plot: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot HR/BI from raw EmotiBit stream CSVs.")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run folder, e.g. data/2026_07_15_1783020361 (defaults to latest under data/)",
    )
    parser.add_argument(
        "--streams",
        default="HR,BI",
        help="Comma-separated stream tags to plot (default: HR,BI)",
    )
    args = parser.parse_args()

    cfg = load_config()
    data_dir = Path(cfg["streaming"].get("data_dir", "data"))

    if args.run_dir:
        run_dir = args.run_dir
    else:
        run_dir = find_latest_run(data_dir)
        if run_dir is None:
            raise SystemExit(f"No run folders found in {data_dir}. Pass --run-dir.")

    raw_dir = run_dir / "raw"
    if not raw_dir.exists():
        raise SystemExit(f"Raw folder not found: {raw_dir}")

    tags = [tag.strip() for tag in args.streams.split(",") if tag.strip()]
    streams = {tag: STREAMS[tag] for tag in tags if tag in STREAMS}
    unknown = [tag for tag in tags if tag not in STREAMS]
    for tag in unknown:
        streams[tag] = {"ylabel": tag, "title": tag}

    print(f"Plotting from: {run_dir}")
    plot_raw_streams(raw_dir, run_dir, streams)


if __name__ == "__main__":
    main()
