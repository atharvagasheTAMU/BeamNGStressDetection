"""
Configuration for NPC traffic (Southern Highway scenario).

Everything you normally tweak for NPC traffic lives here:
  - level / track file locations
  - NPC_SPAWNS (which NPC appears when the ego reaches which main-track node)
  - NPC vehicle pool size / models / parking layout

Ported from beamngcode/despawn.py + beamngcode/despawn_vehicle_pool.py so this
project stays standalone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Map / level
# ---------------------------------------------------------------------------

LEVEL_NAME = "SouthernHighway"
SCENARIO_NAME = "mode_switch_stress_npc_demo"

# ---------------------------------------------------------------------------
# Track files (copied into beamng_demo/tracks/southern_highway/)
# ---------------------------------------------------------------------------

TRACKS_DIR = Path(__file__).resolve().parent.parent / "tracks" / "southern_highway"

# Road the NPC trigger nodes are numbered against (1-based node numbers in
# NPC_SPAWNS.ego_trigger_node refer to THIS file).
MAIN_TRACK_PATH = TRACKS_DIR / "TNmapedited7.json"

NPC1_TRACK_PATH = TRACKS_DIR / "NPC1.json"
NPC2_TRACK_PATH = TRACKS_DIR / "NPC2.json"
NPC3_TRACK_PATH = TRACKS_DIR / "NPC3.json"
NPC4_TRACK_PATH = TRACKS_DIR / "NPC4.json"
NPC6_TRACK_PATH = TRACKS_DIR / "NPC6.json"
NPC1_HIGHWAY_PATH = TRACKS_DIR / "NPCinitialhighway1.json"
NPC2_HIGHWAY_PATH = TRACKS_DIR / "NPCinitialhighway2.json"
NPC1_MAINHIGHWAY = TRACKS_DIR / "oncomingnpchighway1.json"
NPC2_MAINHIGHWAY = TRACKS_DIR / "oncomingnpchighway2.json"
NPC_CRAZY = TRACKS_DIR / "NPCcrazy.json"

# ---------------------------------------------------------------------------
# General settings
# ---------------------------------------------------------------------------

DEFAULT_SPEED_MPS = 10.0       # fallback if an NPC track node has no "v"
NODE_REACH_RADIUS = 15.0       # metres; ego within this of a node = "reached"
NPC_PROGRESS_WINDOW = 30       # forward node window for NPC progress search
VERBOSE_DEBUG = False

# Debug colours for NPC route polylines (only drawn when the demo enables it)
NPC_TRACK_RGBA = (0.95, 0.25, 0.95, 0.9)

# ---------------------------------------------------------------------------
# NPC pool settings
# ---------------------------------------------------------------------------

# Reusable vehicle count. Size it to the max number of NPCs you expect to be
# active/overlapping at once. Larger = fewer "WAITING for a pool slot" delays
# but a heavier one-time startup cost.
POOL_SIZE = 5

# Vehicle models pre-created in the pool. POOL_SIZE slots are split across
# these as evenly as possible (remainder goes to the first models).
# Every npc_model used in NPC_SPAWNS must appear here.
POOL_MODELS = ["template_car", "etk800", "bastion"]

# Idle pool vehicles are parked laterally offset from the trigger track's
# start point (off the drivable path, on loaded terrain).
PARK_LATERAL_OFFSET_M = 25.0
PARK_SPACING_M = 5.0

# Startup-only pacing while connecting the pool (never paid again mid-drive).
POOL_CONNECT_DELAY_S = 0.15

# teleport() + ai.set_script() are not free. Cap how many NPCs are assigned /
# released per monitor tick; extras are deferred to later ticks.
MAX_ASSIGNS_PER_TICK = 1
MAX_RELEASES_PER_TICK = 1


# ---------------------------------------------------------------------------
# NPC spawn configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NpcNodeSpawnConfig:
    """
    Configuration for one NPC.

    Parameters:
        vid:              Unique NPC id string
        npc_track_path:   Path to the NPC's route JSON file
        ego_trigger_node: Node NUMBER (1-based) on the TRIGGER track
                          (MAIN_TRACK_PATH) that activates this NPC
        npc_spawn_point:  Node NUMBER (1-based) on the NPC's OWN track
                          where it starts driving from
        npc_model:        BeamNG model name (must be in POOL_MODELS)
        npc_despawn_node: Node NUMBER (1-based) on the NPC's own track at
                          which it is released back to the pool (<=0 = never)
        track_rgba:       Debug polyline colour
    """

    vid: str
    npc_track_path: Path
    ego_trigger_node: int
    npc_spawn_point: int = 1
    npc_model: str = "etk800"
    npc_despawn_node: int = -1
    track_rgba: tuple = NPC_TRACK_RGBA


NPC_SPAWNS: tuple[NpcNodeSpawnConfig, ...] = (
    NpcNodeSpawnConfig("npc4", NPC4_TRACK_PATH, ego_trigger_node=42, npc_spawn_point=2, npc_model="template_car", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc1", NPC1_TRACK_PATH, ego_trigger_node=1, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=13),
    NpcNodeSpawnConfig("npc2", NPC2_TRACK_PATH, ego_trigger_node=11, npc_spawn_point=2, npc_model="bastion", npc_despawn_node=15),
    NpcNodeSpawnConfig("npc3", NPC3_TRACK_PATH, ego_trigger_node=40, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc6", NPC6_TRACK_PATH, ego_trigger_node=53, npc_spawn_point=2, npc_model="etk800", npc_despawn_node=55),
    NpcNodeSpawnConfig("npc8", NPC1_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc9", NPC1_HIGHWAY_PATH, ego_trigger_node=20, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc10", NPC1_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc11", NPC1_HIGHWAY_PATH, ego_trigger_node=40, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc17", NPC2_HIGHWAY_PATH, ego_trigger_node=19, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc18", NPC2_HIGHWAY_PATH, ego_trigger_node=25, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc19", NPC2_HIGHWAY_PATH, ego_trigger_node=35, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc20", NPC2_HIGHWAY_PATH, ego_trigger_node=45, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc21", NPC2_HIGHWAY_PATH, ego_trigger_node=55, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc26", NPC1_HIGHWAY_PATH, ego_trigger_node=10, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc27", NPC1_HIGHWAY_PATH, ego_trigger_node=20, npc_spawn_point=33, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc28", NPC1_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=33, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc28V2", NPC1_HIGHWAY_PATH, ego_trigger_node=35, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc29", NPC2_HIGHWAY_PATH, ego_trigger_node=5, npc_spawn_point=27, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc30", NPC1_HIGHWAY_PATH, ego_trigger_node=2, npc_spawn_point=42, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc31", NPC2_HIGHWAY_PATH, ego_trigger_node=2, npc_spawn_point=39, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc32", NPC1_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=42, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc33", NPC2_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=39, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc35", NPC2_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc36", NPC2_HIGHWAY_PATH, ego_trigger_node=25, npc_spawn_point=27, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc36v2", NPC2_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=27, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npccrazy", NPC_CRAZY, ego_trigger_node=18, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc37", NPC1_MAINHIGHWAY, ego_trigger_node=30, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc38", NPC1_MAINHIGHWAY, ego_trigger_node=32, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc39", NPC1_MAINHIGHWAY, ego_trigger_node=35, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc40", NPC1_MAINHIGHWAY, ego_trigger_node=39, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc41", NPC1_MAINHIGHWAY, ego_trigger_node=40, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc42", NPC1_MAINHIGHWAY, ego_trigger_node=42, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc43", NPC1_MAINHIGHWAY, ego_trigger_node=45, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc44", NPC1_MAINHIGHWAY, ego_trigger_node=47, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc45", NPC1_MAINHIGHWAY, ego_trigger_node=49, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc46", NPC1_MAINHIGHWAY, ego_trigger_node=51, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc47", NPC1_MAINHIGHWAY, ego_trigger_node=52, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc48", NPC1_MAINHIGHWAY, ego_trigger_node=55, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc49", NPC1_MAINHIGHWAY, ego_trigger_node=63, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc50", NPC1_MAINHIGHWAY, ego_trigger_node=65, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc51", NPC1_MAINHIGHWAY, ego_trigger_node=73, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc52", NPC1_MAINHIGHWAY, ego_trigger_node=75, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc53", NPC1_MAINHIGHWAY, ego_trigger_node=82, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc54", NPC1_MAINHIGHWAY, ego_trigger_node=87, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc57", NPC2_MAINHIGHWAY, ego_trigger_node=30, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=40),
    NpcNodeSpawnConfig("npc58", NPC2_MAINHIGHWAY, ego_trigger_node=32, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc59", NPC2_MAINHIGHWAY, ego_trigger_node=35, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc60", NPC2_MAINHIGHWAY, ego_trigger_node=39, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc61", NPC2_MAINHIGHWAY, ego_trigger_node=40, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc62", NPC2_MAINHIGHWAY, ego_trigger_node=42, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc63", NPC2_MAINHIGHWAY, ego_trigger_node=45, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc64", NPC2_MAINHIGHWAY, ego_trigger_node=47, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc65", NPC2_MAINHIGHWAY, ego_trigger_node=49, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc66", NPC2_MAINHIGHWAY, ego_trigger_node=51, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc67", NPC2_MAINHIGHWAY, ego_trigger_node=52, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc68", NPC2_MAINHIGHWAY, ego_trigger_node=55, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc69", NPC2_MAINHIGHWAY, ego_trigger_node=63, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc70", NPC2_MAINHIGHWAY, ego_trigger_node=65, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc71", NPC2_MAINHIGHWAY, ego_trigger_node=73, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc72", NPC2_MAINHIGHWAY, ego_trigger_node=75, npc_spawn_point=1, npc_model="template_car", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc73", NPC2_MAINHIGHWAY, ego_trigger_node=82, npc_spawn_point=1, npc_model="etk800", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc74", NPC2_MAINHIGHWAY, ego_trigger_node=87, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=42),
)
