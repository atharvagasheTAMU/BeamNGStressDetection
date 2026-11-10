# EmotiBit Live — Standard Operating Procedure

This guide explains how to record and process EmotiBit biosignal data using this toolkit. The data can be streamed live or it can be replayed from a recorded session from the oscilloscope using the raw csv file.
**What you'll get at the end of a session:** a folder containing your raw sensor data (CSV files you can open in Excel) and proecssed data containing various metrics such as ibi, hr, etc.

### Project files

| File | Purpose |
|---|---|
| `config.yaml` | All settings |
| `stream_timesync.py` | **Default** live listener, device/recording-aligned timestamps |
| `stream.py` | Simpler alternative live listener, computer-clock timestamps |
| `replay.py` | Replays a raw CSV over the network for testing |
| `process_offline.py` | Recalculates features from saved raw data |
| `plot_streams.py` | Generates HR/BI chart images |
| `signal_utils.py` | Signal-processing math (filters, feature extraction) |
| `run_utils.py` | Recording-folder naming/discovery |
| `timesync.py` | RD/TL/AK timestamp alignment logic |


---

## 1. What this toolkit does

This toolkit does three things:

1. **Records** data from a real EmotiBit device over Wi-Fi (Live mode)
2. **Replays** a previously recorded file to test the setup without needing the device (Replay mode)
3. **Processes** raw recordings afterward into ready-to-analyze feature files 

Everything you need to run is in one folder. You will mostly work with two things:

- A settings file called `config.yaml` 
- A handful of commands you type into a terminal
---

## 2. One-time setup

Do this once per computer.

### Step 1 — Install Python

If you don't already have Python installed, download it from [python.org](https://www.python.org/downloads/) (version 3.10 or newer) and install it.

### Step 2 — Install Git and create a GitHub account

