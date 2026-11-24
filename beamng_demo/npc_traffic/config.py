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

# Ego driving tracks per stress mode. All three follow the same road with the
# same node count/ordering (426 nodes), only the speed profile differs, so the
# mode switch can map the current node index across tracks.
NEUTRAL_TRACK_PATH = TRACKS_DIR / "TNmapedited7.json"
AGGRESSIVE_TRACK_PATH = TRACKS_DIR / "aggressive1.json"
DEFENSIVE_TRACK_PATH = TRACKS_DIR / "Passive1.json"

# Road the NPC trigger nodes are numbered against (1-based node numbers in
# NPC_SPAWNS.ego_trigger_node refer to THIS file).
MAIN_TRACK_PATH = AGGRESSIVE_TRACK_PATH

# NPC route files
NPC1_TRACK_PATH = TRACKS_DIR / "NPC1.json"
NPC2_TRACK_PATH = TRACKS_DIR / "NPC2.json"
NPC3_TRACK_PATH = TRACKS_DIR / "NPC3.json"
NPC6_TRACK_PATH = TRACKS_DIR / "NPC6.json"
TRUCK = TRACKS_DIR / "NPCtruck.json"
NPC1_HIGHWAY_PATH = TRACKS_DIR / "NPCinitialhighway1.json"
NPC2_HIGHWAY_PATH = TRACKS_DIR / "NPCinitialhighway2.json"
NPC_CRAZY = TRACKS_DIR / "NPCcrazy.json"
NPC1_MAINHIGHWAY = TRACKS_DIR / "oncomingnpchighway1.json"
NPC2_MAINHIGHWAY = TRACKS_DIR / "oncomingnpchighway2.json"
NPC1_TOSUBURB = TRACKS_DIR / "NPCtosuburb1.json"
NPC1_ONCOMINGTOSUBURB = TRACKS_DIR / "oncomingtosuburbnpc1.json"
NPC2_ONCOMINGTOSUBURB = TRACKS_DIR / "oncomingtosuburbnpc2.json"
NPC3_ONCOMINGTOSUBURB = TRACKS_DIR / "oncomingtosuburbnpc3.json"
STATIONARY1 = TRACKS_DIR / "stationary1.json"
TAILGATE = TRACKS_DIR / "tailgate.json"
WC1 = TRACKS_DIR / "WC1.json"
WC2 = TRACKS_DIR / "WC2.json"
WC3 = TRACKS_DIR / "WC3.json"
WC4 = TRACKS_DIR / "WC4.json"
WC6 = TRACKS_DIR / "WC6.json"
WC7 = TRACKS_DIR / "WC7.json"
WC8 = TRACKS_DIR / "WC8.json"
WC9 = TRACKS_DIR / "WC9.json"
OTWC = TRACKS_DIR / "OT_WC.json"

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

# TODO(lab machine): POOL_SIZE / POOL_MODELS below are sized for a low-end dev
# PC. On the lab machine (RTX 4080) raise POOL_SIZE to ~25 and tune POOL_MODELS.
# Nearly every NPC in NPC_SPAWNS is "bastion" (one is "us_semi"), and slots are
# split evenly across POOL_MODELS, so a bastion-heavy mix may want "bastion"
# repeated, e.g. ["bastion", "bastion", "bastion", "us_semi"] (duplicate names
# are merged and their counts add together).
#
# Reusable vehicle count. Size it to the max number of NPCs you expect to be
# active/overlapping at once. Larger = fewer "WAITING for a pool slot" delays
# but a heavier one-time startup cost.
POOL_SIZE = 5

# Vehicle models pre-created in the pool. POOL_SIZE slots are split across
# these as evenly as possible (remainder goes to the first models).
# Every npc_model used in NPC_SPAWNS must appear here.
POOL_MODELS = ["bastion", "us_semi"]

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
    npc_model: str = "bastion"
    npc_despawn_node: int = -1
    track_rgba: tuple = NPC_TRACK_RGBA


