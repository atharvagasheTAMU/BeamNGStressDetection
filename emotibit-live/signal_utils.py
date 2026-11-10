"""EDA/PPG signal processing and window feature extraction."""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.signal as sg
from scipy.signal import butter, filtfilt
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = [
    "time",
    "unix time",
    "lf/hf",
    "lf",
    "hf",
    "ibi mean",
    "ibi stdev",
    "hr",
    "bvp amplitude",
    "scr recovery time",
    "scr peaks",
    "scr rise time",
    "scr no peaks",
    "scl mean",
    "scl stdev",
]


def butter_bandpass_filter(data, lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype="band")
    return filtfilt(b, a, data)


def butter_lowpass_filter(data, cutoff, fs, order=4):
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype="low")
    return filtfilt(b, a, data)


def filter_EDA(signal, sample_rate):
    scr = butter_bandpass_filter(data=signal, order=3, lowcut=0.05, highcut=0.25, fs=sample_rate)
    scl = butter_lowpass_filter(data=signal, order=3, fs=sample_rate, cutoff=0.05)
    return scr, scl


def filter_BVP(signal, sample_rate):
    return butter_bandpass_filter(data=signal, order=4, lowcut=0.7, highcut=2.5, fs=sample_rate)


def find_SCL(signal):
    return np.average(signal), np.std(signal)


def find_SCR(signal, sample_rate):
    peaks = peaks_SCR(signal)
    if peaks.size == 0:
        return -1, -1, -1, 0

    peaks, peak_heights, rise_lengths, half_lengths, _, _ = SCR_feats(signal, peaks, sample_rate)
    if peaks.size == 0:
        return -1, -1, -1, 0

    scr_count = peaks.size
    rise_times = np.average(rise_lengths / sample_rate) if rise_lengths.size else -1
    half_times = np.average(half_lengths / sample_rate) if half_lengths.size else -1
    peak_heights = np.average(peak_heights) if peak_heights.size else -1
    return half_times, peak_heights, rise_times, scr_count


def peaks_SCR(signal, threshold=0.25, range=20):
    peaks, _ = sg.find_peaks(signal)
    cropped_signal = signal[range:-range]
    threshold = np.std(cropped_signal) * np.linspace(0.5, 1, range) ** 2 * threshold
    true_peaks = []
    for peak in peaks:
        if peak >= range and peak <= len(signal) - range:
            after = signal[peak : peak + range]
            before = signal[peak : peak - range : -1]
            limit = signal[peak] - threshold
            if np.any(after < limit) and np.any(before < limit):
                true_peaks.append(peak)
        elif peak < range:
            after = signal[peak : peak + range]
            before = signal[peak - 1 :: -1]
            limit = signal[peak] - threshold
            trim_limit = signal[peak] - threshold[:peak]
            if np.any(after < limit) and np.any(before < trim_limit):
                true_peaks.append(peak)
        elif peak > len(signal) - range:
            after = signal[peak:]
            before = signal[peak : peak - range : -1]
            limit = signal[peak] - threshold
            trim_limit = signal[peak] - threshold[: len(signal) - peak]
            if np.any(before < limit) and np.any(after < trim_limit):
                true_peaks.append(peak)
    return np.array(true_peaks)


def SCR_feats(signal, peaks, sr):
    peak_heights, rise_lengths, low_indices, half_lengths, half_indices = _find_features_SCR(
        signal, peaks, sr
    )
    if low_indices.size == 0:
        return peaks, peak_heights, rise_lengths, half_lengths, low_indices, half_indices

    threshold = np.std(signal) * 1.0
    if peaks[0] < low_indices[0]:
        peaks = np.delete(peaks, 0)
        return SCR_feats(signal, peaks, sr)

    for i, height in enumerate(peak_heights):
        if height < threshold:
            peaks = np.delete(peaks, i)
            return SCR_feats(signal, peaks, sr)

    for i, half_length in enumerate(half_lengths):
        if half_length > 10 * sr:
            peaks = np.delete(peaks, i)
            return SCR_feats(signal, peaks, sr)

    return peaks, peak_heights, rise_lengths, half_lengths, low_indices, half_indices


