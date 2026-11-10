# BeamNG Stress Detection

## What this does

This project watches a person's heart rate and skin conductance (a measure of
sweat response) while they drive in the BeamNG driving simulator, and
automatically decides whether they seem **stressed** or **calm**.

A wearable sensor (EmotiBit) measures heart rate and skin signals. That data
is sent to this software, which looks for signs of stress — like a heart
rate that's rising quickly, or several skin-conductance spikes close
together. When stress is detected, the simulator can automatically switch
the self-driving car onto a more cautious ("defensive") route; when the
person calms down, it switches back.

There are two ways to use it:

- **Just watch the numbers** — run a simple on-screen readout of stress
  detection without needing the driving simulator at all. Useful for
  testing the sensor and tuning sensitivity.
- **Full demo** — run it together with BeamNG so the car's driving style
  changes automatically based on detected stress.

No programming knowledge is required to run the demo once it is set up —
see **Quick start** below. The **Technical details** section further down
is for anyone who wants to understand or modify how stress is detected.

## Quick start

### What you need

1. An EmotiBit sensor, worn and connected, with its software (the
   "Oscilloscope" app) running and UDP output turned on.
2. BeamNG.tech installed (only needed for the full driving demo).
3. **Python 3.10+** and a project **virtual environment** (`.venv`) with
   packages installed once — see **Python environment** below.

### Python environment (one-time)

Use a `.venv` in this folder so dependencies stay separate from other
projects. Set up once:

```powershell
cd path\to\BeamNGStressDetection
.\setup_env.ps1
```

Or manually:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r emotibit-live/requirements.txt
```

**Before each session**, activate the venv in any terminal where you run
Python (prompt should show `(.venv)`):

```powershell
.\.venv\Scripts\Activate.ps1
```

`run_demo.ps1` uses the project's `.venv` automatically when it exists.

If `import pylsl` fails, install **liblsl** (Lab Streaming Layer). See
[liblsl](https://github.com/sccn/liblsl).

### Running it

You always run **two programs at the same time**, in two separate terminal
windows:

**Terminal 1 — reads the sensor and sends the data:**

```bash
cd emotibit-live
python lsl_bridge.py
```

Leave this running for the whole session.

**Terminal 2 — detects stress (pick one):**

```bash
# Just watch the numbers, no driving simulator
python scripts/stress_detect_console.py --baseline-mode combined

# Full demo with BeamNG
python scripts/mode_switch_stress_demo.py --baseline-mode slope
```

Press `Ctrl+C` in a terminal to stop that program.

If you don't have the sensor connected yet and just want to try the system
using a pre-recorded session instead, use **`.\run_demo.ps1`** (replay + console)
or see **Replay without hardware** in the Technical details section below.

**Replay shortcut (3 windows):** from this folder (after `.\setup_env.ps1`):

```powershell
.\run_demo.ps1
```

Edit `run_demo.ps1` at the top to change recording file, `RunMode` (`console`
or `demo`), and stress detection mode.

---

## Technical details

### Requirements

- Python 3.10+
- [liblsl](https://github.com/sccn/liblsl) (for `pylsl`)
- **Live:** EmotiBit with UDP output enabled (see `emotibit-live/README.md`)
- **BeamNG demo:** BeamNG.tech + compatible [beamngpy](https://github.com/BeamNG/BeamNGpy) version

Configure BeamNG paths in `beamng_demo/mode_switch_demo.py` (`BEAMNG_HOME`,
`BEAMNG_BINARY`) before running the driving demo.

### Repository layout

| Path | Purpose |
|------|---------|
| `emotibit-live/` | UDP ingest, feature extraction, `lsl_bridge.py` (HR / HRV / SCR on LSL) |
| `stress_detection/` | LSL consumer + stress detector modules (see its own README) |
| `beamng_demo/` | Track JSON files + shared BeamNG path-switching helpers |
| `scripts/` | Entry points: console test and BeamNG stress demo |

Only **one** process may bind the EmotiBit UDP port (`12346` by default in
`emotibit-live/config.yaml`). Use `lsl_bridge.py` for this project — not
`stream.py` — so features are published on LSL.

### Data flow

```
EmotiBit (UDP) → lsl_bridge.py → LSL (HR-Bridge, SCR-Bridge, …)
                                      ↓
                    stress_detect_console.py  |  mode_switch_stress_demo.py
```

LSL stream names consumed by `stress_detection/lsl_receiver.py`:

- **HR:** name contains `HR` (e.g. `HR-Bridge`)
- **SCR features:** name contains `SCR-BRIDGE` (channels: `scr_peaks`, `scr_no_peaks`)

### Replay without hardware (3 terminals)

1. `python lsl_bridge.py` (in `emotibit-live/`)
2. `python replay.py path\to\recording.csv --speed 50` (in `emotibit-live/`)
3. Console or BeamNG demo (as in Quick start)

Details on recordings and replay flags: `emotibit-live/README.md`.

### Detection modes (`--baseline-mode`)

| Mode | Signal | Typical driving map |
|------|--------|---------------------|
| `rolling` | HR vs rolling mean | elevated → aggressive, lowered → defensive |
| `calibrated` | HR vs recorded baseline | same as rolling |
| `slope` | HR rise (BPM/s) | fast rise → defensive (hold, then aggressive) |
| `eda` | SCR peak count per 20 s window | count ≥ threshold → defensive |
| `combined` | HR slope + SCR (`--combine-policy or\|and`) | defensive on fused cue |

Common flags: `--slope-thresh`, `--slope-window`, `--scr-min-peaks`,
`--defensive-hold`, `--combine-policy`.

Threshold defaults live in the corresponding modules under
`stress_detection/` (e.g. `SCR_MIN_PEAKS_FOR_STRESS`, `SLOPE_THRESH_BPM_PER_S`).
See `stress_detection/README.md` for how each mode decides "stressed".

### Run a detector module directly (optional)

```bash
python -m stress_detection.combined_stress_detector
```

Run from the repo root so `stress_detection` is importable (same as the
`scripts/` entry points).

### Data on disk

`lsl_bridge.py` writes runs under `emotibit-live/data/`. That directory is
gitignored; do not commit session captures.