- Download and install Git from [git-scm.com/downloads](https://git-scm.com/downloads) (accept the default options during installation).
- Create a free account at [github.com](https://github.com) if you don't already have one.

You only need this so you can download the project files in the next step and get future updates easily.

### Step 3 — Get the project files

Open a terminal (Command Prompt / PowerShell on Windows, Terminal on Mac) and run:

```bash
git clone https://github.com/atharvagasheTAMU/EmotiBit-Live.git
cd EmotiBit-Live
```

If you'd rather not install Git, you can instead download the project as a ZIP from [github.com/atharvagasheTAMU/EmotiBit-Live](https://github.com/atharvagasheTAMU/EmotiBit-Live.git) (green "Code" button → "Download ZIP"), unzip it, then open a terminal inside the unzipped folder. Note that this way you won't be able to easily pull future updates.

### Step 4 — Install the required packages

Still in that terminal, run:

```bash
pip install -r requirements.txt
```

This installs everything the toolkit needs. You won't need to touch this again unless you're told to.

> **You're done with setup.** For every future session, you only need to open a terminal in this folder and run one command (see Sections 4 and 5).

---

## 3. The settings file (`config.yaml`)

`config.yaml` is a plain text file that controls how recording and processing behave. Open it with any text editor (Notepad, VS Code, etc.). You do **not** need to understand code to edit it — it's just a list of `setting: value` lines.

You will rarely need to change most of these. The ones you're most likely to touch are marked **(commonly changed)**.

### 3.1 Connection settings — `streaming:`

These control how your computer listens for data from the EmotiBit.

| Setting | What it means in plain terms | When to change it |
|---|---|---|
| `udp_host` | The network address your computer listens on. | Leave as `localhost` |
| `udp_port` | The "channel number" data comes in on. | Leave as `12346` — this is EmotiBit's standard channel. Only change if your EmotiBit app/device is set to a different port. |
| `run_tag` **(commonly changed)** | A short label added to your recording folder name, e.g. participant ID or condition. | Set this before each session, e.g. `run_tag: "P01_baseline"`, so your recordings are easy to find later. Leave `""` (empty) if you don't need a label. |
| `save_raw` | Whether to save the raw sensor data to disk. | Leave `true`. Only set to `false` if you specifically don't want any files saved  |
| `data_dir` | The folder where all recordings are stored. | Leave as `data` unless you want recordings saved somewhere else. |
| `ppg_stream` | Which sensor channel is used for the heart-signal (PPG) calculations. | Leave as `PG` unless instructed otherwise. |
| `eda_stream` | Which sensor channel is used for the skin-conductance (EDA) calculations. | Leave as `EA` unless instructed otherwise. |
| `skipped_streams` | A list of sensor channels to ignore completely (not saved, not used). | Leave as-is unless you want to exclude specific channels (e.g. to save disk space). |

### 3.2 Processing settings — `processing:`

These control how raw data is turned into calculated features (heart rate, stress markers, etc.).

| Setting | What it means in plain terms | When to change it |
|---|---|---|
| `window_size_sec` | How many seconds of data are used to calculate each feature value. Bigger = smoother but less responsive. | Leave at `20` unless your study needs a different resolution. |
| `stride_sec` | How often a new feature value is calculated. | Leave at `1` (once per second) for most cases. |
| `edge_buffer_sec` | A short buffer trimmed from the very start/end of a recording to avoid inaccurate edge readings. | Leave at `2`. |
| `min_reliability` **(commonly changed)** | How confident the EmotiBit must be in a reading (0–100) before it's used in feature calculations. Lower this if your sensor readings are noisy but you still want data through. | Try `80` first; lower it (e.g. `60`) only if you're getting very little processed data due to a loose-fitting sensor. |
| `live_processing` | Whether features (heart rate, etc.) are calculated *while* recording, versus only saving raw data. | Leave `true` if you want to see live numbers. Set `false` if you only care about raw data and want to save your computer's processing power. |
| `visualize_every_n_windows` | Not currently active — safe to ignore. | — |

### 3.3 Offline processing settings — `offline:`

Only relevant if you're re-processing an already-saved recording (Section 6). In normal use, leave everything under `offline:` as `null`/`false` — the toolkit will automatically find your most recent recording.

| Setting | What it means in plain terms |
|---|---|
| `ppg_csv` / `eda_csv` | Manually point to specific PPG/EDA files instead of auto-detecting them. Leave `null` for normal use. |
| `output_csv` | Manually choose where the processed output is saved. Leave `null` to use the default location. |
| `is_e4` | Turn on only if you're processing data from an Empatica E4 device instead of EmotiBit. |

### 3.4 Time zone setting — `timesync:`

| Setting | What it means in plain terms | When to change it |
|---|---|---|
| `recording_timezone` **(commonly changed)** | The time zone of the location where the recording happened. Needed so timestamps line up correctly. | Set this to match wherever you're recording, e.g. `America/Chicago` (Texas) or `America/Los_Angeles` (California), before each session in a new location. |

---

## 4. Running with a real EmotiBit device (Live mode)

Use this when a participant is wearing the EmotiBit and you want to record real data.

### Before you start

- [ ] The EmotiBit is charged, powered on, and worn correctly
- [ ] Turn on UDP on Oscilloscope. (Click on Output List in the Top Right Corner -> Check UDP)
- [ ] Oscilloscope is set to send data to your port (port `12346`, same as `config.yaml`. This is the default port. No Need to change anything)
- [ ] Your computer and the EmotiBit are on the same Wi-Fi network
- [ ] You've set `run_tag` in `config.yaml` if you want a labeled folder (Section 3.1)

### Steps

1. Open a terminal in the project folder.
2. Run:

   ```bash
   python stream_timesync.py
   ```

3. You'll see a message like this — this tells you where your data is being saved:

   ```
   Run folder: data/2026_07_15_1783020361
   Listening on localhost:12346
   Press Ctrl+C to stop.
   ```

4. Once enough data has come in (about 20–25 seconds), you'll start seeing live updates in the terminal, confirming data is flowing:

   ```
   [features @ 22.0s] LF/HF=1.234
   ```

5. When you're done recording, press **Ctrl+C** in the terminal to stop safely. Always stop this way rather than closing the window — it makes sure your files are saved properly.

### What you get afterward

Everything is saved automatically in the folder printed in step 3 (inside the `data/` folder):

- **Raw data**: one file per sensor channel (heart rate, skin conductance, motion, etc.) — plain CSVs you can open in Excel
- **Live processed data**: a file with calculated features (heart rate, stress markers) already computed for you, if `live_processing` is on

> **Don't need device-aligned timestamps and just want the simplest possible recording?** You can use `python stream.py` instead of `python stream_timesync.py`

---

## 5. Testing without a device (Replay mode)

Use this to test your setup, practice the workflow, or debug problems — no EmotiBit hardware needed. You "replay" an existing recording file as if it were live data.

You'll need **two terminal windows** open at the same time, both in the project folder. First click on Terminal in the top left of the IDE. Click on New Terminal to start the terminal.Next, click again on Terminal and then click on Split Terminal. Two terminals will open at the bottom of the IDE.

### Steps

1. **In Terminal 1**, start the listener, pointing it at the same file you're about to replay so its timestamps line up correctly:

   ```bash
   python stream_timesync.py --timesync-csv path\to\your_recording.csv
   ```

   Replace `path\to\your_recording.csv` with the file you're about to replay in Terminal 2 (must be the same file in both terminals).

2. **In Terminal 2**, play back that recording file:

   ```bash
   python replay.py path\to\your_recording.csv --speed 50 --max-seconds 60
   ```

   - `--speed 50` plays it back 50× faster than real time (useful for quick tests). Use `--speed 1` to simulate real-time pacing.
   - `--max-seconds 60` stops after simulating 60 seconds of recording — remove this option to replay the whole file.

3. Watch Terminal 1 — it behaves exactly like a live session, saving files to a new `data/` folder and printing live feature updates.

4. When the replay finishes (or you press **Ctrl+C** in Terminal 1), your test recording is saved just like a real session.

### Common tweaks

| I want to... | Do this |
|---|---|
| Replay as fast as possible | `--speed 0` |
| Replay at real-world speed | `--speed 1` |
| Only test a short clip | Add `--max-seconds 30` (or any number of seconds) |
| Skip timestamp alignment and just test quickly | Use `python stream.py` in Terminal 1 instead (no `--timesync-csv` needed) |

---

## 6. Processing a recording afterward (Offline mode)

Use this if you recorded raw data only (or want to recalculate features with different settings) after the session is over.

### Steps

1. Open a terminal in the project folder.
2. To process your **most recent** recording, simply run:

   ```bash
   python process_offline.py
   ```

3. To process a **specific** recording folder instead:

   ```bash
   python process_offline.py --run-dir data\2026_07_15_1783020361
   ```

   (Replace the folder name with the one you want — you'll find it inside the `data` folder.)

4. The result is saved as a CSV file inside that recording's `processed` folder, containing calculated features (heart rate, stress markers, etc.) for the whole session.

### Making a quick chart

To generate a simple heart-rate chart image from a recording:

```bash
python plot_streams.py --run-dir data\2026_07_15_1783020361
```

This saves `HR.png` and `BI.png` (heart rate and beat-interval charts) directly into that recording's folder — handy for a quick visual check.

---

## 7. Finding your files

Every recording (live or replay) creates its own folder under `data/`, named with the date and a unique number, e.g.:

```
data/2026_07_15_1783020361/
├── raw/          → one CSV per sensor (open these in Excel)
└── processed/    → calculated feature CSVs (heart rate, stress markers, etc.)
```

If you set a `run_tag` (Section 3.1), it appears in the folder name too, e.g. `data/2026_07_15_P01_baseline_1783020361/`, making it easy to identify.

`process_offline.py` and `plot_streams.py` automatically use the **most recent** folder if you don't specify one.

---

## 8. Troubleshooting

| Problem | What's probably happening | What to do |
|---|---|---|
| Nothing happens / no "Listening..." message | Something else is already using that channel | Close other copies of the program, or ask a lab helper to check the port setting |
| Program runs, but no files are growing in size | The EmotiBit isn't actually sending data here | Check the EmotiBit is on and pointed at your computer's network address and port `12346` |
| No calculated features appear, only raw data | Not enough time has passed yet, or sensor contact is poor | Wait about 20–25 seconds; if it still doesn't appear, check the sensor is worn snugly (see `min_reliability` in Section 3.2) |
| Timestamps look wrong / don't match other devices | Time zone mismatch | Set `recording_timezone` in `config.yaml` to the correct location (Section 3.4) |
| `process_offline.py` says it can't find data | Wrong folder, or raw files are missing | Double check the folder path, and that both heart-rate and skin-conductance raw files exist in that folder's `raw/` subfolder |
| Replay finishes almost instantly | Speed is set very high | Use `--speed 1` for a realistic-speed test |

---

## 9. Quick command reference

| I want to... | Command |
|---|---|
| Record from a real device (default) | `python stream_timesync.py` |
| Record using computer-clock timestamps instead | `python stream.py` |
| Test without hardware | Terminal 1: `python stream_timesync.py --timesync-csv <file.csv>` — Terminal 2: `python replay.py <file.csv> --speed 50` |
| Process a saved recording | `python process_offline.py --run-dir data\<folder_name>` |
| Make a heart-rate chart | `python plot_streams.py --run-dir data\<folder_name>` |
| Change settings | Edit `config.yaml` in any text editor |
| Stop a running session | Press `Ctrl+C` in the terminal |

---

## Appendix: Technical details (for advanced users)

The rest of this document is background information for anyone maintaining the code or troubleshooting deeper issues. Everyday users do not need to read this.


### A.1 File path resolution priority in `process_offline.py`

When deciding which files to read/write, the script checks, in order:

1. Command-line flags (`--run-dir`, `--ppg-csv`, `--eda-csv`, `--output-csv`)
2. Values under `offline:` in `config.yaml`
3. Auto-discovery of the most recent folder under `data_dir`, using `ppg_stream`/`eda_stream` to locate the right raw files

### A.2 Full replay flag reference (`replay.py`)

| Flag | Default | Description |
|---|---|---|
| `csv_file` | required | The raw EmotiBit CSV to replay |
| `--host` | `localhost` | Must match `streaming.udp_host` |
| `--port` | `12346` | Must match `streaming.udp_port` |
| `--speed` | `1.0` | Playback speed multiplier (`0` = as fast as possible) |
| `--max-seconds` | none | Stop after this many seconds of recording time |

### A.3 Timesync mode details

`stream_timesync.py` is the default/recommended script. It derives `unix time` from the recording's own RD/TL/AK timing packets instead of your computer's clock — useful for aligning with another recording device, and harmless to use even when you don't need that alignment.

- **Live hardware:** omit `--timesync-csv`; the time map builds itself from incoming packets (early packets may be dropped until the first sync arrives).
- **Replay:** pass the same CSV to both `stream_timesync.py --timesync-csv <file>` and `replay.py <file>` so timestamps are correct from the first packet.
- Override the time zone per run with `--recording-tz`, without editing `config.yaml`.

`stream.py` is a simpler alternative that just uses your computer's clock at the moment each packet is received (`time.time()`) instead of the recording's own timing packets. Use it only if you specifically want to skip timestamp alignment.

### A.4 Feature columns produced

Both `processed_data_live.csv` and `processed_data.csv` contain:

`time`, `unix time`, `lf/hf`, `lf`, `hf`, `ibi mean`, `ibi stdev`, `hr`, `bvp amplitude`, `scr recovery time`, `scr peaks`, `scr rise time`, `scr no peaks`, `scl mean`, `scl stdev`


