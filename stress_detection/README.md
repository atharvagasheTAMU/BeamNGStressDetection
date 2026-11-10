# Stress Detection

## What this does

This folder contains the logic that decides whether someone is **stressed**
or **calm**, based on two body signals:

- **Heart rate (HR)** — how fast the heart is beating, and how quickly that
  changes.
- **Skin conductance (EDA)** — tiny increases in sweat on the skin, which
  show up as brief "spikes." These spikes are called **SCR peaks**, and
  people tend to produce more of them close together when startled or
  stressed.

There are several ways to decide "stressed," listed below from simplest to
most combined. You choose one with `--baseline-mode` when running the demo
or console script (see the main project README).

- **Rolling** — compares the current heart rate to the average heart rate
  over the last 30 seconds or so. If it's noticeably higher or lower than
  that recent average, it's flagged as stressed or calm.
- **Calibrated** — like Rolling, but the "normal" heart rate is measured
  once at the start of the session and doesn't drift afterward.
- **Slope** — looks at how *quickly* the heart rate is climbing right now,
  rather than comparing it to an average. A fast rise is treated as a sign
  of sudden stress.
- **EDA (skin response)** — counts how many sweat-response spikes happened
  in roughly the last 20 seconds. Several spikes close together is treated
  as a sign of stress.
- **Combined** — uses both the heart rate "Slope" check and the "EDA" check
  together. You can choose whether *either one* is enough to flag stress,
  or whether *both* need to agree.

Whichever mode is chosen, once stress is detected it stays "on" for a short
period (so the system doesn't flicker on and off every second) and then
automatically returns to normal.

All of the specific numbers used above (how many seconds, how many spikes,
how much of a heart rate rise) can be adjusted — see **Technical details**
below.

---

## Technical details

Pure-Python detectors with no BeamNG or UI dependency. Each detector takes
samples from `DataStore` (fed by LSL) and returns a `StressState`
(`elevated`, `lowered`, or `baseline`). Demos in `../scripts/` map that
state to driving modes.

Signal processing for HR and EDA happens **upstream** in
`../emotibit-live/lsl_bridge.py` (20 s windows, 1 s stride). This package
assumes those LSL streams are already running.

### Inputs (`lsl_receiver.py`)

| LSL source | Stored as | Used by |
|------------|-----------|---------|
| Stream name contains `HR` (e.g. `HR-Bridge`) | Rolling BPM buffer (`~30` samples) | HR detectors |
| Stream name contains `SCR-BRIDGE` | Latest `(scr_peaks, scr_no_peaks)` | EDA / combined |
| Raw EDA / GSR streams (optional) | Raw sample buffer | Not used by current detectors |

`scr_peaks` is mean SCR peak **amplitude** in the window (or `-1` if none).
`scr_no_peaks` is the **count** of validated SCR peaks in the window — this
is the EDA stress signal.

Start the receiver with `start_receiver(store, require_hr=..., require_scr=...)`
depending on mode.

### HR detection

All HR modes consume `store.get_hr()` (BPM time series from `HR-Bridge`).

#### Rolling (`hr_stress_detector.py`)

- **Baseline:** mean of all samples in the HR buffer
- **Current:** latest BPM
- **Rule:** `delta = current − baseline`
  - `delta ≥ +8` BPM (default) → `ELEVATED`
  - `delta ≤ −8` BPM → `LOWERED`
  - else hold current state
- **Debounce:** minimum 6 s between state changes
- **Warmup:** at least 8 HR samples before decisions

Best when you want symmetric "high vs low" vs a drifting local average. In
the BeamNG demo, `ELEVATED` → aggressive track, `LOWERED` → defensive.

#### Calibrated (`calibrated_stress_detector.py`)

Same ± BPM rule as rolling, but baseline is **fixed** after collecting N
calibration samples at session start (default 30). Use when you want a
stable "resting HR" reference that does not chase sustained elevation.

#### Slope (`slope_stress_detector.py`)

Detects **acute rise**, not level:

- Fit linear regression of BPM vs time over the last **4 s** (default),
  using poll timestamps
- **Rule:** slope ≥ **1.3 BPM/s** (default) → `ELEVATED`
- **Hold:** stay `ELEVATED` for **20 s**, then auto-release to `BASELINE`
- **Warmup:** need enough timestamped HR samples in the regression window

In the BeamNG demo, `ELEVATED` → **defensive**; default start mode is
aggressive.

**Tunables:** `SLOPE_WINDOW_S`, `SLOPE_THRESH_BPM_PER_S`,
`MIN_SLOPE_SAMPLES`, `DEFENSIVE_HOLD_S` at top of module (or CLI on
`../scripts/stress_detect_console.py`).

### EDA detection (`eda_scr_stress_detector.py`)

Consumes **`scr_no_peaks`** from `store.get_scr_features()` (SCR-Bridge),
not raw EDA.

- **Rule:** `scr_no_peaks ≥ 3` (default `SCR_MIN_PEAKS_FOR_STRESS`) →
  `ELEVATED`
- **Hold:** 20 s defensive hold, then `BASELINE`
- **Warmup:** at least one SCR window sample

`scr_peaks` (amplitude) is exposed for logging only; the threshold is on
**peak count**.

BeamNG demo maps `ELEVATED` → defensive (same as slope).

### Combined (`combined_stress_detector.py`)

Runs slope + SCR count logic each tick, then fuses cues:

| Policy | Stressed when |
|--------|----------------|
| `or` (default) | HR slope ≥ threshold **or** SCR count ≥ threshold |
| `and` | Both must trigger |

One shared `defensive_hold_s` timer; sub-detectors do not own separate
holds.

### Module map

| File | Role |
|------|------|
| `lsl_receiver.py` | LSL → `DataStore` |
| `hr_stress_detector.py` | Rolling mean HR |
| `calibrated_stress_detector.py` | Fixed-baseline HR |
| `slope_stress_detector.py` | HR rate of change |
| `eda_scr_stress_detector.py` | SCR peak count |
| `combined_stress_detector.py` | HR slope + SCR fusion |

### Configuration

Defaults are module-level constants and `*Config` dataclasses. Override via
constructor in code, or flags on `../scripts/stress_detect_console.py` and
`../scripts/mode_switch_stress_demo.py` (`--baseline-mode`, `--slope-thresh`,
`--scr-min-peaks`, `--combine-policy`, etc.).

### Standalone test (repo root)

```bash
python -m stress_detection.hr_stress_detector
python -m stress_detection.eda_scr_stress_detector
python -m stress_detection.combined_stress_detector
```

Requires `lsl_bridge.py` running and, for HR modes, `HR-Bridge` on the
network.
