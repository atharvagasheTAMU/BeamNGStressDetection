"""Offline feature extraction from saved EmotiBit CSV streams."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
import yaml
from sklearn.preprocessing import StandardScaler

from run_utils import find_latest_run
from signal_utils import FEATURE_COLUMNS, extract_window_features, filter_BVP, filter_EDA

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_stream_csv(path: Path, is_e4: bool = False) -> pd.DataFrame:
    df = pd.read_csv(path)
    if is_e4:
        df.columns = ["time", "value"]
        df["reliability"] = 100
        df["time"] = pd.to_datetime(df["time"])
        min_datetime = df["time"].min()
        df["time"] = (df["time"] - min_datetime).dt.total_seconds() * 1000
        df["emotibit time"] = df["time"]
    return df


def prepare_streams(ppg, eda, min_reliability, edge_buffer_sec):
    ppg = ppg[ppg["reliability"] >= min_reliability].drop(columns=["reliability"]).copy()
    eda = eda[eda["reliability"] >= min_reliability].drop(columns=["reliability"]).copy()
    ppg.rename(columns={"emotibit time": "time"}, inplace=True)
    eda.rename(columns={"emotibit time": "time"}, inplace=True)
    ppg["time"] = ppg["time"] / 1000
    eda["time"] = eda["time"] / 1000

    start_time = math.ceil(max(ppg.iloc[0, 0], eda.iloc[0, 0]))
    end_time = math.floor(min(ppg.iloc[-1, 0], eda.iloc[-1, 0]))
    total_time = end_time - start_time - edge_buffer_sec * 2
    offset = start_time + edge_buffer_sec

    session_start_unix = None
    if "unix time" in ppg.columns:
        aligned = ppg[ppg["time"] >= offset]
        if not aligned.empty:
            session_start_unix = float(aligned["unix time"].iloc[0])
        ppg = ppg.drop(columns=["unix time"])
    if "unix time" in eda.columns:
        eda = eda.drop(columns=["unix time"])

    ppg["time"] = ppg["time"] - offset
    eda["time"] = eda["time"] - offset
    ppg = ppg[(ppg["time"] > 0) & (ppg["time"] < total_time)].copy()
    eda = eda[(eda["time"] > 0) & (eda["time"] < total_time)].copy()

    scaler = StandardScaler()
    ppg["value"] = scaler.fit_transform(ppg["value"].to_numpy().reshape(-1, 1)).reshape(-1)
    eda["value"] = scaler.fit_transform(eda["value"].to_numpy().reshape(-1, 1)).reshape(-1)

    ppg_sr = len(ppg) / total_time
    eda_sr = len(eda) / total_time

    ppg_filtered = ppg.copy()
    ppg_filtered["value"] = filter_BVP(ppg["value"].to_numpy(), ppg_sr)
    scr = eda.copy()
    scl = eda.copy()
    scr["value"], scl["value"] = filter_EDA(eda["value"].to_numpy(), eda_sr)
    return ppg_filtered, scr, scl, total_time, session_start_unix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None, help="Run folder, e.g. data/2026_07_15_1783020361")
    parser.add_argument("--ppg-csv", type=Path, default=None)
    parser.add_argument("--eda-csv", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config()
    stream_cfg = cfg["streaming"]
    proc_cfg = cfg["processing"]
    offline_cfg = cfg["offline"]
    data_dir = Path(stream_cfg.get("data_dir", "data"))

    if args.run_dir:
        run_dir = args.run_dir
    elif args.ppg_csv or args.eda_csv or offline_cfg.get("ppg_csv") or offline_cfg.get("eda_csv"):
        run_dir = None
    else:
        run_dir = find_latest_run(data_dir)
        if run_dir is None:
            raise SystemExit(f"No run folders found in {data_dir}. Pass --run-dir or --ppg-csv/--eda-csv.")

    if run_dir:
        print(f"Using run folder: {run_dir}")
        raw_dir = run_dir / "raw"
        ppg_path = args.ppg_csv or Path(offline_cfg["ppg_csv"] or raw_dir / f"stream_{stream_cfg['ppg_stream']}.csv")
        eda_path = args.eda_csv or Path(offline_cfg["eda_csv"] or raw_dir / f"stream_{stream_cfg['eda_stream']}.csv")
        output_path = args.output_csv or Path(
            offline_cfg.get("output_csv") or run_dir / "processed" / "processed_data.csv"
        )
    else:
        ppg_path = args.ppg_csv or Path(offline_cfg["ppg_csv"])
        eda_path = args.eda_csv or Path(offline_cfg["eda_csv"])
        output_path = args.output_csv or Path(offline_cfg.get("output_csv", "processed_data.csv"))

    ppg = load_stream_csv(ppg_path, is_e4=offline_cfg.get("is_e4", False))
    eda = load_stream_csv(eda_path, is_e4=offline_cfg.get("is_e4", False))
    ppg_filtered, scr, scl, total_time, session_start_unix = prepare_streams(
        ppg, eda, proc_cfg["min_reliability"], proc_cfg["edge_buffer_sec"]
    )

    window_size = proc_cfg["window_size_sec"]
    stride = proc_cfg["stride_sec"]
    windows = [(t, t + window_size) for t in range(0, total_time - window_size + 1, stride)]

    rows = []
    for index, (start, end) in enumerate(windows):
        features = extract_window_features(
            ppg_filtered,
            window_start=start,
            window_end=end,
            window_size_sec=window_size,
            visualize=False,
            # visualize=index % proc_cfg.get("visualize_every_n_windows", 60) == 0,
            eda_scr_df=scr,
            eda_scl_df=scl,
            prefiltered=True,
        )
        if features is None:
            continue
        features["time"] = end
        if session_start_unix is not None:
            features["unix time"] = session_start_unix + end
        rows.append(features)
        print(f"Window {index + 1}/{len(windows)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=FEATURE_COLUMNS).to_csv(output_path, index=False)
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