def _find_features_SCR(signal, peaks, sr):
    peaks, low_points, low_indices = _find_lowPoints(signal, peaks, sr)
    if len(peaks) > len(low_points) and peaks[0] < low_indices[0]:
        peaks = peaks[1:]

    peak_vals = signal[peaks]
    heights = peak_vals - low_points
    rises = peaks - low_indices
    _, half_indices = _find_halfRT(signal, peaks, heights, low_points)

    half_times = []
    for half_index in half_indices:
        possible_peaks = np.where(peaks < half_index)[0]
        half_times.append(half_index - peaks[possible_peaks[-1]])

    return (
        np.array(heights),
        np.array(rises),
        np.array(low_indices),
        np.array(half_times),
        np.array(half_indices),
    )


def _find_lowPoints(signal, peaks, sr, threshold_factor=0.5):
    low_points = []
    low_indices = []
    diff_1st_signal = np.diff(signal)
    diff_2nd_signal = np.diff(signal, n=2)
    std_diff = np.std(diff_1st_signal)
    threshold = threshold_factor * std_diff
    low_peaks = np.where(
        (np.abs(diff_1st_signal[:-1]) < threshold) & (diff_2nd_signal > 0)
    )[0] + 1

    upper_limit = 4
    for i in range(len(peaks)):
        if i == 0:
            bottom_range = 0 if peaks[i] / sr < upper_limit else int(peaks[i] - upper_limit * sr)
            tgt = range(bottom_range, peaks[i])
        else:
            bottom_range = (
                peaks[i - 1]
                if (peaks[i] - peaks[i - 1]) / sr < upper_limit
                else int(peaks[i] - upper_limit * sr)
            )
            tgt = range(bottom_range, peaks[i])

        possible_onsets = low_peaks[np.where((low_peaks >= tgt[0]) & (low_peaks <= tgt[-1]))]
        if len(possible_onsets) > 0:
            low_index = possible_onsets[-1]
        else:
            min_val = math.inf
            low_index = tgt[0]
            for onset in tgt:
                if (peaks[i] - onset) / sr > 5:
                    continue
                onset_val = signal[onset]
                if onset_val < min_val:
                    min_val = onset_val
                    low_index = onset

        low_points.append(signal[low_index])
        low_indices.append(low_index)

    return peaks, low_points, low_indices


def _find_halfRT(signal, peaks, heights, low_points):
    if len(heights) == 0:
        return [], []

    half_vals = heights / 2 + low_points
    halfies = []
    half_indices = []
    for i, p in enumerate(peaks):
        start = p
        end = peaks[i + 1] if i != len(peaks) - 1 else len(signal)
        index_condition = (
            (np.arange(len(signal)) > start)
            & (np.arange(len(signal)) < end)
            & (np.arange(len(signal)) < start + 200)
        )
        half_index = np.where(index_condition & (signal <= half_vals[i]))[0]
        try:
            half_index = half_index[0]
        except IndexError:
            continue
        halfies.append(signal[half_index])
        half_indices.append(half_index)
    return halfies, half_indices


def peaks_BVP(signal, sr, threshold=0.2, range=10, peak_width=0.3, trim_length=5):
    peaks, _ = sg.find_peaks(signal)
    cropped_signal = signal[trim_length:-trim_length]
    threshold = np.std(cropped_signal) * np.linspace(0.5, 1, range) ** 2 * threshold
    true_peaks = []
    for peak in peaks:
        if peak >= range and peak <= len(signal) - range:
            after = signal[peak : peak + range]
            before = signal[peak : peak - range : -1]
            limit = signal[peak] - threshold
            if np.any(after < limit) and np.any(before < limit):
                true_peaks.append(peak)
        elif peak < range:
            after = signal[peak : peak + range]
            before = signal[peak - 1 :: -1]
            limit = signal[peak] - threshold
            trim_limit = signal[peak] - threshold[:peak]
            if np.any(after < limit) and np.any(before < trim_limit):
                true_peaks.append(peak)
        elif peak > len(signal) - range:
            after = signal[peak:]
            before = signal[peak : peak - range : -1]
            limit = signal[peak] - threshold
            trim_limit = signal[peak] - threshold[: len(signal) - peak]
            if np.any(before < limit) and np.any(after < trim_limit):
                true_peaks.append(peak)

    peak_width = peak_width * sr
    last_peak = -peak_width
    kept_peaks = []
    for peak in true_peaks:
        if peak - last_peak > peak_width:
            kept_peaks.append(peak)
            last_peak = peak
        elif signal[peak] > signal[last_peak]:
            kept_peaks = kept_peaks[:-1]
            kept_peaks.append(peak)
            last_peak = peak
    return np.array(kept_peaks)


