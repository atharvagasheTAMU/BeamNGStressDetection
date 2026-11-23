"""
Monitor for the reusable NPC vehicle pool.

Trigger -> acquire a free pool slot, teleport it to the new config's spawn
pose, apply the AI path. Despawn node -> release the slot (hold + teleport
back to its parking spot) so it can be reused by the next queued trigger.

Call tick(ego_pos) from your own loop (the stress demo does this), or
run_forever() for a standalone NPC-only loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from beamngpy import BeamNGpy, Vehicle

from ..mode_switch_demo import poll_position
from .config import NpcNodeSpawnConfig
from .helpers import (
    advance_track_progress,
    ego_reached_node,
    get_npc_positions,
    npc_reached_despawn_node,
    pose_at_index,
)
from .vehicle_pool import NpcVehiclePool, PoolSlot


@dataclass
class NpcVehiclePoolMonitor:
    beamng: BeamNGpy
    ego: Vehicle
    pool: NpcVehiclePool
    # Track the NPC ego_trigger_node numbers refer to (NOT necessarily the
    # track the ego is currently driving).
    main_track: list[dict]
    npc_tracks: dict[str, list[dict]]
    npc_config_by_vid: dict[str, NpcNodeSpawnConfig]
    poll_interval_s: float = 0.1
    node_reach_radius: float = 15.0
    verbose: bool = False

    # teleport() + ai.set_script() are engine-side calls that aren't free
    # (physics reset + terrain-cling raycasts along the whole path). Cap how
    # many assigns/releases happen per tick so the cost is spread across
    # ticks instead of stacked (FPS drop).
    max_assigns_per_tick: int = 1
    max_releases_per_tick: int = 1

    # Several NPC configs can share the same track + spawn point. If a new
    # one triggers while a previous occupant of that spot hasn't driven away
    # yet, teleporting on top of it overlaps collision geometry ->
    # "Instability detected" spam -> crash. Before assigning a slot we check
    # no other *active* NPC is within this radius (2D, metres) of the target
    # spawn pose; if one is, the trigger is queued and retried later.
    min_spawn_clearance_m: float = 12.0

    npc_triggered: dict[str, bool] = field(default_factory=dict)
    npc_progress: dict[str, int] = field(default_factory=dict)
    pending_triggers: list[NpcNodeSpawnConfig] = field(default_factory=list)
    active_slots: dict[str, PoolSlot] = field(default_factory=dict)
    _last_positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for config in self.npc_config_by_vid.values():
            self.npc_triggered.setdefault(config.vid, False)

    def tick(self, ego_pos: tuple[float, float, float] | None = None) -> None:
        """Run one monitor step.

        Pass *ego_pos* when the caller already polled the ego this tick (avoids
        a second sensor poll); otherwise the ego is polled here.
        """
        pos = ego_pos if ego_pos is not None else poll_position(self.beamng, self.ego)
        if pos is None:
            return

        assigns_done = self._check_triggers(pos)
        self._drain_pending_triggers(self.max_assigns_per_tick - assigns_done)
        self._track_active_npcs()

    def run_forever(self) -> None:
        try:
            while True:
                self.tick()
                time.sleep(self.poll_interval_s)
        except KeyboardInterrupt:
            print("\nStopped by user.", flush=True)

    def _check_triggers(self, pos: tuple[float, float, float]) -> int:
        """Detect newly-triggered NPCs. Returns how many were assigned a
        pool slot this tick (capped at max_assigns_per_tick)."""
        assigns_done = 0
        for config in self.npc_config_by_vid.values():
            if self.npc_triggered.get(config.vid, False):
                continue

            trigger_idx = config.ego_trigger_node - 1
            if not ego_reached_node(pos, self.main_track, trigger_idx, self.node_reach_radius):
                continue

            self.npc_triggered[config.vid] = True
            assigns_done += self._activate_or_queue(config, assigns_done)

        return assigns_done

    def _pool_usage_line(self, model: str) -> str:
        return (
            f"{self.pool.in_use_count(model)}/{self.pool.slot_count(model)} "
            f"{model} in use, "
            f"{self.pool.in_use_count()}/{self.pool.pool_size} total"
        )

    def _activate_or_queue(self, config: NpcNodeSpawnConfig, assigns_done: int) -> int:
        if assigns_done >= self.max_assigns_per_tick:
            self.pending_triggers.append(config)
            print(
                f"  [TRIGGER] Ego reached node {config.ego_trigger_node}, "
                f"{config.vid} QUEUED (per-tick cap reached, "
                f"{len(self.pending_triggers)} queued)",
                flush=True,
            )
            return 0

        blocker = self._spawn_blocked_by(config)
        if blocker is not None:
            self.pending_triggers.append(config)
            print(
                f"  [TRIGGER] Ego reached node {config.ego_trigger_node}, "
                f"{config.vid} QUEUED (spawn point occupied by {blocker}, "
                f"{len(self.pending_triggers)} queued)",
                flush=True,
            )
            return 0

        slot = self.pool.acquire(config.npc_model)
        if slot is None:
            self.pending_triggers.append(config)
            print(
                f"  [TRIGGER] Ego reached node {config.ego_trigger_node}, "
                f"{config.vid} WAITING for a {config.npc_model} pool slot "
                f"({self._pool_usage_line(config.npc_model)}, "
                f"{len(self.pending_triggers)} queued)",
                flush=True,
            )
            return 0

        self._assign_slot(slot, config)
        print(
            f"  [TRIGGER] Ego reached node {config.ego_trigger_node}, "
            f"{config.vid} took slot {slot.vehicle.vid} ({slot.model}) "
            f"({self._pool_usage_line(config.npc_model)})",
            flush=True,
        )
        return 1

    def _spawn_pose_for(
        self, config: NpcNodeSpawnConfig
    ) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        return pose_at_index(self.npc_tracks[config.vid], config.npc_spawn_point - 1)

    def _spawn_blocked_by(self, config: NpcNodeSpawnConfig) -> str | None:
        """Return the vid of an active NPC occupying *config*'s spawn point
        (within min_spawn_clearance_m), or None if the spot is clear."""
        spawn_pos, _ = self._spawn_pose_for(config)
        sx, sy = spawn_pos[0], spawn_pos[1]
        for active_vid, slot in self.active_slots.items():
            pos = self._last_positions.get(slot.vehicle.vid)
            if pos is None:
                continue
            dx = pos[0] - sx
            dy = pos[1] - sy
            if (dx * dx + dy * dy) ** 0.5 <= self.min_spawn_clearance_m:
                return active_vid
        return None

    def _assign_slot(self, slot: PoolSlot, config: NpcNodeSpawnConfig) -> None:
        spawn_pos, spawn_quat = self._spawn_pose_for(config)
        self.pool.assign(slot, config, spawn_pos, spawn_quat)
        self.pool.activate(
            slot, self.npc_tracks[config.vid], config.npc_spawn_point, config.vid
        )
        self.active_slots[config.vid] = slot
        self.npc_progress[config.vid] = config.npc_spawn_point - 1
        # The vehicle is now at spawn_pos; seed its tracked position so a
        # trigger that fires on the very next tick (before _track_active_npcs
        # has a fresh reading) still sees this spot as occupied.
        self._last_positions[slot.vehicle.vid] = spawn_pos

    def _drain_pending_triggers(self, budget: int) -> int:
        """Assign at most `budget` queued triggers this tick."""
        assigns_done = 0
        skipped: list[NpcNodeSpawnConfig] = []
        while self.pending_triggers and assigns_done < budget:
            config = self.pending_triggers[0]

            blocker = self._spawn_blocked_by(config)
            if blocker is not None:
                # Still occupied — retry next tick, but don't block other
                # queued triggers behind it forever.
                self.pending_triggers.pop(0)
                skipped.append(config)
                continue

            slot = self.pool.acquire(config.npc_model)
            if slot is None:
                break
            self.pending_triggers.pop(0)
            self._assign_slot(slot, config)
            assigns_done += 1
            print(
                f"  [SPAWN] Slot freed, {config.vid} took slot {slot.vehicle.vid} "
                f"({slot.model}) ({self._pool_usage_line(config.npc_model)}, "
                f"{len(self.pending_triggers)} still queued)",
                flush=True,
            )
        if skipped:
            self.pending_triggers[:0] = skipped
        return assigns_done

    def _track_active_npcs(self) -> None:
        if not self.active_slots:
            return

        physical_vehicles = {
            slot.vehicle.vid: slot.vehicle for slot in self.active_slots.values()
        }
        positions = get_npc_positions(
            self.beamng, list(physical_vehicles.keys()), physical_vehicles
        )
        self._last_positions.update(positions)

        releases_done = 0
        for vid in list(self.active_slots.keys()):
            slot = self.active_slots[vid]
            config = self.npc_config_by_vid[vid]
            if config.npc_despawn_node <= 0:
                continue

            npc_pos = positions.get(slot.vehicle.vid)
            if npc_pos is None:
                continue

            track = self.npc_tracks[vid]
            progress = advance_track_progress(
                npc_pos, track, self.npc_progress.get(vid, config.npc_spawn_point - 1)
            )
            self.npc_progress[vid] = progress

            if not npc_reached_despawn_node(progress, config.npc_despawn_node):
                continue

            if releases_done >= self.max_releases_per_tick:
                # Release budget used up; picked up again next tick (harmless
                # to keep driving one more tick).
                continue

            self.pool.release(slot)
            del self.active_slots[vid]
            self.npc_progress.pop(vid, None)
            releases_done += 1
            print(
                f"  [DESPAWN] {vid} reached node {config.npc_despawn_node}, "
                f"released {slot.vehicle.vid} back to pool",
                flush=True,
            )
