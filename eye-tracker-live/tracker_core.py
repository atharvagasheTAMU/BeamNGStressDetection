"""
Metric helpers and MediaPipe models for eye-tracker LSL streaming.

Logic aligned with EyeTracker/eye_tracker2.py (not imported from that folder).
"""

from __future__ import annotations

import math
import os
import sys
import warnings

warnings.filterwarnings("ignore")

os.environ["GLOG_minloglevel"] = "3"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["ABSL_MIN_LOG_LEVEL"] = "3"

import cv2
import mediapipe as mp
import numpy as np

if not hasattr(mp, "solutions"):
    raise ImportError(
        "mediapipe>=1.0 removed mp.solutions (FaceMesh/Pose). "
        "Install the EyeTracker stack: pip install mediapipe==0.10.14 "
        "(see eye-tracker-live/requirements.txt)."
    )

mp_face_mesh = mp.solutions.face_mesh
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils

_old_fd2 = os.dup(2)
_devnull_fd = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull_fd, 2)
os.close(_devnull_fd)
_old_py_err, sys.stderr = sys.stderr, open(os.devnull, "w")

face_mesh = mp_face_mesh.FaceMesh(refine_landmarks=True)
pose = mp_pose.Pose()
_dummy = np.zeros((100, 100, 3), dtype=np.uint8)
face_mesh.process(_dummy)
pose.process(_dummy)

sys.stderr.close()
sys.stderr = _old_py_err
os.dup2(_old_fd2, 2)
os.close(_old_fd2)

FACE_3D_MODEL = np.array(
    [
        [0.0, 0.0, 0.0],
        [0.0, -330.0, -65.0],
        [-225.0, 170.0, -135.0],
        [225.0, 170.0, -135.0],
        [-150.0, -150.0, -125.0],
        [150.0, -150.0, -125.0],
    ],
    dtype=np.float64,
)
FACE_LM_INDICES = [1, 152, 263, 33, 287, 57]

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]


def distance(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def get_head_pose(landmarks, image_w, image_h):
    img_pts = np.array(
        [[landmarks[i].x * image_w, landmarks[i].y * image_h] for i in FACE_LM_INDICES],
        dtype=np.float64,
    )
    focal = float(image_w)
    cam = np.array(
        [
            [focal, 0, image_w / 2.0],
            [0, focal, image_h / 2.0],
            [0, 0, 1.0],
        ],
        dtype=np.float64,
    )
    dist = np.zeros((4, 1), dtype=np.float64)
    ok, rvec, _ = cv2.solvePnP(
        FACE_3D_MODEL, img_pts, cam, dist, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return None, None, None
    rmat, _ = cv2.Rodrigues(rvec)
    sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
    if sy > 1e-6:
        pitch = math.degrees(math.atan2(rmat[2, 1], rmat[2, 2]))
        yaw = math.degrees(math.atan2(-rmat[2, 0], sy))
        roll = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))
    else:
        pitch = math.degrees(math.atan2(-rmat[1, 2], rmat[1, 1]))
        yaw = math.degrees(math.atan2(-rmat[2, 0], sy))
        roll = 0.0
    return yaw, pitch, roll


def get_iod_pixels(landmarks, image_w, image_h):
    p1 = (landmarks[33].x * image_w, landmarks[33].y * image_h)
    p2 = (landmarks[263].x * image_w, landmarks[263].y * image_h)
    return distance(p1, p2)


def get_eye_openness(landmarks, eye_indices, image_w, image_h):
    p = [(int(landmarks[i].x * image_w), int(landmarks[i].y * image_h)) for i in eye_indices]
    horizontal = distance(p[0], p[3])
    if horizontal == 0:
        return 0.0
    ear = (distance(p[1], p[5]) + distance(p[2], p[4])) / (2.0 * horizontal)
    return max(0.0, min((ear / 0.35) * 100, 100.0))


def get_shoulder_width_pixels(pose_landmarks, image_w, image_h):
    ls = pose_landmarks.landmark[mp_pose.PoseLandmark.LEFT_SHOULDER]
    rs = pose_landmarks.landmark[mp_pose.PoseLandmark.RIGHT_SHOULDER]
    if ls.visibility < 0.5 or rs.visibility < 0.5:
        return None
    return distance((ls.x * image_w, ls.y * image_h), (rs.x * image_w, rs.y * image_h))


def compute_proximity_ratio(current_iod, baseline_iod, current_shoulder, baseline_shoulder):
    iod_r = (current_iod / baseline_iod) if (baseline_iod and current_iod) else None
    sho_r = (
        (current_shoulder / baseline_shoulder)
        if (current_shoulder and baseline_shoulder)
        else None
    )
    if iod_r is not None and sho_r is not None:
        return (iod_r + sho_r) / 2.0
    return iod_r


def get_brow_height(landmarks, image_w, image_h):
    iod = get_iod_pixels(landmarks, image_w, image_h)
    if iod == 0:
        return None
    r_brow = (landmarks[52].x * image_w, landmarks[52].y * image_h)
    r_iris = (landmarks[468].x * image_w, landmarks[468].y * image_h)
    l_brow = (landmarks[282].x * image_w, landmarks[282].y * image_h)
    l_iris = (landmarks[473].x * image_w, landmarks[473].y * image_h)
    return (distance(r_brow, r_iris) + distance(l_brow, l_iris)) / (2.0 * iod)


def get_brow_furrow(landmarks, image_w, image_h):
    iod = get_iod_pixels(landmarks, image_w, image_h)
    if iod == 0:
        return None
    p1 = (landmarks[107].x * image_w, landmarks[107].y * image_h)
    p2 = (landmarks[336].x * image_w, landmarks[336].y * image_h)
    return distance(p1, p2) / iod


def get_lip_corner_elevation(landmarks, image_w, image_h):
    iod = get_iod_pixels(landmarks, image_w, image_h)
    if iod == 0:
        return None
    lip_center_y = landmarks[13].y * image_h
    r_corner_y = landmarks[61].y * image_h
    l_corner_y = landmarks[291].y * image_h
    right_elev = (lip_center_y - r_corner_y) / iod
    left_elev = (lip_center_y - l_corner_y) / iod
    return (right_elev + left_elev) / 2.0


def get_gaze_direction(landmarks):
    r_width = abs(landmarks[133].x - landmarks[33].x)
    l_width = abs(landmarks[263].x - landmarks[362].x)
    if r_width < 1e-6 or l_width < 1e-6:
        return None, "unknown"
    r_ratio = (landmarks[468].x - landmarks[33].x) / r_width
    l_ratio = (landmarks[473].x - landmarks[362].x) / l_width
    avg = (r_ratio + l_ratio) / 2.0
    label = "left" if avg < 0.42 else "right" if avg > 0.58 else "center"
    return round(avg, 3), label


def get_gaze_vertical(landmarks):
    r_height = abs(landmarks[145].y - landmarks[159].y)
    l_height = abs(landmarks[374].y - landmarks[386].y)
    if r_height < 1e-6 or l_height < 1e-6:
        return None, "unknown"
    r_ratio = (landmarks[468].y - landmarks[159].y) / r_height
    l_ratio = (landmarks[473].y - landmarks[386].y) / l_height
    avg = (r_ratio + l_ratio) / 2.0
    label = "up" if avg < 0.42 else "down" if avg > 0.58 else "center"
    return round(avg, 3), label