def find_BVP(signal, sample_rate):
    signal = np.asarray(signal)
    peaks = np.rint(peaks_BVP(signal, sr=sample_rate)).astype(int)
    min_peaks = np.rint(peaks_BVP(-signal, sr=sample_rate)).astype(int)
    n = len(signal)
    peaks = peaks[(peaks >= 0) & (peaks < n)]
    min_peaks = min_peaks[(min_peaks >= 0) & (min_peaks < n)]

    heights = signal[peaks] if peaks.size else np.array([])
    lows = signal[min_peaks] if min_peaks.size else np.array([])
    ibi, mean_ibi, std_ibi = _find_IBI(peaks, sample_rate)
    amps = _find_amps(heights, lows)
    return amps, mean_ibi, std_ibi, ibi, peaks


def extract_ppg_metrics(signal, sample_rate):
    amps, mean_ibi, std_ibi, ibi, peaks = find_BVP(signal, sample_rate)
    lf_hf, lf, hf = _safe_hrv_metrics(ibi, peaks, sample_rate)
    return {
        "bvp amplitude": amps,
        "ibi mean": mean_ibi,
        "ibi stdev": std_ibi,
        "lf/hf": lf_hf,
        "lf": lf,
        "hf": hf,
    }


def find_LH(ibi, peaks, sample_rate):
    signal = []
    for i in range(len(ibi)):
        peak = peaks[i]
        ibis = [ibi[i]] * (peak if i == 0 else peak - peaks[i - 1])
        signal.append(ibis)
    signal = [item for sublist in signal for item in sublist]

    freq, power = sg.welch(signal, fs=sample_rate, scaling="density", nperseg=len(signal))
    step = freq[1] - freq[0]
    vlf_power = np.sum(power[np.where((freq >= 0.0033) & (freq <= 0.04))]) * step
    lf_power = np.sum(power[np.where((freq >= 0.04) & (freq <= 0.15))]) * step
    hf_power = np.sum(power[np.where((freq >= 0.15) & (freq <= 0.4))]) * step
    total_power = vlf_power + lf_power + hf_power
    lf = lf_power / (total_power - vlf_power)
    hf = hf_power / (total_power - vlf_power)
    return lf / hf, lf, hf


def _find_IBI(peaks, sample_rate):
    ibi = np.array([peaks[i + 1] - peaks[i] for i in range(len(peaks) - 1)])
    ibi = ibi * 1000 / sample_rate
    if ibi.size == 0:
        return ibi, -1, -1
    return ibi, np.average(ibi), np.std(ibi)


def _find_amps(heights, lows):
    if heights.size == 0 or lows.size == 0:
        return -1
    return (np.average(heights) - np.average(lows)) / 2


def _safe_hrv_metrics(ibi, peaks, sample_rate):
    if ibi.size == 0 or peaks.size < 2:
        return -1, -1, -1
    try:
        return find_LH(ibi, peaks, sample_rate)
    except (ValueError, ZeroDivisionError, IndexError):
        return -1, -1, -1


def _hr_from_ibi_mean(ibi_mean):
    if ibi_mean <= 0:
        return -1
    return round(60000 / ibi_mean, 2)


