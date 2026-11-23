"""
NPC helper functions (ported from beamngcode/despawn.py).

Generic geometry / track helpers (load_track, heading_at_index, apply_ai_path,
find_nearest_index, ...) are reused from beamng_demo.mode_switch_demo; only
NPC-specific helpers live here.
"""

from __future__ import annotations

import math

from beamngpy import BeamNGpy, Vehicle

from ..mode_switch_demo import apply_ai_path, find_nearest_index, heading_at_index, yaw_to_quat
from .config import NODE_REACH_RADIUS, NPC_PROGRESS_WINDOW, VERBOSE_DEBUG


def dist2d(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def pose_at_index(
    points: list[dict], index: int
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Position (z + 0.5 m drop clearance) and yaw quaternion at a track index."""
    p = points[index]
    pos = (float(p["x"]), float(p["y"]), float(p["z"]) + 0.5)
    quat = yaw_to_quat(heading_at_index(points, index))
    return pos, quat


def advance_track_progress(
    pos: tuple[float, float, float],
    points: list[dict],
    progress_hint: int,
) -> int:
    """Furthest track index reached so far (monotonic, 0-based)."""
    nearest = find_nearest_index(pos, points, hint=progress_hint, window=NPC_PROGRESS_WINDOW)
    return max(progress_hint, nearest)


def npc_reached_despawn_node(progress_idx: int, despawn_node: int) -> bool:
    """True once the NPC reached or passed a 1-based despawn node."""
    if despawn_node <= 0:
        return False
    return progress_idx >= despawn_node - 1


def ego_reached_node(
    pos: tuple[float, float, float],
    points: list[dict],
    node_index: int,
    radius: float = NODE_REACH_RADIUS,
) -> bool:
    """True if pos is within *radius* (2D) of the track node at node_index."""
    p = points[node_index]
    return dist2d(pos[0], pos[1], float(p["x"]), float(p["y"])) <= radius


def ai_set_mode(vehicle: Vehicle, mode: str) -> None:
    try:
        if hasattr(vehicle, "ai") and hasattr(vehicle.ai, "set_mode"):
            vehicle.ai.set_mode(mode)
        else:
            vehicle.ai_set_mode(mode)
    except Exception:
        pass


def hold_npc(npc: Vehicle) -> None:
    """Hold NPC in place: AI off + parking brake."""
    ai_set_mode(npc, "disabled")
    npc.control(throttle=0.0, brake=0.0, steering=0.0, parkingbrake=1.0)


def activate_npc(npc: Vehicle, npc_track: list[dict], spawn_point: int, vid: str) -> None:
    """Release the brake and start the NPC along its track from spawn_point."""
    spawn_index = spawn_point - 1
    npc.control(throttle=0.0, brake=0.0, steering=0.0, parkingbrake=0.0)
    apply_ai_path(npc, npc_track[spawn_index:])
    p = npc_track[spawn_index]
    print(
        f"[NPC] {vid} ACTIVATED at track point {spawn_point} "
        f"({float(p['x']):.1f}, {float(p['y']):.1f}, {float(p['z']):.1f})",
        flush=True,
    )


def get_npc_positions(
    beamng: BeamNGpy,
    vids: list[str],
    npcs: dict[str, Vehicle],
) -> dict[str, tuple[float, float, float]]:
    """Batch-read NPC positions; falls back per-vehicle when needed."""
    positions: dict[str, tuple[float, float, float]] = {}
    if not vids:
        return positions

    try:
        states = beamng.vehicles.get_states(vids)
        for vid in vids:
            st = states.get(vid)
            if st and "pos" in st:
                p = st["pos"]
                positions[vid] = (float(p[0]), float(p[1]), float(p[2]))
    except Exception as e:
        if VERBOSE_DEBUG:
            print(f"  [DEBUG] get_npc_positions batch error: {e}", flush=True)

    for vid in vids:
        if vid in positions:
            continue
        npc = npcs.get(vid)
        if npc is None:
            continue
        try:
            npc.sensors.poll()
            state = npc.state
            if state and "pos" in state:
                p = state["pos"]
                positions[vid] = (float(p[0]), float(p[1]), float(p[2]))
        except Exception as e:
            if VERBOSE_DEBUG:
                print(f"  [DEBUG] get_npc_positions({vid}) fallback error: {e}", flush=True)

    return positions
