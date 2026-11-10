"""
Eye-tracker live metrics publisher for stress-detection middleware.

Runs the same camera pipeline as EyeTracker/eye_tracker2.py, but pushes ~1 Hz
metric rows and discrete events onto LSL instead of only writing Excel.

Stream names (keep in sync with stress_detection/eye_lsl_receiver.py):
    EyeMetrics-Bridge  — one JSON string sample per metrics tick
    EyeEvent-Bridge    — JSON string per discrete event

Run:
    python lsl_bridge.py
    python lsl_bridge.py --experiment-id session01 --camera 0

Prerequisites:
    pip install -r eye-tracker-live/requirements.txt
    Start scripts/eye_tracker_console.py (or another LSL consumer) in another terminal.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

import cv2

from tracker_core import (
    LEFT_EYE,
    RIGHT_EYE,
    compute_proximity_ratio,
    face_mesh,
    get_brow_furrow,
    get_brow_height,
    get_eye_openness,
    get_gaze_direction,
    get_gaze_vertical,
    get_head_pose,
    get_iod_pixels,
    get_lip_corner_elevation,
    get_shoulder_width_pixels,
    mp_drawing,
    mp_face_mesh,
    mp_pose,
    pose,
)

METRICS_STREAM_NAME = "EyeMetrics-Bridge"
EVENTS_STREAM_NAME = "EyeEvent-Bridge"
METRICS_INTERVAL_S = 1.0

# Thresholds (same defaults as eye_tracker2.py)
WARN_YAW_THRESH = 30.0
WARN_PITCH_THRESH = 20.0
AWAY_DURATION_THRESH = 1.5
WARN_COOLDOWN = 5.0
EYE_MOVEMENT_THRESHOLD = 5
POSE_MOVEMENT_THRESHOLD = 0.05
EYE_CLOSURE_THRESHOLD = 0.57
EYE_CLOSURE_DURATION_THRESH = 0.5
EYE_OPENNESS_PERCENT_THRESHOLD = 0.70
LOG_COOLDOWN_SECONDS = 2


def _make_outlets():
    try:
        from pylsl import StreamInfo, StreamOutlet
    except ImportError as exc:
        print(
            "Failed to import pylsl. Install: pip install pylsl",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    metrics_info = StreamInfo(
        name=METRICS_STREAM_NAME,
        type="EyeMetrics",
        channel_count=1,
        nominal_srate=1.0,
        channel_format="string",
        source_id="beamng_eye_metrics_bridge",
    )
    metrics_info.desc().append_child_value("format", "json")
    metrics_info.desc().append_child_value("reference", "EyeTracker/eye_tracker2.py metrics row")

    events_info = StreamInfo(
        name=EVENTS_STREAM_NAME,
        type="EyeEvents",
        channel_count=1,
        nominal_srate=0.0,
        channel_format="string",
        source_id="beamng_eye_events_bridge",
    )
    events_info.desc().append_child_value("format", "json")

    return StreamOutlet(metrics_info), StreamOutlet(events_info)


def _push_event(outlet, experiment_id: str, event_type: str, value: str) -> None:
    payload = {
        "experiment_id": experiment_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event_type,
        "value": value,
    }
    outlet.push_sample([json.dumps(payload)])


def _push_metrics(outlet, row: dict) -> None:
    outlet.push_sample([json.dumps(row, default=str)])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-id",
        default="",
        help="Session label included in every LSL payload (prompted if empty).",
    )
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index (default: 0).")
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Do not open the preview window (headless streaming).",
    )
    parser.add_argument(
        "--print-events",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mirror discrete events to stdout (default: on).",
    )
    return parser.parse_args()


def _draw_top_banner(frame, text: str, *, ok: bool = False) -> None:
    """On-preview hint so calibration is visible (like eye_tracker2.py)."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thickness = 0.65, 2
    pad_y = 12
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    bar_h = th + pad_y * 2
    bg = (0, 60, 0) if ok else (0, 0, 0)
    color = (0, 255, 120) if ok else (0, 220, 255)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (frame.shape[1], bar_h), bg, -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)
    cv2.putText(
        frame,
        text,
        ((frame.shape[1] - tw) // 2, pad_y + th),
        font,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def main() -> None:
    args = _parse_args()
    experiment_id = args.experiment_id.strip() or input("Enter experiment ID: ").strip() or "unknown"

    metrics_outlet, events_outlet = _make_outlets()
    print(f"[eye-bridge] LSL outlets: {METRICS_STREAM_NAME}, {EVENTS_STREAM_NAME}")
    print(f"[eye-bridge] experiment_id={experiment_id!r}  camera={args.camera}")
    print("[eye-bridge] SPACE=calibrate  Q=quit\n")

    def log_event(event_type: str, value: str) -> None:
        _push_event(events_outlet, experiment_id, event_type, value)
        if args.print_events:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{ts} [{event_type}] {value}")

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"[eye-bridge] ERROR: cannot open camera {args.camera}", file=sys.stderr)
        raise SystemExit(1)

    prev_left_eye = prev_right_eye = None
    prev_pose = None
    eye_closed_start = None
    last_eye_log_time = 0.0
    last_pose_log_time = 0.0
    last_head_y = None
    calibrated = False
    baseline_iod = None
    baseline_shoulder = None
    baseline_brow_height = None
    baseline_brow_furrow = None
    baseline_lip_elev = None
    baseline_eye_openness = None
    last_metrics_log = 0.0
    eye_pct = 0.0
    head_yaw = head_pitch = head_roll = None
    head_away_start = None
    last_head_warn_time = 0.0
    last_head_angle_log = 0.0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_results = face_mesh.process(frame_rgb)
        pose_results = pose.process(frame_rgb)
        h, w, _ = frame.shape
        current_time = time.time()

        if not args.no_gui:
            if not calibrated:
                _draw_top_banner(
                    frame,
                    "Click here, sit normally, press SPACE to calibrate",
                )
            else:
                _draw_top_banner(frame, "Calibrated — streaming metrics", ok=True)

        current_iod = None
        lm = None
        gaze_h = gaze_v = gaze_dir = None
        gaze_ratio_h = gaze_ratio_v = None
        h_dir = None
        is_away = False
        eye_lbl = None

        if calibrated and baseline_eye_openness:
            _eye_low_thresh = baseline_eye_openness * 0.75
            _eye_shut_thresh = baseline_eye_openness * 0.55
        else:
            _eye_low_thresh = EYE_OPENNESS_PERCENT_THRESHOLD * 100
            _eye_shut_thresh = EYE_CLOSURE_THRESHOLD * 100

        if face_results.multi_face_landmarks:
            for face_landmarks in face_results.multi_face_landmarks:
                if not args.no_gui:
                    mp_drawing.draw_landmarks(
                        frame, face_landmarks, mp_face_mesh.FACEMESH_CONTOURS
                    )
                lm = face_landmarks.landmark

                left_eye_pos = (int(lm[362].x * w), int(lm[362].y * h))
                right_eye_pos = (int(lm[133].x * w), int(lm[133].y * h))

                if prev_left_eye and prev_right_eye:
                    le_mv = abs(prev_left_eye[0] - left_eye_pos[0]) + abs(
                        prev_left_eye[1] - left_eye_pos[1]
                    )
                    re_mv = abs(prev_right_eye[0] - right_eye_pos[0]) + abs(
                        prev_right_eye[1] - right_eye_pos[1]
                    )
                    if (
                        le_mv > EYE_MOVEMENT_THRESHOLD or re_mv > EYE_MOVEMENT_THRESHOLD
                    ) and current_time - last_eye_log_time > LOG_COOLDOWN_SECONDS:
                        log_event("Eye_Movement", f"L:{le_mv}px R:{re_mv}px")
                        last_eye_log_time = current_time
                prev_left_eye = left_eye_pos
                prev_right_eye = right_eye_pos

                eye_pct = (
                    get_eye_openness(lm, LEFT_EYE, w, h)
                    + get_eye_openness(lm, RIGHT_EYE, w, h)
                ) / 2

                if calibrated and baseline_eye_openness and eye_pct < _eye_low_thresh:
                    log_event("Low_Eye_Openness", f"{eye_pct:.1f}%")

                if eye_pct < _eye_shut_thresh:
                    if eye_closed_start is None:
                        eye_closed_start = current_time
                else:
                    if eye_closed_start is not None:
                        dur = current_time - eye_closed_start
                        if calibrated and dur >= EYE_CLOSURE_DURATION_THRESH:
                            log_event("Eyes_Closed", f"{dur:.2f}s")
                        eye_closed_start = None

                current_iod = get_iod_pixels(lm, w, h)
                gaze_ratio_h, gaze_h = get_gaze_direction(lm)
                gaze_ratio_v, gaze_v = get_gaze_vertical(lm)
                if gaze_h not in (None, "unknown") and gaze_v not in (None, "unknown"):
                    if gaze_h == "center" and gaze_v == "center":
                        gaze_dir = "center"
                    elif gaze_v == "center":
                        gaze_dir = gaze_h
                    elif gaze_h == "center":
                        gaze_dir = gaze_v
                    else:
                        gaze_dir = f"{gaze_v}-{gaze_h}"

        if face_results.multi_face_landmarks:
            nose_y = face_results.multi_face_landmarks[0].landmark[1].y
            if last_head_y is not None and abs(nose_y - last_head_y) > 0.03:
                log_event("Head_Nod", f"dy={abs(nose_y - last_head_y):.3f}")
            last_head_y = nose_y

        head_yaw = head_pitch = head_roll = None
        if lm is not None:
            head_yaw, head_pitch, head_roll = get_head_pose(lm, w, h)
            if head_yaw is not None:
                if abs(head_yaw) >= abs(head_pitch):
                    h_dir = (
                        "right"
                        if head_yaw > 10
                        else "left"
                        if head_yaw < -10
                        else "forward"
                    )
                else:
                    h_dir = (
                        "down"
                        if head_pitch > 10
                        else "up"
                        if head_pitch < -10
                        else "forward"
                    )

                if current_time - last_head_angle_log >= 2.0:
                    log_event(
                        "Head_Angle",
                        f"yaw={head_yaw:+.1f}deg pitch={head_pitch:+.1f}deg "
                        f"roll={head_roll:+.1f}deg | {h_dir}",
                    )
                    last_head_angle_log = current_time

                is_away = abs(head_yaw) > WARN_YAW_THRESH or abs(head_pitch) > WARN_PITCH_THRESH
                if is_away:
                    if head_away_start is None:
                        head_away_start = current_time
                    elif (
                        current_time - head_away_start >= AWAY_DURATION_THRESH
                        and current_time - last_head_warn_time >= WARN_COOLDOWN
                    ):
                        away_dir = (
                            "right"
                            if head_yaw > WARN_YAW_THRESH
                            else "left"
                            if head_yaw < -WARN_YAW_THRESH
                            else "down"
                            if head_pitch > WARN_PITCH_THRESH
                            else "up"
                        )
                        log_event(
                            "Head_Away",
                            f"dir={away_dir} | yaw={head_yaw:+.1f}deg pitch={head_pitch:+.1f}deg",
                        )
                        last_head_warn_time = current_time
                else:
                    head_away_start = None

        current_shoulder = None
        if pose_results.pose_landmarks:
            if not args.no_gui:
                mp_drawing.draw_landmarks(
                    frame, pose_results.pose_landmarks, mp_pose.POSE_CONNECTIONS
                )
            curr_pose = [(lmk.x, lmk.y) for lmk in pose_results.pose_landmarks.landmark]
            if prev_pose:
                total_mv = sum(
                    abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(prev_pose, curr_pose)
                )
                if (
                    total_mv > POSE_MOVEMENT_THRESHOLD
                    and current_time - last_pose_log_time > LOG_COOLDOWN_SECONDS
                ):
                    log_event("Body_Movement", f"{total_mv:.3f}")
                    last_pose_log_time = current_time
            prev_pose = curr_pose
            current_shoulder = get_shoulder_width_pixels(pose_results.pose_landmarks, w, h)

        key = 0 if args.no_gui else cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            if current_iod and lm is not None:
                baseline_iod = current_iod
                baseline_shoulder = current_shoulder
                baseline_brow_height = get_brow_height(lm, w, h)
                baseline_brow_furrow = get_brow_furrow(lm, w, h)
                baseline_lip_elev = get_lip_corner_elevation(lm, w, h)
                baseline_eye_openness = eye_pct
                calibrated = True
                log_event("Calibration_Set", "Baseline recorded for all metrics")
                print(
                    f"[Calibrated] IOD={baseline_iod:.1f}px | "
                    f"Eye={baseline_eye_openness:.1f}%"
                )
            else:
                print("[Calibration] No face detected — try again.")

        prox = prox_pct = prox_lbl = None
        brow_pct = brow_lbl = None
        furrow_pct = furrow_lbl = None
        delta_pct = lip_lbl = None
        smile_squint = False

        if calibrated and lm is not None:
            if baseline_eye_openness:
                eye_ratio = eye_pct / baseline_eye_openness * 100
                eye_lbl = (
                    "open"
                    if eye_ratio > 85
                    else "closing"
                    if eye_ratio < 55
                    else "drowsy"
                )

            prox = compute_proximity_ratio(
                current_iod, baseline_iod, current_shoulder, baseline_shoulder
            )
            if prox is not None:
                prox_pct = prox * 100
                prox_lbl = (
                    "closer"
                    if prox_pct > 105
                    else "farther"
                    if prox_pct < 95
                    else "neutral"
                )

            brow_h = get_brow_height(lm, w, h)
            if brow_h and baseline_brow_height:
                brow_pct = (brow_h / baseline_brow_height) * 100
                brow_lbl = (
                    "raised"
                    if brow_pct > 110
                    else "lowered"
                    if brow_pct < 90
                    else "neutral"
                )

            brow_f = get_brow_furrow(lm, w, h)
            if brow_f and baseline_brow_furrow:
                furrow_pct = (brow_f / baseline_brow_furrow) * 100
                furrow_lbl = (
                    "furrowed"
                    if furrow_pct < 100
                    else "relaxed"
                    if furrow_pct > 108
                    else "neutral"
                )

            lip_e = get_lip_corner_elevation(lm, w, h)
            if lip_e is not None and baseline_lip_elev is not None:
                delta_pct = (lip_e - baseline_lip_elev) * 100
                lip_lbl = (
                    "smile"
                    if delta_pct > 3.5
                    else "grimace"
                    if delta_pct < -2.5
                    else "neutral"
                )

            if (
                baseline_eye_openness
                and baseline_lip_elev is not None
                and lip_e is not None
            ):
                smile_delta = (lip_e - baseline_lip_elev) * 100
                eye_ratio_sq = (eye_pct / baseline_eye_openness) * 100
                if smile_delta > 5 and eye_ratio_sq < 88:
                    smile_squint = True

        if (
            calibrated
            and lm is not None
            and current_time - last_metrics_log >= METRICS_INTERVAL_S
        ):
            row = {
                "Experiment_ID": experiment_id,
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Eye_Openness_Pct": round(eye_pct, 1),
                "Eye_State": eye_lbl,
                "Gaze_Horizontal": gaze_h,
                "Gaze_Vertical": gaze_v,
                "Gaze_Direction": gaze_dir,
                "Gaze_Ratio_H": gaze_ratio_h,
                "Gaze_Ratio_V": gaze_ratio_v,
                "Head_Yaw_Deg": round(head_yaw, 1) if head_yaw is not None else None,
                "Head_Pitch_Deg": round(head_pitch, 1) if head_pitch is not None else None,
                "Head_Roll_Deg": round(head_roll, 1) if head_roll is not None else None,
                "Head_Direction": h_dir,
                "Is_Looking_Away": int(is_away),
                "Proximity_Pct": round(prox_pct, 1) if prox_pct is not None else None,
                "Proximity_State": prox_lbl,
                "Brow_Height_Pct": round(brow_pct, 1) if brow_pct is not None else None,
                "Brow_State": brow_lbl,
                "Brow_Furrow_Pct": round(furrow_pct, 1) if furrow_pct is not None else None,
                "Furrow_State": furrow_lbl,
                "Lip_Elevation_Pct": round(delta_pct, 1) if delta_pct is not None else None,
                "Lip_State": lip_lbl,
                "Smile_Squint": int(smile_squint),
            }
            _push_metrics(metrics_outlet, row)
            last_metrics_log = current_time

        if not args.no_gui:
            cv2.imshow("Eye Tracker LSL Bridge", frame)
            if key == ord("q"):
                break
        elif calibrated and lm is None:
            time.sleep(0.01)

    cap.release()
    if not args.no_gui:
        cv2.destroyAllWindows()
    print("[eye-bridge] Stopped.")


if __name__ == "__main__":
    main()