def extract_window_features(
    ppg_df,
    eda_df=None,
    window_start=0.0,
    window_end=0.0,
    window_size_sec=20.0,
    visualize=False,
    eda_scr_df=None,
    eda_scl_df=None,
    prefiltered=False,
):
    ppg_window = ppg_df[(ppg_df["time"] >= window_start) & (ppg_df["time"] <= window_end)].copy()
    if eda_scr_df is not None:
        scr_window = eda_scr_df[
            (eda_scr_df["time"] >= window_start) & (eda_scr_df["time"] <= window_end)
        ].copy()
        scl_source = eda_scl_df if eda_scl_df is not None else eda_scr_df
        scl_window = scl_source[
            (scl_source["time"] >= window_start) & (scl_source["time"] <= window_end)
        ].copy()
    elif eda_df is not None:
        scr_window = eda_df[(eda_df["time"] >= window_start) & (eda_df["time"] <= window_end)].copy()
        scl_window = scr_window
    else:
        return None

    if ppg_window.empty or scr_window.empty or scl_window.empty:
        return None

    ppg_sr = len(ppg_window) / window_size_sec
    eda_sr = len(scr_window) / window_size_sec
    if ppg_sr <= 0 or eda_sr <= 0:
        return None

    scaler = StandardScaler()
    ppg_values = scaler.fit_transform(ppg_window["value"].to_numpy().reshape(-1, 1)).reshape(-1)
    if prefiltered:
        scr_values = scr_window["value"].to_numpy()
        scl_values = scl_window["value"].to_numpy()
    else:
        ppg_values = filter_BVP(ppg_values, ppg_sr)
        eda_values = scaler.fit_transform(scr_window["value"].to_numpy().reshape(-1, 1)).reshape(-1)
        scr_values, scl_values = filter_EDA(eda_values, eda_sr)

    # if visualize:
    #     visualize_PPG(ppg_values, ppg_sr)
    #     visualize_EDA(scr_values, scl_values, eda_sr)

    ppg_metrics = extract_ppg_metrics(ppg_values, ppg_sr)
    scr_rec, scr_amp, scr_rise, scr_count = find_SCR(scr_values, eda_sr)
    scl_mean, scl_stdev = find_SCL(scl_values)

    return {
        "time": window_end,
        "lf/hf": ppg_metrics["lf/hf"],
        "lf": ppg_metrics["lf"],
        "hf": ppg_metrics["hf"],
        "ibi mean": ppg_metrics["ibi mean"],
        "ibi stdev": ppg_metrics["ibi stdev"],
        "hr": _hr_from_ibi_mean(ppg_metrics["ibi mean"]),
        "bvp amplitude": ppg_metrics["bvp amplitude"],
        "scr recovery time": scr_rec,
        "scr peaks": scr_amp,
        "scr rise time": scr_rise,
        "scr no peaks": scr_count,
        "scl mean": scl_mean,
        "scl stdev": scl_stdev,
    }


def visualize_PPG(ppg, sr):
    peaks = peaks_BVP(ppg, sr)
    min_peaks = peaks_BVP(ppg * -1, sr)
    plt.figure(figsize=(10, 4))
    plt.scatter(peaks, ppg[peaks], color="red", label="High Peaks")
    plt.scatter(min_peaks, ppg[min_peaks], color="green", label="Low Peaks")
    plt.plot(ppg, label="PPG Signal")
    plt.xlabel("Samples")
    plt.ylabel("Amplitude")
    plt.title("PPG Signal Visualization")
    plt.legend()
    plt.grid(True)
    plt.show()


def visualize_EDA(scr, scl, sr):
    peaks = peaks_SCR(scr)
    if peaks.size != 0:
        peaks, _, _, _, low_indices, half_indices = SCR_feats(scr, peaks, sr)
    else:
        low_indices, half_indices = [], []

    fig, axs = plt.subplots(2, 1, figsize=(10, 8))
    axs[0].plot(scr, label="SCR Signal")
    if len(peaks):
        axs[0].scatter(peaks, scr[peaks], color="red", label="Peaks")
    if len(low_indices):
        axs[0].scatter(low_indices, scr[low_indices], color="blue", label="Onsets")
    if len(half_indices):
        axs[0].scatter(half_indices, scr[half_indices], color="green", label="Half Recovery")
    axs[0].set_title("Skin Conductance Response (SCR)")
    axs[0].legend()
    axs[1].plot(scl, label="SCL Signal", color="orange")
    axs[1].set_title("Skin Conductance Level (SCL)")
    axs[1].legend()
    plt.tight_layout()
    plt.show()
