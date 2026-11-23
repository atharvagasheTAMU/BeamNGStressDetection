"""
Reusable NPC vehicle pool.

A fixed, small set of Vehicle instances is created once and never deleted.
NPC "spawn" becomes: acquire a free slot matching the NPC's model, teleport
it to the new track's spawn pose, apply the new AI path. NPC "despawn"
becomes: hold + teleport the vehicle back to its parking spot and mark the
slot free again.

No spawnNewVehicle / delete / connect() calls happen after startup, which
removes the per-event asset-load and socket-handshake stutter that runtime
spawn/despawn still pays every time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from beamngpy import BeamNGpy, Scenario, Vehicle

from .config import NpcNodeSpawnConfig
from .helpers import activate_npc, hold_npc


def distribute_pool_counts(pool_size: int, models: list[str]) -> dict[str, int]:
    """Split *pool_size* slots across *models* as evenly as possible.

    Remainder slots go to the first models in list order. Duplicate model
    names in *models* are merged (their counts add together).
    """
    if not models:
        raise ValueError("POOL_MODELS must not be empty")
    if pool_size < 1:
        raise ValueError(f"POOL_SIZE must be >= 1, got {pool_size}")

    base = pool_size // len(models)
    remainder = pool_size % len(models)

    counts: dict[str, int] = {}
    for i, model in enumerate(models):
        slot_count = base + (1 if i < remainder else 0)
        counts[model] = counts.get(model, 0) + slot_count
    return counts


@dataclass
class PoolSlot:
    vehicle: Vehicle
    model: str
    park_pos: tuple[float, float, float]
    park_quat: tuple[float, float, float, float]
    assigned_vid: str | None = None
    assigned_config: NpcNodeSpawnConfig | None = None


class NpcVehiclePool:
    """Fixed-size set of reusable NPC vehicles, keyed by pool slot."""

    def __init__(self, model_counts: dict[str, int], vid_prefix: str = "pool_npc") -> None:
        if not model_counts:
            raise ValueError("model_counts must not be empty")
        if any(count < 1 for count in model_counts.values()):
            zero_models = [m for m, c in model_counts.items() if c < 1]
            raise ValueError(
                f"Each model needs at least one pool slot; got zero for: {zero_models}"
            )

        self.model_counts = dict(model_counts)
        self.pool_size = sum(self.model_counts.values())
        self.vid_prefix = vid_prefix
        self.slots: list[PoolSlot] = []

    @classmethod
    def from_models(
        cls,
        pool_size: int,
        models: list[str],
        vid_prefix: str = "pool_npc",
    ) -> "NpcVehiclePool":
        """Build a pool from POOL_SIZE and a POOL_MODELS list."""
        return cls(distribute_pool_counts(pool_size, models), vid_prefix=vid_prefix)

    def add_to_scenario(
        self,
        scenario: Scenario,
        park_positions: list[tuple[float, float, float]],
        park_quat: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    ) -> None:
        if len(park_positions) != self.pool_size:
            raise ValueError(
                f"Expected {self.pool_size} park positions, got {len(park_positions)}"
            )

        slot_index = 0
        for model, count in self.model_counts.items():
            for _ in range(count):
                pos = park_positions[slot_index]
                vid = f"{self.vid_prefix}_{slot_index}"
                vehicle = Vehicle(vid, model=model, license="NPC")
                scenario.add_vehicle(vehicle, pos=pos, rot_quat=park_quat, cling=True)
                self.slots.append(
                    PoolSlot(
                        vehicle=vehicle,
                        model=model,
                        park_pos=pos,
                        park_quat=park_quat,
                    )
                )
                slot_index += 1

    def connect_and_hold(self, beamng: BeamNGpy, batch_delay_s: float = 0.15) -> None:
        """Connect + hold every pool vehicle. Runs once at startup only."""
        for slot in self.slots:
            slot.vehicle.connect(beamng)
            hold_npc(slot.vehicle)
            if batch_delay_s > 0:
                time.sleep(batch_delay_s)

    def acquire(self, model: str) -> PoolSlot | None:
        """Return a free slot for *model*, or None if all are in use."""
        for slot in self.slots:
            if slot.model == model and slot.assigned_vid is None:
                return slot
        return None

    def slot_count(self, model: str) -> int:
        return self.model_counts.get(model, 0)

    def in_use_count(self, model: str | None = None) -> int:
        if model is None:
            return sum(1 for slot in self.slots if slot.assigned_vid is not None)
        return sum(
            1
            for slot in self.slots
            if slot.model == model and slot.assigned_vid is not None
        )

    def assign(
        self,
        slot: PoolSlot,
        config: NpcNodeSpawnConfig,
        spawn_pos: tuple[float, float, float],
        spawn_quat: tuple[float, float, float, float],
    ) -> None:
        """Reassign a free slot's vehicle to a new NPC config in place."""
        try:
            slot.vehicle.teleport(spawn_pos, rot_quat=spawn_quat, reset=True)
        except TypeError:
            slot.vehicle.teleport(spawn_pos, reset=True)
        slot.assigned_vid = config.vid
        slot.assigned_config = config

    def activate(
        self,
        slot: PoolSlot,
        npc_track: list[dict],
        spawn_point: int,
        display_vid: str,
    ) -> None:
        activate_npc(slot.vehicle, npc_track, spawn_point, display_vid)

    def release(self, slot: PoolSlot) -> None:
        """Hold the vehicle and return it to its parking spot; free the slot."""
        hold_npc(slot.vehicle)
        try:
            slot.vehicle.teleport(slot.park_pos, rot_quat=slot.park_quat, reset=True)
        except TypeError:
            slot.vehicle.teleport(slot.park_pos, reset=True)
        slot.assigned_vid = None
        slot.assigned_config = None
