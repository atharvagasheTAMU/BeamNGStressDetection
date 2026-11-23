"""Startup helpers: park-position layout and config validation for the NPC pool."""

from __future__ import annotations

import math

from ..mode_switch_demo import heading_at_index
from .config import NPC_SPAWNS, POOL_MODELS, POOL_SIZE
from .vehicle_pool import distribute_pool_counts


def compute_park_positions(
    trigger_track: list[dict],
    pool_size: int,
    lateral_offset: float,
    spacing: float,
) -> list[tuple[float, float, float]]:
    """Park spots in a row, offset sideways from the track's first node."""
    p0 = trigger_track[0]
    heading = heading_at_index(trigger_track, 0)
    perp_x = -math.sin(heading)
    perp_y = math.cos(heading)

    positions = []
    for i in range(pool_size):
        offset = lateral_offset + i * spacing
        x = float(p0["x"]) + perp_x * offset
        y = float(p0["y"]) + perp_y * offset
        z = float(p0["z"])
        positions.append((x, y, z))
    return positions


def validate_pool_models() -> dict[str, int]:
    """Validate POOL_MODELS / POOL_SIZE and that every NPC model is covered."""
    if POOL_SIZE < len(POOL_MODELS):
        raise RuntimeError(
            f"POOL_SIZE ({POOL_SIZE}) must be >= number of POOL_MODELS "
            f"({len(POOL_MODELS)}) so each model gets at least one slot."
        )

    model_counts = distribute_pool_counts(POOL_SIZE, POOL_MODELS)
    npc_models = {config.npc_model for config in NPC_SPAWNS}
    missing = sorted(npc_models - set(model_counts))
    if missing:
        raise RuntimeError(
            f"NPC_SPAWNS use npc_model values not in POOL_MODELS: {missing}. "
            f"Add them to POOL_MODELS or change the NPC configs."
        )
    return model_counts


def print_pool_distribution(model_counts: dict[str, int]) -> None:
    print(f"  Pool: {sum(model_counts.values())} slots across {len(model_counts)} models")
    for model, count in model_counts.items():
        print(f"    {model}: {count}")


def validate_spawns(trigger_track: list[dict], npc_tracks: dict[str, list[dict]]) -> None:
    for config in NPC_SPAWNS:
        if config.ego_trigger_node < 1 or config.ego_trigger_node > len(trigger_track):
            raise RuntimeError(
                f"{config.vid}: ego_trigger_node {config.ego_trigger_node} "
                f"is out of range (trigger track has {len(trigger_track)} nodes)."
            )
        spawn_i = config.npc_spawn_point - 1
        track = npc_tracks[config.vid]
        if spawn_i >= len(track):
            raise RuntimeError(
                f"{config.vid}: npc_spawn_point {config.npc_spawn_point} "
                f"is out of range (NPC track has {len(track)} nodes)."
            )
        if config.npc_despawn_node > len(track):
            raise RuntimeError(
                f"{config.vid}: npc_despawn_node {config.npc_despawn_node} "
                f"is out of range (NPC track has {len(track)} nodes)."
            )