NPC_SPAWNS: tuple[NpcNodeSpawnConfig, ...] = (
    NpcNodeSpawnConfig("npc1", NPC1_TRACK_PATH, ego_trigger_node=1, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=13),
    NpcNodeSpawnConfig("npc2", NPC2_TRACK_PATH, ego_trigger_node=11, npc_spawn_point=2, npc_model="bastion", npc_despawn_node=15),
    NpcNodeSpawnConfig("npc3", NPC3_TRACK_PATH, ego_trigger_node=40, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc6", NPC6_TRACK_PATH, ego_trigger_node=53, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=55),
    NpcNodeSpawnConfig("npc7", TRUCK, ego_trigger_node=66, npc_spawn_point=1, npc_model="us_semi", npc_despawn_node=34),
    NpcNodeSpawnConfig("npc8", NPC1_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc9", NPC1_HIGHWAY_PATH, ego_trigger_node=20, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc10", NPC1_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc11", NPC1_HIGHWAY_PATH, ego_trigger_node=40, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc17", NPC2_HIGHWAY_PATH, ego_trigger_node=19, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc18", NPC2_HIGHWAY_PATH, ego_trigger_node=25, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc19", NPC2_HIGHWAY_PATH, ego_trigger_node=35, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc20", NPC2_HIGHWAY_PATH, ego_trigger_node=45, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc21", NPC2_HIGHWAY_PATH, ego_trigger_node=55, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc26", NPC1_HIGHWAY_PATH, ego_trigger_node=10, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc27", NPC1_HIGHWAY_PATH, ego_trigger_node=20, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc28", NPC1_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc28V2", NPC1_HIGHWAY_PATH, ego_trigger_node=35, npc_spawn_point=33, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc29", NPC2_HIGHWAY_PATH, ego_trigger_node=5, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc30", NPC1_HIGHWAY_PATH, ego_trigger_node=2, npc_spawn_point=42, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc31", NPC2_HIGHWAY_PATH, ego_trigger_node=2, npc_spawn_point=39, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc32", NPC1_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=42, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc33", NPC2_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=39, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc35", NPC2_HIGHWAY_PATH, ego_trigger_node=15, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc36", NPC2_HIGHWAY_PATH, ego_trigger_node=25, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc36v2", NPC2_HIGHWAY_PATH, ego_trigger_node=30, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npccrazy", NPC_CRAZY, ego_trigger_node=18, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc37", NPC1_MAINHIGHWAY, ego_trigger_node=90, npc_spawn_point=42, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc38", NPC1_MAINHIGHWAY, ego_trigger_node=98, npc_spawn_point=40, npc_model="bastion", npc_despawn_node=44),
    NpcNodeSpawnConfig("npc39", NPC1_MAINHIGHWAY, ego_trigger_node=103, npc_spawn_point=35, npc_model="bastion", npc_despawn_node=39),
    NpcNodeSpawnConfig("npc40", NPC1_MAINHIGHWAY, ego_trigger_node=107, npc_spawn_point=32, npc_model="bastion", npc_despawn_node=35),
    NpcNodeSpawnConfig("npc41", NPC1_MAINHIGHWAY, ego_trigger_node=114, npc_spawn_point=28, npc_model="bastion", npc_despawn_node=32),
    NpcNodeSpawnConfig("npc42", NPC1_MAINHIGHWAY, ego_trigger_node=119, npc_spawn_point=23, npc_model="bastion", npc_despawn_node=27),
    NpcNodeSpawnConfig("npc43", NPC1_MAINHIGHWAY, ego_trigger_node=125, npc_spawn_point=17, npc_model="bastion", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc44", NPC1_MAINHIGHWAY, ego_trigger_node=133, npc_spawn_point=7, npc_model="bastion", npc_despawn_node=17),
    NpcNodeSpawnConfig("npc45", NPC1_MAINHIGHWAY, ego_trigger_node=137, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=14),
    NpcNodeSpawnConfig("npc57", NPC2_MAINHIGHWAY, ego_trigger_node=88, npc_spawn_point=37, npc_model="bastion", npc_despawn_node=41),
    NpcNodeSpawnConfig("npc58", NPC2_MAINHIGHWAY, ego_trigger_node=98, npc_spawn_point=35, npc_model="bastion", npc_despawn_node=39),
    NpcNodeSpawnConfig("npc59", NPC2_MAINHIGHWAY, ego_trigger_node=102, npc_spawn_point=31, npc_model="bastion", npc_despawn_node=36),
    NpcNodeSpawnConfig("npc60", NPC2_MAINHIGHWAY, ego_trigger_node=108, npc_spawn_point=28, npc_model="bastion", npc_despawn_node=31),
    NpcNodeSpawnConfig("npc61", NPC2_MAINHIGHWAY, ego_trigger_node=114, npc_spawn_point=23, npc_model="bastion", npc_despawn_node=27),
    NpcNodeSpawnConfig("npc62", NPC2_MAINHIGHWAY, ego_trigger_node=117, npc_spawn_point=22, npc_model="bastion", npc_despawn_node=26),
    NpcNodeSpawnConfig("npc63", NPC2_MAINHIGHWAY, ego_trigger_node=121, npc_spawn_point=20, npc_model="bastion", npc_despawn_node=24),
    NpcNodeSpawnConfig("npc64", NPC2_MAINHIGHWAY, ego_trigger_node=126, npc_spawn_point=10, npc_model="bastion", npc_despawn_node=21),
    NpcNodeSpawnConfig("npc65", NPC2_MAINHIGHWAY, ego_trigger_node=132, npc_spawn_point=5, npc_model="bastion", npc_despawn_node=16),
    NpcNodeSpawnConfig("npc66", NPC2_MAINHIGHWAY, ego_trigger_node=139, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=11),
    NpcNodeSpawnConfig("npc74", NPC1_TOSUBURB, ego_trigger_node=143, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=30),
    NpcNodeSpawnConfig("npc75", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=140, npc_spawn_point=80, npc_model="bastion", npc_despawn_node=94),
    NpcNodeSpawnConfig("npc76", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=145, npc_spawn_point=80, npc_model="bastion", npc_despawn_node=91),
    NpcNodeSpawnConfig("npc77", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=150, npc_spawn_point=80, npc_model="bastion", npc_despawn_node=87),
    NpcNodeSpawnConfig("npc78", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=155, npc_spawn_point=70, npc_model="bastion", npc_despawn_node=81),
    NpcNodeSpawnConfig("npc79", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=160, npc_spawn_point=70, npc_model="bastion", npc_despawn_node=78),
    NpcNodeSpawnConfig("npc80", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=170, npc_spawn_point=60, npc_model="bastion", npc_despawn_node=73),
    NpcNodeSpawnConfig("npc81", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=180, npc_spawn_point=56, npc_model="bastion", npc_despawn_node=62),
    NpcNodeSpawnConfig("npc82", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=190, npc_spawn_point=47, npc_model="bastion", npc_despawn_node=55),
    NpcNodeSpawnConfig("npc83", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=195, npc_spawn_point=50, npc_model="bastion", npc_despawn_node=51),
    NpcNodeSpawnConfig("npc84", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=200, npc_spawn_point=40, npc_model="bastion", npc_despawn_node=46),
    NpcNodeSpawnConfig("npc85", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=209, npc_spawn_point=27, npc_model="bastion", npc_despawn_node=38),
    NpcNodeSpawnConfig("npc86", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=217, npc_spawn_point=18, npc_model="bastion", npc_despawn_node=26),
    NpcNodeSpawnConfig("npc87", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=227, npc_spawn_point=10, npc_model="bastion", npc_despawn_node=18),
    NpcNodeSpawnConfig("npc88", NPC1_ONCOMINGTOSUBURB, ego_trigger_node=235, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=11),
    NpcNodeSpawnConfig("npc89", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=144, npc_spawn_point=86, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc90", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=150, npc_spawn_point=80, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc91", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=160, npc_spawn_point=70, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc92", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=175, npc_spawn_point=57, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc93", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=187, npc_spawn_point=47, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc94", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=200, npc_spawn_point=38, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc95", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=210, npc_spawn_point=25, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc96", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=220, npc_spawn_point=17, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc97", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=230, npc_spawn_point=9, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc98", NPC2_ONCOMINGTOSUBURB, ego_trigger_node=235, npc_spawn_point=3, npc_model="bastion", npc_despawn_node=42),
    NpcNodeSpawnConfig("npc67", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=238, npc_spawn_point=59, npc_model="bastion", npc_despawn_node=72),
    NpcNodeSpawnConfig("npc68", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=253, npc_spawn_point=49, npc_model="bastion", npc_despawn_node=58),
    NpcNodeSpawnConfig("npc69", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=263, npc_spawn_point=37, npc_model="bastion", npc_despawn_node=49),
    NpcNodeSpawnConfig("npc70", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=278, npc_spawn_point=16, npc_model="bastion", npc_despawn_node=37),
    NpcNodeSpawnConfig("npc71", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=292, npc_spawn_point=12, npc_model="bastion", npc_despawn_node=27),
    NpcNodeSpawnConfig("npc72", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=302, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=15),
    NpcNodeSpawnConfig("npc73", NPC3_ONCOMINGTOSUBURB, ego_trigger_node=305, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=12),
    NpcNodeSpawnConfig("npc99", STATIONARY1, ego_trigger_node=234, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=-1),
    NpcNodeSpawnConfig("npc100", TAILGATE, ego_trigger_node=245, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=-1),
    NpcNodeSpawnConfig("npc101", WC1, ego_trigger_node=306, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=15),
    NpcNodeSpawnConfig("npc102", WC2, ego_trigger_node=321, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=6),
    NpcNodeSpawnConfig("npc103", WC3, ego_trigger_node=347, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=15),
    NpcNodeSpawnConfig("npc104", WC4, ego_trigger_node=370, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=5),
    NpcNodeSpawnConfig("npc105", WC6, ego_trigger_node=376, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=27),
    NpcNodeSpawnConfig("npc106", WC7, ego_trigger_node=390, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=9),
    NpcNodeSpawnConfig("npc107", WC8, ego_trigger_node=392, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=12),
    NpcNodeSpawnConfig("npc108", WC9, ego_trigger_node=404, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=7),
    NpcNodeSpawnConfig("npc109", OTWC, ego_trigger_node=355, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=-1),
    NpcNodeSpawnConfig("npc110", OTWC, ego_trigger_node=365, npc_spawn_point=1, npc_model="bastion", npc_despawn_node=-1),
)
