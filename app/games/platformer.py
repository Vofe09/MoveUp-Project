#!/usr/bin/env python3
"""MoveUp — Elemental Run: cooperative two-character platformer.

Controls
--------
Fire: A / D to move, W to jump, or the left stick.
Water: Left / Right arrows to move, Up to jump, or the right stick.
Controller select: Up/Down or stick vertical to choose a HID controller; Enter confirms.
Level select: A/D or Left/Right to choose, Enter to start, 1/2/3 quick-select.
In-game: R restarts; Esc returns to level select; F1 prints HID axis values.
Win screen: R replay, L level select, N next level, Enter next level.

The joystick axis mapping intentionally matches the supplied shooter.py:
left stick  = X axis 2, Y axis 3; right stick = X axis 0, Y axis 1.
"""

import math
import random
import sys
import time
import atexit
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import pygame

_scriptDirectory = Path(__file__).resolve().parent
for _candidateDirectory in (_scriptDirectory, *_scriptDirectory.parents):
    if (_candidateDirectory / "moveup_stats.py").exists():
        sys.path.insert(0, str(_candidateDirectory))
        break
from moveup_stats import MoveUpStatistics, metric

moveUpStatistics = MoveUpStatistics(
    "Elemental Run", "platformer", sys.executable if getattr(sys, "frozen", False) else __file__
)

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_WIDTH = 900
BASE_HEIGHT = 650
FPS = 60
WORLD_WIDTH = 2960
GROUND_Y = 560
HUD_HEIGHT = 76

PLAYER_WIDTH = 34
PLAYER_HEIGHT = 46
PLAYER_SPEED = 260.0
PLAYER_ACCELERATION = 1750.0
PLAYER_FRICTION = 2050.0
JUMP_SPEED = 510.0
GRAVITY = 1450.0
MAX_FALL_SPEED = 700.0
AXIS_DEADZONE = 0.18
JUMP_AXIS_THRESHOLD = -0.62
JUMP_BUFFER_TIME = 0.14
COYOTE_TIME = 0.11

JOYSTICK_AXES = {
    "fire": {"x": 2, "y": 3},
    "water": {"x": 0, "y": 1},
}

# Neon ruin palette: graphite blue with distinct warm/cool character accents.
COLOR_BG_DARK = (8, 12, 29)
COLOR_BG_LIGHT = (20, 34, 64)
COLOR_GLASS = (13, 23, 46)
COLOR_TEXT = (241, 248, 255)
COLOR_MUTED = (143, 164, 199)
COLOR_NEON_BLUE = (0, 224, 255)
COLOR_NEON_GREEN = (111, 255, 177)
COLOR_FIRE = (255, 123, 66)
COLOR_FIRE_LIGHT = (255, 206, 131)
COLOR_WATER = (77, 220, 255)
COLOR_WATER_LIGHT = (211, 250, 255)
COLOR_LAVA = (255, 83, 49)
COLOR_WATER_HAZARD = (34, 137, 224)
COLOR_PLATFORM = (31, 47, 79)
COLOR_PLATFORM_TOP = (58, 91, 134)

pygame.init()
pygame.joystick.init()
pygame.display.set_caption("MoveUp — Elemental Run")

displayScreen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
displayWidth, displayHeight = displayScreen.get_size()
screen = pygame.Surface((BASE_WIDTH, BASE_HEIGHT)).convert()
clock = pygame.time.Clock()

fontTiny = pygame.font.SysFont("DejaVu Sans", 11, bold=True)
fontSmall = pygame.font.SysFont("DejaVu Sans", 13)
font = pygame.font.SysFont("DejaVu Sans", 16, bold=True)
fontLarge = pygame.font.SysFont("DejaVu Sans", 29, bold=True)
fontHuge = pygame.font.SysFont("DejaVu Sans", 43, bold=True)


# =============================================================================
# GENERAL HELPERS
# =============================================================================
def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def blitText(surface: pygame.Surface, text: str, textFont: pygame.font.Font,
             color: tuple[int, int, int], position: tuple[int, int],
             center: bool = False) -> pygame.Rect:
    rendered = textFont.render(text, True, color)
    textRect = rendered.get_rect()
    if center:
        textRect.center = position
    else:
        textRect.topleft = position
    surface.blit(rendered, textRect)
    return textRect


def drawPanel(surface: pygame.Surface, rect: pygame.Rect,
              fillColor: tuple[int, int, int],
              borderColor: tuple[int, int, int], radius: int = 10,
              borderWidth: int = 1) -> None:
    pygame.draw.rect(surface, fillColor, rect, border_radius=radius)
    pygame.draw.rect(surface, borderColor, rect, borderWidth, border_radius=radius)


def getConnectedJoysticks() -> list:
    devices = []
    for deviceIndex in range(pygame.joystick.get_count()):
        try:
            device = pygame.joystick.Joystick(deviceIndex)
            device.init()
            devices.append(device)
        except pygame.error as error:
            print(f"Не удалось инициализировать джойстик {deviceIndex}: {error}")
    return devices


joysticks = getConnectedJoysticks()
activeJoystick: Optional[object] = joysticks[0] if joysticks else None
if activeJoystick:
    print(f"Джойстик: {activeJoystick.get_name()}")
    print(f"Оси: {activeJoystick.get_numaxes()}, кнопки: {activeJoystick.get_numbuttons()}")
else:
    print("HID-геймпад не найден — можно играть с клавиатуры.")


# =============================================================================
# LEVEL DEFINITIONS — Level 1 is retained; levels 2 and 3 add tougher routes.
# Rect tuples use (x, y, width, height). Every level has five runes per hero.
# =============================================================================
LEVEL_DEFINITIONS = [
    {
        "name": "ИСКРА",
        "subtitle": "Разминка стихий",
        "difficulty": 1,
        "worldWidth": 2960,
        "description": "Элементальные озёра и разрывы.",
        "objective": "ЦЕЛЬ: собери руны, используя озёра своей стихии и перепрыгивая чужие.",
        "groundPlatforms": [
            (0, GROUND_Y, 420, 90), (500, GROUND_Y, 410, 90),
            (1000, GROUND_Y, 410, 90), (1505, GROUND_Y, 405, 90),
            (2010, GROUND_Y, 410, 90), (2510, GROUND_Y, 450, 90),
        ],
        "raisedPlatforms": [
            (170, 492, 132, 28), (326, 458, 124, 28),
            (565, 492, 145, 28), (733, 462, 122, 28),
            (1060, 492, 145, 28), (1235, 462, 130, 28),
            (1560, 492, 150, 28), (1737, 457, 125, 28),
            (2060, 492, 145, 28), (2235, 462, 130, 28),
        ],
        "hazards": [(420, 80, "water"), (910, 90, "lava"),
                    (1410, 95, "water"), (1910, 100, "lava"),
                    (2420, 90, "water")],
        "gems": [
            (236, 464, "fire"), (386, 430, "water"),
            (630, 464, "fire"), (794, 434, "water"),
            (1125, 464, "fire"), (1300, 434, "water"),
            (1630, 464, "fire"), (1800, 429, "water"),
            (2125, 464, "fire"), (2300, 434, "water"),
        ],
        "checkpoints": [515, 1015, 1520, 2025, 2525],
        "exits": {"fire": (2790, 496, 43, 64), "water": (2848, 496, 43, 64)},
        "sections": [(40, "01 / ИСКРА"), (520, "02 / ОТРАЖЕНИЕ"),
                     (1015, "03 / ТЕРМАЛЬНЫЙ РАЗЛОМ"), (1520, "04 / ПОТОК"),
                     (2025, "05 / КРАСНАЯ ЗОНА"), (2530, "06 / ВЫХОД")],
    },
    {
        "name": "ПЕРЕЛОМ",
        "subtitle": "Зигзаги, узкие мосты и высокие уступы",
        "difficulty": 2,
        "worldWidth": 3240,
        "description": "Лазерные окна и крошащиеся опоры.",
        "objective": "ЦЕЛЬ: проходи лазеры в паузах и не задерживайся на крошащихся плитах.",
        # Unlike level 1, the floor is broken into short, uneven safe pads.
        "groundPlatforms": [
            (0, GROUND_Y, 315, 90), (415, GROUND_Y, 190, 90),
            (715, GROUND_Y, 170, 90), (990, GROUND_Y, 180, 90),
            (1275, GROUND_Y, 205, 90), (1580, GROUND_Y, 180, 90),
            (1870, GROUND_Y, 190, 90), (2165, GROUND_Y, 180, 90),
            (2450, GROUND_Y, 195, 90), (2735, GROUND_Y, 180, 90),
            (3000, GROUND_Y, 240, 90),
        ],
        # Six distinct obstacle sets: staircases, a suspended bridge, and reverse steps.
        "raisedPlatforms": [
            # 01 — steep ascent over the first chasm
            (165, 500, 86, 28), (260, 456, 78, 28), (350, 414, 72, 28),
            (438, 447, 76, 28), (490, 493, 66, 28),
            # 02 — climb to a high bridge, then descend onto the next pad
            (560, 504, 82, 28), (638, 463, 75, 28), (726, 421, 70, 28),
            (812, 390, 78, 28), (899, 434, 72, 28), (953, 484, 62, 28),
            # 03 — an elevated traverse with one wide bridge segment
            (1024, 488, 84, 28), (1118, 448, 80, 28), (1210, 408, 70, 28),
            (1290, 408, 112, 28), (1412, 452, 72, 28), (1484, 500, 70, 28),
            # 04 — reverse staircase through a narrow thermal channel
            (1604, 500, 76, 28), (1688, 459, 72, 28), (1770, 419, 68, 28),
            (1850, 449, 75, 28), (1942, 488, 72, 28),
            # 05 — three-height zigzag; the highest ledge is deliberately narrow
            (2195, 500, 74, 28), (2275, 455, 70, 28), (2355, 411, 66, 28),
            (2430, 385, 80, 28), (2525, 432, 72, 28), (2605, 477, 70, 28),
            # 06 — final stepped corridor
            (2760, 500, 78, 28), (2845, 462, 70, 28), (2930, 423, 68, 28),
            (3010, 448, 75, 28), (3108, 480, 55, 28),
        ],
        # No liquid pools in this stage: gaps are bottomless pits. The main
        # challenge is reading laser cycles, spikes and temporary footholds.
        "hazards": [],
        # Spike strips interrupt otherwise safe pads: jump timing matters, not just distance.
        "spikes": [
            (535, 548, 28, 12), (770, 548, 28, 12), (1135, 548, 24, 12),
            (1708, 548, 28, 12), (2290, 548, 30, 12), (2875, 548, 28, 12),
            (1420, 440, 22, 12),
        ],
        # Gates extend from high above the jump arc to the floor. Players must read the cycle.
        # Tuple: x, y, width, height, period, active duration, phase offset.
        "laserGates": [
            (1380, 220, 12, 340, 3.0, 1.22, 1.65),
            (1985, 220, 12, 340, 3.35, 1.38, 0.25),
        ],
        # Patrolling saws threaten the elevated routes.
        # Tuple: centerX, centerY, radius, minX, maxX, speed.
        "saws": [
            (780, 405, 14, 740, 860, 92),
            (1350, 390, 15, 1310, 1450, 112),
            (2390, 375, 15, 2280, 2510, 122),
        ],
        # Temporary footholds are useful shortcuts, but standing still makes them collapse.
        # Tuple: x, y, width, height, time before collapse.
        "crumblePlatforms": [
            (890, 402, 52, 18, 0.72), (1180, 382, 48, 18, 0.64),
            (1491, 430, 50, 18, 0.70), (2057, 420, 48, 18, 0.65),
            (2928, 400, 48, 18, 0.58),
        ],
        "gems": [
            (285, 428, "fire"), (375, 386, "water"),
            (850, 362, "water"), (662, 435, "fire"),
            (1242, 380, "fire"), (1335, 380, "water"),
            (1804, 391, "fire"), (1888, 421, "water"),
            (2468, 357, "fire"), (2560, 404, "water"),
        ],
        # Five checkpoints sit in the middle of stable floor pads, never on a trap or gap.
        "checkpoints": [450, 1040, 1615, 2210, 2790],
        # Once both rune sets are complete, both heroes must hold their own plate.
        "switches": {"fire": (3020, 552, 42, 8), "water": (3074, 552, 42, 8)},
        "exits": {"fire": (3138, 496, 43, 64), "water": (3188, 496, 43, 64)},
        "sections": [(20, "01 / КРУТОЙ ПОДЪЁМ"), (425, "02 / КАМЕННЫЕ ЗУБЬЯ"),
                     (720, "03 / ВЫСОКИЙ МОСТ"), (995, "04 / ОБРАТНЫЙ СКЛОН"),
                     (1280, "05 / УЗКИЙ КАНАЛ"), (1585, "06 / ПЕРЕКРЁСТОК"),
                     (1875, "07 / ЛОМАНАЯ ЛЕСТНИЦА"), (2170, "08 / ВЕРШИНА"),
                     (2455, "09 / ПОСЛЕДНИЙ ЗИГЗАГ"), (2740, "10 / ВЫХОД")],
    },
    {
        "name": "ЯДРО",
        "subtitle": "Башни, парящие мосты и длинные разрывы",
        "difficulty": 3,
        "worldWidth": 4000,
        "description": "Движущиеся мосты, пилы и лазеры.",
        "objective": "ЦЕЛЬ: пересекай пропасти на движущихся мостах, обходя пилы.",
        # Short ground pads and broad chasms make the elevated routes essential.
        "groundPlatforms": [
            (0, GROUND_Y, 255, 90), (390, GROUND_Y, 130, 90),
            (655, GROUND_Y, 145, 90), (930, GROUND_Y, 135, 90),
            (1190, GROUND_Y, 155, 90), (1470, GROUND_Y, 130, 90),
            (1725, GROUND_Y, 145, 90), (1995, GROUND_Y, 135, 90),
            (2260, GROUND_Y, 150, 90), (2545, GROUND_Y, 125, 90),
            (2810, GROUND_Y, 130, 90), (3070, GROUND_Y, 145, 90),
            (3350, GROUND_Y, 140, 90), (3625, GROUND_Y, 375, 90),
        ],
        # A high-rise opening, a long bridge, sharp descents, and narrow summit ledges.
        "raisedPlatforms": [
            # 01 — opening spire; six ledges climb above the floor
            (165, 500, 78, 28), (250, 455, 68, 28), (330, 410, 64, 28),
            (405, 370, 64, 28), (480, 410, 55, 28),
            # 02 — needle bridge with tight footholds on both sides
            (445, 490, 70, 28), (525, 445, 63, 28),
            (675, 355, 62, 28), (758, 395, 62, 28), (840, 440, 54, 28),
            (912, 485, 55, 28),
            # 03 — short, offset ledges: no straight, level walk-through
            (980, 500, 76, 28), (1065, 458, 70, 28), (1145, 415, 64, 28),
            (1223, 447, 62, 28), (1298, 486, 65, 28),
            # 04 — an elevated traverse ending in a long suspended bridge
            (1422, 500, 68, 28), (1496, 452, 63, 28), (1566, 405, 58, 28),
            (1634, 360, 70, 28), (1712, 390, 120, 28),
            (1908, 480, 62, 28),
            # 05 — the core's central tower, then a rapid descent
            (1988, 500, 68, 28), (2060, 455, 62, 28), (2128, 410, 58, 28),
            (2192, 365, 66, 28), (2268, 405, 58, 28), (2332, 450, 62, 28),
            (2398, 492, 70, 28),
            # 06 — highest and narrowest ledges over the widest lava channel
            (2470, 495, 74, 28), (2512, 445, 62, 28), (2580, 395, 60, 28),
            (2648, 345, 58, 28), (2792, 430, 58, 28),
            (2854, 475, 62, 28),
            # 07 — alternating heights; upper platforms don't form a flat corridor
            (2960, 500, 72, 28), (3038, 455, 62, 28), (3106, 410, 58, 28),
            (3172, 370, 62, 28), (3248, 407, 60, 28), (3318, 450, 62, 28),
            (3385, 492, 68, 28),
            # 08 — final ascent across staggered stones into the exit basin
            (3455, 490, 70, 28), (3512, 448, 64, 28), (3584, 405, 60, 28),
            (3650, 365, 60, 28), (3720, 405, 60, 28), (3790, 448, 60, 28),
            (3855, 490, 70, 28),
        ],
        # Keep the central-core route visually clean: gaps are open voids;
        # moving bridges, saws and timed laser gates define this stage.
        "hazards": [],
        # Long spike strips create explicit take-off and landing decisions on safe ground.
        "spikes": [
            (190, 548, 34, 12), (730, 548, 30, 12), (1055, 548, 26, 12),
            (1580, 548, 24, 12), (2120, 548, 28, 12), (2660, 548, 26, 12),
            (3390, 548, 30, 12),
            (1068, 446, 20, 12), (2502, 433, 22, 12), (3178, 358, 22, 12),
        ],
        # Each gate has a different rhythm; active beams are lethal and inactive beams are passable.
        "laserGates": [
            (1305, 215, 12, 345, 3.2, 1.35, 1.75),
            (2330, 215, 12, 345, 3.65, 1.48, 0.35),
            (2855, 215, 12, 345, 3.35, 1.28, 2.05),
        ],
        # Saws sweep across landing zones and tower routes; wait for a safe opening.
        "saws": [
            (710, 340, 15, 675, 790, 112),
            (1120, 397, 15, 1070, 1210, 124),
            (1800, 370, 15, 1720, 1855, 134),
            (2620, 355, 16, 2570, 2740, 146),
            (3420, 455, 15, 3360, 3480, 138),
        ],
        # Crumble stones offer dangerous shortcuts across the chasms.
        "crumblePlatforms": [
            (825, 405, 50, 18, 0.62), (1355, 425, 50, 18, 0.60),
            (1932, 422, 50, 18, 0.55), (2415, 420, 48, 18, 0.58),
            (2960, 420, 50, 18, 0.55), (3500, 420, 45, 18, 0.52),
        ],
        # Moving bridges cross three chasms. Tuple: x, y, width, height,
        # minimum left edge, maximum left edge, horizontal speed.
        "movingPlatforms": [
            (590, 430, 72, 20, 590, 650, 78),
            (1832, 430, 80, 20, 1832, 1932, 88),
            (2708, 385, 76, 20, 2708, 2780, 92),
        ],
        "gems": [
            (358, 382, "fire"), (705, 327, "water"),
            (1175, 387, "fire"), (1509, 424, "water"),
            (1668, 332, "water"), (1800, 362, "fire"),
            (2238, 337, "fire"), (2676, 317, "water"),
            (2820, 402, "fire"), (3690, 337, "water"),
        ],
        # Checkpoints follow the major gauntlets and are placed on wide, stable landing pads.
        "checkpoints": [420, 970, 1505, 2030, 2580, 3120, 3680],
        "exits": {"fire": (3892, 496, 43, 64), "water": (3942, 496, 43, 64)},
        "sections": [(18, "01 / ШПИЛЬ"), (405, "02 / ИГОЛЬЧАТЫЙ МОСТ"),
                     (945, "03 / СДВИНУТЫЕ ПЛИТЫ"), (1205, "04 / ПАРЯЩИЙ МОСТ"),
                     (1740, "05 / ЦЕНТРАЛЬНАЯ БАШНЯ"), (2275, "06 / ЯДРО"),
                     (2560, "07 / ЛАВОВЫЙ ПРОЛЁТ"), (3085, "08 / ЛОМАНЫЙ КОРИДОР"),
                     (3640, "09 / ФИНАЛЬНЫЙ ПОДЪЁМ")],
    },
]
GEMS_PER_PLAYER = 5

# Populated by loadLevel(); keeping these as shared collections lets physics and
# drawing routines work unchanged regardless of the chosen level.
groundPlatforms: list[pygame.Rect] = []
raisedPlatforms: list[pygame.Rect] = []
solidPlatforms: list[pygame.Rect] = []
movingPlatforms: list[dict] = []
crumblePlatforms: list[dict] = []
hazards: list[dict] = []
spikeHazards: list[dict] = []
sawHazards: list[dict] = []
laserGates: list[dict] = []
gemPositions: list[dict] = []
checkpointPositions: list[int] = []
exitZones: dict[str, pygame.Rect] = {}
switchZones: dict[str, pygame.Rect] = {}
switchActivated = False
sectionLabels: list[tuple[int, str]] = []
startPositions = {"fire": 64.0, "water": 112.0}
currentLevelName = LEVEL_DEFINITIONS[0]["name"]
currentLevelSubtitle = LEVEL_DEFINITIONS[0]["subtitle"]
currentLevelDifficulty = LEVEL_DEFINITIONS[0]["difficulty"]
selectedLevelIndex = 0
completedLevels: set[int] = set()

# Session analytics. Each level attempt is retained in details.rounds.
levelAttemptHistory: list[dict] = []
activeLevelAttempt: Optional[dict] = None
lastCompletedLevelAttempt: Optional[dict] = None
sessionAborted = False
sessionFinalized = False
lastStatsSaveTime = 0.0


def _platformerRoundSnapshot(outcome: str = "in_progress") -> Optional[dict]:
    if activeLevelAttempt is None:
        return None
    elapsedSeconds = max(0.0, time.monotonic() - activeLevelAttempt["started_monotonic"])
    return {
        "level_index": activeLevelAttempt["level_index"],
        "level_name": activeLevelAttempt["level_name"],
        "difficulty": activeLevelAttempt["difficulty"],
        "outcome": outcome,
        "duration_seconds": round(max(elapsedTime, elapsedSeconds), 3),
        "deaths": int(deathCount),
        "runes_collected": int(len(collectedGems)),
        "runes_available": int(len(gemPositions)),
        "fire_path_length_px": round(activeLevelAttempt["fire_path_length_px"], 3),
        "water_path_length_px": round(activeLevelAttempt["water_path_length_px"], 3),
        "fire_max_speed_px_per_second": round(activeLevelAttempt["fire_max_speed_px_per_second"], 3),
        "water_max_speed_px_per_second": round(activeLevelAttempt["water_max_speed_px_per_second"], 3),
    }


def savePlatformerStatistics(sessionStatus: str, includeActiveAttempt: bool = True) -> None:
    rounds = list(levelAttemptHistory)
    activeSnapshot = _platformerRoundSnapshot("in_progress") if includeActiveAttempt else None
    if activeSnapshot:
        roundsForMetrics = rounds + [activeSnapshot]
    else:
        roundsForMetrics = rounds

    attempts = len(roundsForMetrics)
    completedRounds = [item for item in roundsForMetrics if item.get("outcome") == "completed"]
    interruptedRounds = [item for item in roundsForMetrics if item.get("outcome") == "interrupted"]
    restartedRounds = [item for item in roundsForMetrics if item.get("outcome") == "restarted"]
    totalDeaths = sum(int(item.get("deaths", 0)) for item in roundsForMetrics)
    totalRunes = sum(int(item.get("runes_collected", 0)) for item in roundsForMetrics)
    totalAvailableRunes = sum(int(item.get("runes_available", 0)) for item in roundsForMetrics)
    totalGameplaySeconds = sum(float(item.get("duration_seconds", 0.0)) for item in roundsForMetrics)
    firePathLength = sum(float(item.get("fire_path_length_px", 0.0)) for item in roundsForMetrics)
    waterPathLength = sum(float(item.get("water_path_length_px", 0.0)) for item in roundsForMetrics)
    fireMaxSpeed = max((float(item.get("fire_max_speed_px_per_second", 0.0)) for item in roundsForMetrics), default=0.0)
    waterMaxSpeed = max((float(item.get("water_max_speed_px_per_second", 0.0)) for item in roundsForMetrics), default=0.0)

    metrics = {
        "levelAttempts": metric("Попытки прохождения уровня", attempts, "шт."),
        "levelsCompleted": metric("Успешно пройденные уровни", len(completedRounds), "шт."),
        "levelAttemptsRestarted": metric("Перезапущенные попытки", len(restartedRounds), "шт."),
        "levelAttemptsInterrupted": metric("Прерванные попытки", len(interruptedRounds), "шт."),
        "deaths": metric("Смерти персонажей", totalDeaths, "шт."),
        "runesCollected": metric("Собранные руны", totalRunes, "шт."),
        "runeCollectionRatePct": metric("Доля собранных рун", totalRunes / totalAvailableRunes * 100.0 if totalAvailableRunes else 0.0, "%"),
        "activeGameplaySeconds": metric("Суммарное время попыток", totalGameplaySeconds, "сек."),
        "firePathLengthPx": metric("Траектория персонажа Огня", firePathLength, "px"),
        "waterPathLengthPx": metric("Траектория персонажа Воды", waterPathLength, "px"),
        "fireMaxSpeedPxPerSecond": metric("Максимальная скорость Огня", fireMaxSpeed, "px/сек"),
        "waterMaxSpeedPxPerSecond": metric("Максимальная скорость Воды", waterMaxSpeed, "px/сек"),
    }
    if attempts:
        metrics["levelCompletionRatePct"] = metric("Доля успешных попыток", len(completedRounds) / attempts * 100.0, "%")
    completedTimes = [float(item.get("duration_seconds", 0.0)) for item in completedRounds]
    if completedTimes:
        metrics["averageLevelCompletionSeconds"] = metric("Среднее время успешного прохождения", sum(completedTimes) / len(completedTimes), "сек.")
        metrics["fastestLevelCompletionSeconds"] = metric("Лучшее время прохождения уровня", min(completedTimes), "сек.")

    settings = {
        "logical_resolution_px": {"width": BASE_WIDTH, "height": BASE_HEIGHT},
        "movement_model": "two character platformer; speeds and distances measured in game-world pixels",
        "levels_used": sorted({item.get("level_name", "") for item in roundsForMetrics if item.get("level_name")}),
    }
    details = {"rounds": rounds}
    if activeSnapshot:
        details["active_round"] = activeSnapshot
    moveUpStatistics.save(sessionStatus, metrics, settings, details)


def capturePlatformerAttempt(outcome: str, shouldMarkSessionAborted: bool = False) -> None:
    global activeLevelAttempt, lastCompletedLevelAttempt, sessionAborted
    snapshot = _platformerRoundSnapshot(outcome)
    if snapshot is None:
        return
    levelAttemptHistory.append(snapshot)
    if outcome == "completed":
        lastCompletedLevelAttempt = snapshot
    if shouldMarkSessionAborted:
        sessionAborted = True
    activeLevelAttempt = None
    savePlatformerStatistics("interrupted", includeActiveAttempt=False)


def beginPlatformerAttempt() -> None:
    global activeLevelAttempt
    activeLevelAttempt = {
        "level_index": selectedLevelIndex,
        "level_name": currentLevelName,
        "difficulty": currentLevelDifficulty,
        "started_monotonic": time.monotonic(),
        "fire_path_length_px": 0.0,
        "water_path_length_px": 0.0,
        "fire_max_speed_px_per_second": 0.0,
        "water_max_speed_px_per_second": 0.0,
    }


def finalizePlatformerStatistics() -> None:
    global sessionFinalized
    if sessionFinalized:
        return
    try:
        if activeLevelAttempt is not None:
            capturePlatformerAttempt("interrupted", shouldMarkSessionAborted=True)
        finalStatus = "completed" if any(item.get("outcome") == "completed" for item in levelAttemptHistory) and not sessionAborted else "interrupted"
        savePlatformerStatistics(finalStatus, includeActiveAttempt=False)
    except Exception as error:
        print(f"Не удалось завершить сохранение статистики платформера: {error}")
    sessionFinalized = True


atexit.register(finalizePlatformerStatistics)


# =============================================================================
# ACTORS AND GAME STATE
# =============================================================================
@dataclass
class Player:
    name: str
    element: str
    x: float
    y: float
    color: tuple[int, int, int]
    lightColor: tuple[int, int, int]
    weakTo: str
    moveKeyLeft: int
    moveKeyRight: int
    jumpKey: int
    vx: float = 0.0
    vy: float = 0.0
    width: int = PLAYER_WIDTH
    height: int = PLAYER_HEIGHT
    grounded: bool = False
    previousJumpAxis: bool = False
    jumpBuffer: float = 0.0
    coyoteTimer: float = 0.0
    respawnFlash: float = 0.0
    checkpointX: float = 0.0
    # Solid platforms touched from below become temporarily non-solid for this
    # player until their full collision rectangle has passed through them.
    passThroughPlatformIds: set[int] = field(default_factory=set)

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(round(self.x), round(self.y), self.width, self.height)

    def respawn(self) -> None:
        self.x = self.checkpointX
        self.y = float(GROUND_Y - self.height)
        self.vx = 0.0
        self.vy = 0.0
        self.grounded = False
        self.jumpBuffer = 0.0
        self.coyoteTimer = 0.0
        self.previousJumpAxis = False
        self.passThroughPlatformIds.clear()
        self.respawnFlash = 0.85


firePlayer = Player(
    name="ОГОНЬ", element="fire", x=64.0,
    y=float(GROUND_Y - PLAYER_HEIGHT), color=COLOR_FIRE,
    lightColor=COLOR_FIRE_LIGHT, weakTo="water",
    moveKeyLeft=pygame.K_a, moveKeyRight=pygame.K_d,
    jumpKey=pygame.K_w, checkpointX=64.0,
)
waterPlayer = Player(
    name="ВОДА", element="water", x=112.0,
    y=float(GROUND_Y - PLAYER_HEIGHT), color=COLOR_WATER,
    lightColor=COLOR_WATER_LIGHT, weakTo="lava",
    moveKeyLeft=pygame.K_LEFT, moveKeyRight=pygame.K_RIGHT,
    jumpKey=pygame.K_UP, checkpointX=112.0,
)
players = [firePlayer, waterPlayer]


def loadLevel(levelIndex: int) -> None:
    """Replace the active world geometry with one of the authored levels."""
    global selectedLevelIndex, WORLD_WIDTH, groundPlatforms, raisedPlatforms
    global solidPlatforms, movingPlatforms, crumblePlatforms, hazards, spikeHazards
    global sawHazards, laserGates, gemPositions, checkpointPositions, exitZones
    global switchZones, switchActivated
    global sectionLabels, startPositions, currentLevelName, currentLevelSubtitle
    global currentLevelDifficulty

    selectedLevelIndex = max(0, min(len(LEVEL_DEFINITIONS) - 1, int(levelIndex)))
    levelData = LEVEL_DEFINITIONS[selectedLevelIndex]
    WORLD_WIDTH = levelData["worldWidth"]
    currentLevelName = levelData["name"]
    currentLevelSubtitle = levelData["subtitle"]
    currentLevelDifficulty = levelData["difficulty"]
    groundPlatforms = [pygame.Rect(*rectData) for rectData in levelData["groundPlatforms"]]
    raisedPlatforms = [pygame.Rect(*rectData) for rectData in levelData["raisedPlatforms"]]
    movingPlatforms = []
    for platformX, platformY, platformWidth, platformHeight, minX, maxX, speed in levelData.get("movingPlatforms", []):
        platformRect = pygame.Rect(platformX, platformY, platformWidth, platformHeight)
        movingPlatforms.append({
            "rect": platformRect,
            "startX": float(platformX),
            "positionX": float(platformX),
            "minX": float(minX),
            "maxX": float(maxX),
            "speed": float(speed),
            "direction": 1.0,
        })
    crumblePlatforms = []
    for crumbleX, crumbleY, crumbleWidth, crumbleHeight, crumbleDelay in levelData.get("crumblePlatforms", []):
        crumblePlatforms.append({
            "rect": pygame.Rect(crumbleX, crumbleY, crumbleWidth, crumbleHeight),
            "delay": float(crumbleDelay),
            "timer": float(crumbleDelay),
            "triggered": False,
            "active": True,
            "fallTimer": 0.0,
        })
    solidPlatforms = (groundPlatforms + raisedPlatforms
                      + [item["rect"] for item in movingPlatforms]
                      + [item["rect"] for item in crumblePlatforms])
    hazards = [
        {
            "rect": pygame.Rect(hazardX, GROUND_Y, hazardWidth, 90),
            "kind": hazardKind,
            "label": "ВОДА" if hazardKind == "water" else "ЛАВА",
        }
        for hazardX, hazardWidth, hazardKind in levelData["hazards"]
    ]
    spikeHazards = [
        {"rect": pygame.Rect(*spikeData)}
        for spikeData in levelData.get("spikes", [])
    ]
    laserGates = []
    for gateX, gateY, gateWidth, gateHeight, period, activeDuration, phase in levelData.get("laserGates", []):
        laserGates.append({
            "rect": pygame.Rect(gateX, gateY, gateWidth, gateHeight),
            "period": float(period),
            "activeDuration": float(activeDuration),
            "phase": float(phase),
            "active": False,
        })
    sawHazards = []
    for centerX, centerY, radius, minX, maxX, speed in levelData.get("saws", []):
        sawHazards.append({
            "x": float(centerX), "startX": float(centerX),
            "y": float(centerY), "radius": int(radius),
            "minX": float(minX), "maxX": float(maxX),
            "speed": float(speed), "direction": 1.0,
            "rect": pygame.Rect(centerX - radius, centerY - radius, radius * 2, radius * 2),
        })
    gemPositions = [
        {"x": gemX, "y": gemY, "owner": owner,
         "originalX": gemX, "originalY": gemY}
        for gemX, gemY, owner in levelData["gems"]
    ]
    checkpointPositions = list(levelData["checkpoints"])
    exitZones = {element: pygame.Rect(*rectData)
                 for element, rectData in levelData["exits"].items()}
    switchZones = {element: pygame.Rect(*rectData)
                   for element, rectData in levelData.get("switches", {}).items()}
    switchActivated = False
    sectionLabels = list(levelData["sections"])
    startPositions = dict(levelData.get("startPositions", {"fire": 64.0, "water": 112.0}))

collectedGems: set[int] = set()
# Ordered pickup history lets death remove exactly the most recently earned rune.
playerGemHistory: dict[str, list[int]] = {"fire": [], "water": []}
deathCount = 0
particles: list[dict] = []
elapsedTime = 0.0
state = "PLAY"
winTimer = 0.0
cameraX = 0.0
statusMessage = "СОБИРАЙ СВОИ РУНЫ И ДОБЕРИТЕСЬ ДО ВЫХОДОВ"
statusTimer = 5.0


def countGems(element: str) -> int:
    return sum(1 for gemIndex in collectedGems if gemPositions[gemIndex]["owner"] == element)


def allGemsCollected() -> bool:
    return len(collectedGems) == len(gemPositions)


# =============================================================================
# BACKGROUND AND WORLD RENDERING
# =============================================================================
def createBackground() -> pygame.Surface:
    background = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
    for y in range(BASE_HEIGHT):
        ratio = y / BASE_HEIGHT
        color = tuple(
            int(COLOR_BG_DARK[channel] * (1 - ratio) + COLOR_BG_LIGHT[channel] * ratio)
            for channel in range(3)
        )
        pygame.draw.line(background, color, (0, y), (BASE_WIDTH, y))

    # A hazy cyan moon and fine orbital rings make the horizon less flat.
    pygame.draw.circle(background, (24, 48, 82), (738, 172), 80)
    pygame.draw.circle(background, (30, 67, 103), (738, 172), 63, 1)
    pygame.draw.circle(background, (35, 79, 119), (738, 172), 47, 1)
    pygame.draw.arc(background, (39, 89, 124), (644, 123, 190, 94), math.pi * 1.03, math.pi * 1.92, 2)

    randomGenerator = random.Random(21)
    skylineX = -5
    while skylineX < BASE_WIDTH + 35:
        buildingWidth = randomGenerator.randint(27, 70)
        buildingHeight = randomGenerator.randint(70, 205)
        buildingRect = pygame.Rect(skylineX, 485 - buildingHeight, buildingWidth, buildingHeight)
        pygame.draw.rect(background, (14, 25, 51), buildingRect)
        pygame.draw.line(background, (27, 54, 88), buildingRect.topleft,
                         (buildingRect.right, buildingRect.top), 2)
        for windowY in range(buildingRect.top + 12, buildingRect.bottom - 8, 18):
            for windowX in range(buildingRect.left + 7, buildingRect.right - 4, 14):
                if randomGenerator.random() < 0.27:
                    pygame.draw.rect(background, (28, 71, 104), (windowX, windowY, 3, 5))
        skylineX += buildingWidth + randomGenerator.randint(5, 14)

    for _ in range(68):
        starX = randomGenerator.randrange(BASE_WIDTH)
        starY = randomGenerator.randrange(30, 385)
        starRadius = randomGenerator.choice((1, 1, 2))
        pygame.draw.circle(background, (55, 91, 139), (starX, starY), starRadius)

    # Distant floor-grid lines gently lead the eye toward the route.
    for lineY in range(480, BASE_HEIGHT, 18):
        pygame.draw.line(background, (18, 39, 69), (0, lineY), (BASE_WIDTH, lineY), 1)
    return background


background = createBackground()


def worldToScreenX(worldX: float) -> int:
    return round(worldX - cameraX)


def drawBackground() -> None:
    screen.blit(background, (0, 0))
    # Slow parallax rails create a sense of motion without cluttering the playfield.
    for bandIndex in range(6):
        bandX = int((bandIndex * 230 - cameraX * 0.20) % (BASE_WIDTH + 220)) - 110
        y = 148 + bandIndex * 48
        pygame.draw.line(screen, (24, 48, 80), (bandX, y), (bandX + 165, y - 19), 1)

    # Faint vertical columns in the far background.
    for columnIndex in range(8):
        columnX = int((columnIndex * 155 - cameraX * 0.12) % (BASE_WIDTH + 120)) - 40
        pygame.draw.line(screen, (19, 39, 68), (columnX, 210), (columnX, 486), 2)


def drawSectionLabels() -> None:
    for sectionX, label in sectionLabels:
        labelX = worldToScreenX(sectionX)
        if -220 < labelX < BASE_WIDTH + 40:
            labelRect = pygame.Rect(labelX, 94, min(214, fontTiny.size(label)[0] + 22), 23)
            pygame.draw.rect(screen, (12, 21, 43), labelRect, border_radius=6)
            pygame.draw.rect(screen, (39, 72, 109), labelRect, 1, border_radius=6)
            blitText(screen, label, fontTiny, (130, 171, 207),
                     (labelRect.x + 9, labelRect.y + 5))


def drawPlatform(platform: pygame.Rect) -> None:
    screenRect = pygame.Rect(worldToScreenX(platform.x), platform.y, platform.width, platform.height)
    if screenRect.right < -30 or screenRect.left > BASE_WIDTH + 30:
        return

    isGround = platform.y == GROUND_Y
    pygame.draw.rect(screen, (8, 14, 31), screenRect, border_radius=5)
    pygame.draw.rect(screen, COLOR_PLATFORM if not isGround else (24, 37, 65), screenRect, border_radius=5)

    # The top surface is thick and bright enough to read clearly against a hero's feet.
    pygame.draw.rect(screen, COLOR_PLATFORM_TOP if not isGround else (42, 67, 103),
                     (screenRect.x, screenRect.y, screenRect.width, 7), border_radius=3)
    pygame.draw.line(screen, (109, 164, 197), (screenRect.left + 5, screenRect.top + 1),
                     (screenRect.right - 5, screenRect.top + 1), 1)
    pygame.draw.line(screen, (14, 23, 44), (screenRect.left + 4, screenRect.bottom - 2),
                     (screenRect.right - 4, screenRect.bottom - 2), 2)

    if isGround:
        # Broad ground tiles have stable visual rhythm instead of looking like thin ledges.
        for seamX in range(screenRect.left + 34, screenRect.right - 8, 48):
            pygame.draw.line(screen, (32, 48, 78), (seamX, screenRect.top + 9),
                             (seamX, min(screenRect.bottom - 5, screenRect.top + 29)), 1)
        for boltX in (screenRect.left + 13, screenRect.right - 13):
            if 0 <= boltX <= BASE_WIDTH:
                pygame.draw.circle(screen, (73, 111, 147), (boltX, screenRect.top + 17), 2)
        # Side-face panels.
        for panelX in range(screenRect.left + 18, screenRect.right - 25, 96):
            pygame.draw.line(screen, (20, 31, 56), (panelX, screenRect.top + 34),
                             (panelX + 28, screenRect.top + 34), 2)
    else:
        for seamX in range(screenRect.left + 24, screenRect.right - 8, 42):
            pygame.draw.line(screen, (42, 61, 96), (seamX, screenRect.top + 9),
                             (seamX, screenRect.bottom - 4), 1)
        # Small cyan brackets make each ledge's edge obvious at a glance.
        pygame.draw.line(screen, (55, 183, 207), (screenRect.left + 3, screenRect.top + 4),
                         (screenRect.left + 15, screenRect.top + 4), 2)
        pygame.draw.line(screen, (55, 183, 207), (screenRect.right - 15, screenRect.top + 4),
                         (screenRect.right - 3, screenRect.top + 4), 2)


def drawMovingPlatform(movingPlatform: dict) -> None:
    platformRect = movingPlatform["rect"]
    screenRect = pygame.Rect(worldToScreenX(platformRect.x), platformRect.y,
                             platformRect.width, platformRect.height)
    if screenRect.right < -100 or screenRect.left > BASE_WIDTH + 100:
        return
    trackStartX = worldToScreenX(round(movingPlatform["minX"] + platformRect.width / 2))
    trackEndX = worldToScreenX(round(movingPlatform["maxX"] + platformRect.width / 2))
    trackY = screenRect.bottom + 7
    pygame.draw.line(screen, (28, 84, 119), (trackStartX, trackY), (trackEndX, trackY), 2)
    for trackX in (trackStartX, trackEndX):
        pygame.draw.circle(screen, COLOR_NEON_BLUE, (trackX, trackY), 3)
    pygame.draw.rect(screen, COLOR_NEON_BLUE, screenRect, 2, border_radius=4)
    pygame.draw.line(screen, (225, 252, 255),
                     (screenRect.left + 7, screenRect.top + 2),
                     (screenRect.right - 7, screenRect.top + 2), 2)
    # Arrow chevrons indicate that this ledge is in motion.
    midX = screenRect.centerx
    midY = screenRect.centery
    pygame.draw.lines(screen, COLOR_NEON_BLUE, False,
                      [(midX - 9, midY - 3), (midX - 3, midY), (midX - 9, midY + 3)], 1)
    pygame.draw.lines(screen, COLOR_NEON_BLUE, False,
                      [(midX + 9, midY - 3), (midX + 3, midY), (midX + 9, midY + 3)], 1)


def drawHazard(hazard: dict, timeValue: float) -> None:
    hazardRect = hazard["rect"]
    screenRect = pygame.Rect(worldToScreenX(hazardRect.x), hazardRect.y,
                             hazardRect.width, hazardRect.height)
    if screenRect.right < 0 or screenRect.left > BASE_WIDTH:
        return

    isWater = hazard["kind"] == "water"
    baseColor = (12, 48, 91) if isWater else (75, 28, 30)
    waveColor = COLOR_WATER if isWater else COLOR_LAVA
    highlightColor = (143, 240, 255) if isWater else (255, 199, 101)
    pygame.draw.rect(screen, baseColor, screenRect)
    pygame.draw.rect(screen, (20, 58, 92) if isWater else (105, 39, 30),
                     (screenRect.x, screenRect.y + 18, screenRect.width, 36))

    # Animated surface waves stay at the hazard lip, clearly showing the collision line.
    for waveIndex in range(2):
        wavePoints = []
        for pointIndex in range(9):
            px = screenRect.left + round(pointIndex * screenRect.width / 8)
            waveY = screenRect.top + 5 + math.sin(pointIndex * 1.12 + timeValue * 3.2 + waveIndex) * 2
            waveY += math.sin(timeValue * 4.1 + waveIndex * 1.5) * 1.5
            wavePoints.append((px, int(waveY)))
        if len(wavePoints) > 1:
            pygame.draw.lines(screen, highlightColor if waveIndex == 0 else waveColor,
                              False, wavePoints, 2)
    pygame.draw.line(screen, waveColor, (screenRect.left, screenRect.top + 1),
                     (screenRect.right, screenRect.top + 1), 2)

    # Warning ticks on the lips make the gap boundaries easier to judge.
    for edgeX in (screenRect.left + 5, screenRect.right - 5):
        pygame.draw.line(screen, waveColor, (edgeX, screenRect.top - 2),
                         (edgeX, screenRect.top + 12), 2)


def drawSpikeHazard(spike: dict) -> None:
    """Draw a crisp, readable strip of lethal triangular spikes."""
    worldRect = spike["rect"]
    spikeRect = pygame.Rect(worldToScreenX(worldRect.x), worldRect.y,
                            worldRect.width, worldRect.height)
    if spikeRect.right < 0 or spikeRect.left > BASE_WIDTH:
        return
    pygame.draw.rect(screen, (31, 20, 39), spikeRect)
    pitch = 12
    for spikeX in range(spikeRect.left, spikeRect.right, pitch):
        spikeRight = min(spikeX + pitch, spikeRect.right)
        points = [(spikeX, spikeRect.bottom),
                  ((spikeX + spikeRight) // 2, spikeRect.top),
                  (spikeRight, spikeRect.bottom)]
        pygame.draw.polygon(screen, (255, 67, 112), points)
        pygame.draw.lines(screen, (255, 174, 192), False, points, 1)
    pygame.draw.line(screen, (255, 91, 130), spikeRect.topleft,
                     spikeRect.topright, 1)


def drawSawHazard(saw: dict, timeValue: float) -> None:
    """A rotating toothed saw marks the moving collision box."""
    centerX = worldToScreenX(round(saw["x"]))
    centerY = int(saw["y"])
    radius = saw["radius"]
    if centerX < -radius - 8 or centerX > BASE_WIDTH + radius + 8:
        return
    pygame.draw.circle(screen, (52, 20, 46), (centerX, centerY), radius + 5)
    pygame.draw.circle(screen, (255, 54, 125), (centerX, centerY), radius, 2)
    for toothIndex in range(8):
        angle = timeValue * 3.0 + toothIndex * math.tau / 8
        innerPoint = (centerX + int(math.cos(angle) * (radius - 3)),
                      centerY + int(math.sin(angle) * (radius - 3)))
        outerPoint = (centerX + int(math.cos(angle + 0.18) * (radius + 4)),
                      centerY + int(math.sin(angle + 0.18) * (radius + 4)))
        otherOuter = (centerX + int(math.cos(angle - 0.18) * (radius + 4)),
                      centerY + int(math.sin(angle - 0.18) * (radius + 4)))
        pygame.draw.polygon(screen, (255, 105, 157), [innerPoint, outerPoint, otherOuter])
    pygame.draw.circle(screen, (245, 248, 255), (centerX, centerY), 4)
    pygame.draw.circle(screen, (255, 54, 125), (centerX, centerY), 2)


def drawLaserGate(gate: dict) -> None:
    """Active beams are bright red; inactive beams keep a dim frame as a timing cue."""
    worldRect = gate["rect"]
    gateRect = pygame.Rect(worldToScreenX(worldRect.x), worldRect.y,
                           worldRect.width, worldRect.height)
    if gateRect.right < -20 or gateRect.left > BASE_WIDTH + 20:
        return
    if gate["active"]:
        pygame.draw.rect(screen, (91, 21, 54), gateRect.inflate(8, 0), border_radius=4)
        pygame.draw.rect(screen, (255, 39, 103), gateRect, border_radius=3)
        pygame.draw.line(screen, (255, 220, 236), gateRect.midtop,
                         gateRect.midbottom, 2)
        for warningY in range(gateRect.top + 8, gateRect.bottom, 22):
            pygame.draw.line(screen, (255, 131, 173),
                             (gateRect.left - 3, warningY), (gateRect.right + 3, warningY + 6), 1)
    else:
        pygame.draw.rect(screen, (24, 57, 80), gateRect, 1, border_radius=3)
        pygame.draw.line(screen, (91, 144, 174), gateRect.midtop,
                         gateRect.midbottom, 1)
        pygame.draw.circle(screen, (255, 190, 92), gateRect.midtop, 4)
        pygame.draw.circle(screen, (255, 190, 92), gateRect.midbottom, 4)


def drawCrumblePlatform(crumble: dict, timeValue: float) -> None:
    platformRect = crumble["rect"]
    if not crumble["active"]:
        if crumble["fallTimer"] <= 0:
            return
        fallOffset = int((0.24 - crumble["fallTimer"]) * 95)
        leftRect = pygame.Rect(worldToScreenX(platformRect.x), platformRect.y + fallOffset,
                               max(1, platformRect.width // 2 - 2), platformRect.height)
        rightRect = pygame.Rect(worldToScreenX(platformRect.x + platformRect.width // 2 + 2),
                                platformRect.y + fallOffset + 3,
                                max(1, platformRect.width - platformRect.width // 2 - 2),
                                platformRect.height)
        pygame.draw.rect(screen, (255, 153, 78), leftRect, border_radius=3)
        pygame.draw.rect(screen, (171, 92, 66), rightRect, border_radius=3)
        return

    drawPlatform(platformRect)
    if crumble["triggered"]:
        warningColor = (255, 184, 92) if crumble["timer"] > 0.25 else (255, 71, 112)
        crackX = worldToScreenX(platformRect.centerx)
        pygame.draw.line(screen, warningColor,
                         (crackX - 8, platformRect.top + 5),
                         (crackX - 2, platformRect.top + 12), 2)
        pygame.draw.line(screen, warningColor,
                         (crackX - 2, platformRect.top + 12),
                         (crackX - 7, platformRect.top + 19), 2)
        pygame.draw.line(screen, warningColor,
                         (crackX - 2, platformRect.top + 12),
                         (crackX + 6, platformRect.top + 18), 2)


def drawCheckpoint(checkpointX: int, checkpointIndex: int) -> None:
    beaconX = worldToScreenX(checkpointX)
    if beaconX < -25 or beaconX > BASE_WIDTH + 25:
        return
    activeCheckpoint = min(firePlayer.checkpointX, waterPlayer.checkpointX) >= checkpointX + 14
    beaconColor = COLOR_NEON_GREEN if activeCheckpoint else (60, 98, 137)
    baseRect = pygame.Rect(beaconX - 13, GROUND_Y - 5, 26, 8)
    pygame.draw.ellipse(screen, (7, 13, 28), baseRect)
    pillarRect = pygame.Rect(beaconX - 4, GROUND_Y - 41, 8, 36)
    pygame.draw.rect(screen, (17, 31, 56), pillarRect, border_radius=4)
    pygame.draw.rect(screen, beaconColor, pillarRect, 1, border_radius=4)
    pygame.draw.circle(screen, beaconColor, (beaconX, GROUND_Y - 43), 5)
    pygame.draw.circle(screen, (218, 255, 255), (beaconX, GROUND_Y - 43), 2)
    blitText(screen, f"CP{checkpointIndex + 1}", fontTiny, beaconColor,
             (beaconX, GROUND_Y - 58), center=True)


def drawGem(gem: dict, gemIndex: int, timeValue: float) -> None:
    if gemIndex in collectedGems:
        return
    gemX = worldToScreenX(gem["x"])
    if gemX < -24 or gemX > BASE_WIDTH + 24:
        return
    color = COLOR_FIRE if gem["owner"] == "fire" else COLOR_WATER
    brightColor = COLOR_FIRE_LIGHT if gem["owner"] == "fire" else COLOR_WATER_LIGHT
    gemY = gem["y"] + int(math.sin(timeValue * 2.8 + gemIndex * 0.9) * 3)

    # A readable diamond rune with a restrained halo and a small inner facet.
    pygame.draw.circle(screen, (*color,), (gemX, gemY), 13, 1)
    points = [(gemX, gemY - 10), (gemX + 8, gemY),
              (gemX, gemY + 10), (gemX - 8, gemY)]
    pygame.draw.polygon(screen, color, points)
    innerPoints = [(gemX, gemY - 6), (gemX + 4, gemY),
                   (gemX, gemY + 6), (gemX - 4, gemY)]
    pygame.draw.polygon(screen, brightColor, innerPoints)
    pygame.draw.line(screen, (255, 255, 255), (gemX, gemY - 5), (gemX, gemY + 4), 1)


def drawDoor(rect: pygame.Rect, color: tuple[int, int, int], label: str,
             isUnlocked: bool, isOccupied: bool) -> None:
    screenRect = pygame.Rect(worldToScreenX(rect.x), rect.y, rect.width, rect.height)
    if screenRect.right < -30 or screenRect.left > BASE_WIDTH + 30:
        return

    frameColor = color if isUnlocked else (72, 91, 122)
    if isOccupied:
        frameColor = COLOR_NEON_GREEN
    outerRect = screenRect.inflate(10, 8)
    pygame.draw.rect(screen, (8, 15, 33), outerRect, border_radius=9)
    pygame.draw.rect(screen, (21, 34, 60), screenRect, border_radius=7)
    pygame.draw.rect(screen, frameColor, screenRect, 2, border_radius=7)
    archRect = pygame.Rect(screenRect.x + 6, screenRect.y + 8,
                           screenRect.width - 12, screenRect.height - 12)
    pygame.draw.arc(screen, frameColor, archRect, math.pi, math.tau, 2)
    pygame.draw.line(screen, frameColor, (screenRect.left + 5, screenRect.bottom - 8),
                     (screenRect.right - 5, screenRect.bottom - 8), 2)
    pygame.draw.circle(screen, frameColor, (screenRect.right - 8, screenRect.centery), 2)
    blitText(screen, label, fontTiny, frameColor,
             (screenRect.centerx, screenRect.top - 17), center=True)


def drawSwitch(zone: pygame.Rect, element: str, isActive: bool) -> None:
    """Render a level-specific co-op pressure plate in world coordinates."""
    screenRect = pygame.Rect(worldToScreenX(zone.x), zone.y, zone.width, zone.height)
    if screenRect.right < -40 or screenRect.left > BASE_WIDTH + 40:
        return
    elementColor = COLOR_FIRE if element == "fire" else COLOR_WATER
    plateColor = COLOR_NEON_GREEN if isActive else elementColor
    pygame.draw.rect(screen, (5, 10, 25), screenRect.inflate(6, 4), border_radius=4)
    pygame.draw.rect(screen, plateColor, screenRect, border_radius=3)
    pygame.draw.line(screen, (240, 252, 255),
                     (screenRect.left + 4, screenRect.top + 1),
                     (screenRect.right - 4, screenRect.top + 1), 1)
    label = "ПЛИТА ОГНЯ" if element == "fire" else "ПЛИТА ВОДЫ"
    blitText(screen, label, fontTiny, plateColor,
             (screenRect.centerx, screenRect.top - 14), center=True)


def drawPlayer(player: Player, timeValue: float) -> None:
    playerRect = player.rect
    screenRect = pygame.Rect(worldToScreenX(playerRect.x), playerRect.y,
                             playerRect.width, playerRect.height)
    if screenRect.right < -40 or screenRect.left > BASE_WIDTH + 40:
        return
    if player.respawnFlash > 0 and int(timeValue * 18) % 2 == 0:
        return

    centerX = screenRect.centerx
    topY = screenRect.top
    bottomY = screenRect.bottom

    # Small floor shadow; all character features remain inside the collision box.
    shadowWidth = 27 if player.grounded else 20
    pygame.draw.ellipse(screen, (5, 9, 23),
                        (centerX - shadowWidth // 2, bottomY - 1, shadowWidth, 6))

    # Body silhouette.
    bodyRect = pygame.Rect(screenRect.x + 3, topY + 12, screenRect.width - 6, 31)
    pygame.draw.rect(screen, (15, 28, 51), bodyRect, border_radius=10)
    pygame.draw.rect(screen, player.color, bodyRect, 2, border_radius=10)
    pygame.draw.line(screen, player.lightColor, (bodyRect.left + 5, bodyRect.top + 7),
                     (bodyRect.left + 5, bodyRect.bottom - 7), 2)

    if player.element == "fire":
        # Compact flame crest: no pixels extend beyond the 46 px hitbox.
        crestPoints = [
            (centerX - 10, topY + 17), (centerX - 10, topY + 8),
            (centerX - 4, topY + 10), (centerX, topY + 1),
            (centerX + 4, topY + 9), (centerX + 10, topY + 5),
            (centerX + 10, topY + 17),
        ]
        pygame.draw.polygon(screen, player.color, crestPoints)
        pygame.draw.polygon(screen, player.lightColor, crestPoints, 1)
        headY = topY + 18
    else:
        # Water's teardrop crest has the same height as Fire's crest.
        dropPoints = [
            (centerX, topY + 1), (centerX + 10, topY + 10),
            (centerX + 8, topY + 17), (centerX, topY + 20),
            (centerX - 8, topY + 17), (centerX - 10, topY + 10),
        ]
        pygame.draw.polygon(screen, player.color, dropPoints)
        pygame.draw.polygon(screen, player.lightColor, dropPoints, 1)
        headY = topY + 19

    # Face and brows.
    pygame.draw.circle(screen, (245, 251, 255), (centerX - 4, headY + 4), 2)
    pygame.draw.circle(screen, (245, 251, 255), (centerX + 4, headY + 4), 2)
    pygame.draw.line(screen, player.lightColor, (centerX - 3, headY + 9),
                     (centerX + 3, headY + 9), 1)

    # Animated feet remain within the hitbox, keeping visual and collision height aligned.
    walkPhase = math.sin(timeValue * 13 + (0 if player.element == "fire" else 1.2))
    legShift = int(walkPhase * 3) if abs(player.vx) > 12 and player.grounded else 0
    pygame.draw.line(screen, player.color, (centerX - 5, bottomY - 8),
                     (centerX - 6 + legShift, bottomY - 2), 3)
    pygame.draw.line(screen, player.color, (centerX + 5, bottomY - 8),
                     (centerX + 6 - legShift, bottomY - 2), 3)

    # A tiny element marker lets the characters remain distinct during overlap.
    markerY = topY + 29
    pygame.draw.circle(screen, player.lightColor, (centerX, markerY), 2)


# =============================================================================
# HUD AND STATUS FEEDBACK
# =============================================================================
def drawProgressCard(rect: pygame.Rect, title: str, current: int,
                     accent: tuple[int, int, int]) -> None:
    drawPanel(screen, rect, (13, 24, 47), (39, 65, 96), radius=8)
    pygame.draw.circle(screen, accent, (rect.x + 14, rect.y + 15), 4)
    blitText(screen, title, fontTiny, accent, (rect.x + 25, rect.y + 7))
    blitText(screen, f"РУНЫ  {current}/{GEMS_PER_PLAYER}", fontSmall,
             COLOR_TEXT, (rect.x + 11, rect.y + 27))
    barRect = pygame.Rect(rect.x + 77, rect.y + 31, rect.width - 87, 6)
    pygame.draw.rect(screen, (30, 43, 65), barRect, border_radius=3)
    fillWidth = round(barRect.width * current / GEMS_PER_PLAYER)
    if fillWidth > 0:
        pygame.draw.rect(screen, accent, (barRect.x, barRect.y, fillWidth, barRect.height), border_radius=3)


def drawHud() -> None:
    header = pygame.Surface((BASE_WIDTH, HUD_HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(header, (7, 14, 32, 245), (0, 0, BASE_WIDTH, HUD_HEIGHT))
    screen.blit(header, (0, 0))
    pygame.draw.line(screen, (0, 166, 207), (0, HUD_HEIGHT - 1),
                     (BASE_WIDTH, HUD_HEIGHT - 1), 2)

    blitText(screen, "MOVEUP", fontLarge, COLOR_TEXT, (16, 7))
    blitText(screen, f"УРОВЕНЬ {selectedLevelIndex + 1:02d} / {currentLevelName}",
             fontTiny, COLOR_NEON_BLUE, (18, 43))

    drawProgressCard(pygame.Rect(218, 13, 139, 50), "ОГОНЬ", countGems("fire"), COLOR_FIRE)
    drawProgressCard(pygame.Rect(366, 13, 139, 50), "ВОДА", countGems("water"), COLOR_WATER)

    progress = (firePlayer.x + waterPlayer.x) / 2 / max(1, WORLD_WIDTH - 100)
    blitText(screen, "МАРШРУТ", fontTiny, COLOR_MUTED, (523, 14))
    progressRect = pygame.Rect(523, 35, 137, 7)
    pygame.draw.rect(screen, (29, 43, 65), progressRect, border_radius=4)
    pygame.draw.rect(screen, COLOR_NEON_BLUE,
                     (progressRect.x, progressRect.y, int(progressRect.width * clamp(progress, 0, 1)), progressRect.height),
                     border_radius=4)
    blitText(screen, f"{int(clamp(progress, 0, 1) * 100)}%", fontTiny,
             COLOR_TEXT, (668, 30))

    blitText(screen, "A/D + W", fontSmall, COLOR_FIRE, (718, 12))
    blitText(screen, "←/→ + ↑", fontSmall, COLOR_WATER, (718, 33))
    blitText(screen, f"СМЕРТИ: {deathCount}   R — рестарт", fontTiny,
             COLOR_FIRE if deathCount else COLOR_MUTED, (718, 54))


def drawStatusToast(text: str, color: tuple[int, int, int]) -> None:
    toastWidth = min(590, max(310, fontSmall.size(text)[0] + 34))
    toastRect = pygame.Rect((BASE_WIDTH - toastWidth) // 2, 83, toastWidth, 31)
    drawPanel(screen, toastRect, (9, 18, 39), (35, 69, 100), radius=8)
    pygame.draw.line(screen, color, (toastRect.left + 10, toastRect.top + 7),
                     (toastRect.left + 10, toastRect.bottom - 7), 2)
    blitText(screen, text, fontSmall, color,
             (toastRect.centerx + 4, toastRect.centery), center=True)


# =============================================================================
# PHYSICS AND GAMEPLAY
# =============================================================================
def getJoystickInput(player: Player) -> tuple[float, bool]:
    if activeJoystick is None:
        player.previousJumpAxis = False
        return 0.0, False

    axes = JOYSTICK_AXES[player.element]
    if activeJoystick.get_numaxes() <= max(axes.values()):
        player.previousJumpAxis = False
        return 0.0, False

    try:
        horizontalAxis = float(activeJoystick.get_axis(axes["x"]))
        verticalAxis = float(activeJoystick.get_axis(axes["y"]))
    except pygame.error:
        player.previousJumpAxis = False
        return 0.0, False

    if abs(horizontalAxis) < AXIS_DEADZONE:
        horizontalAxis = 0.0
    jumpIsHeld = verticalAxis < JUMP_AXIS_THRESHOLD
    jumpPressed = jumpIsHeld and not player.previousJumpAxis
    player.previousJumpAxis = jumpIsHeld
    return horizontalAxis, jumpPressed


def spawnParticles(x: float, y: float, color: tuple[int, int, int],
                   count: int = 12) -> None:
    for _ in range(count):
        angle = random.uniform(0, math.tau)
        speed = random.uniform(45, 190)
        particles.append({
            "x": x, "y": y,
            "vx": math.cos(angle) * speed,
            "vy": math.sin(angle) * speed,
            "life": random.uniform(0.25, 0.58),
            "maxLife": 0.58,
            "color": color,
            "radius": random.randint(2, 4),
        })


def setStatus(message: str, duration: float = 2.4) -> None:
    global statusMessage, statusTimer
    statusMessage = message
    statusTimer = duration


def resetGame() -> None:
    global elapsedTime, state, winTimer, cameraX, statusMessage, statusTimer, switchActivated
    global deathCount, solidPlatforms
    firePlayer.checkpointX = float(startPositions["fire"])
    waterPlayer.checkpointX = float(startPositions["water"])
    firePlayer.x = firePlayer.checkpointX
    waterPlayer.x = waterPlayer.checkpointX
    firePlayer.respawn()
    waterPlayer.respawn()
    firePlayer.respawnFlash = 0.0
    waterPlayer.respawnFlash = 0.0
    firePlayer.passThroughPlatformIds.clear()
    waterPlayer.passThroughPlatformIds.clear()
    collectedGems.clear()
    playerGemHistory["fire"].clear()
    playerGemHistory["water"].clear()
    for gem in gemPositions:
        gem["x"] = gem["originalX"]
        gem["y"] = gem["originalY"]
    deathCount = 0
    particles.clear()
    switchActivated = False
    for crumblePlatform in crumblePlatforms:
        crumblePlatform["timer"] = crumblePlatform["delay"]
        crumblePlatform["triggered"] = False
        crumblePlatform["active"] = True
        crumblePlatform["fallTimer"] = 0.0
    solidPlatforms = (groundPlatforms + raisedPlatforms
                      + [item["rect"] for item in movingPlatforms]
                      + [item["rect"] for item in crumblePlatforms])
    for movingPlatform in movingPlatforms:
        movingPlatform["positionX"] = movingPlatform["startX"]
        movingPlatform["direction"] = 1.0
        movingPlatform["rect"].x = round(movingPlatform["startX"])
    for saw in sawHazards:
        saw["x"] = saw["startX"]
        saw["direction"] = 1.0
        saw["rect"].x = round(saw["x"] - saw["radius"])
    elapsedTime = 0.0
    for gate in laserGates:
        gate["active"] = ((gate["phase"] % gate["period"]) < gate["activeDuration"])
    winTimer = 0.0
    cameraX = 0.0
    state = "PLAY"
    statusMessage = LEVEL_DEFINITIONS[selectedLevelIndex].get(
        "objective", "СОБЕРИТЕ РУНЫ И ДОБЕРИТЕСЬ ДО ВЫХОДОВ"
    )
    statusTimer = 5.0


def updateCheckpoints(player: Player) -> None:
    # Checkpoints are sensors on known-safe floor sections. Only the next marker
    # can activate, preventing one landing from accidentally skipping the whole level.
    playerCenterX = player.x + player.width / 2
    for checkpointIndex, checkpointX in enumerate(checkpointPositions):
        respawnX = float(checkpointX + 14)
        if respawnX <= player.checkpointX:
            continue
        if player.grounded and abs(playerCenterX - checkpointX) <= 23:
            player.checkpointX = respawnX
            spawnParticles(checkpointX, GROUND_Y - 43, COLOR_NEON_GREEN, 10)
            setStatus(f"КОНТРОЛЬНАЯ ТОЧКА {checkpointIndex + 1} СОХРАНЕНА ДЛЯ {player.name}", 2.2)
            break


def respawnPlayer(player: Player, message: str) -> None:
    global deathCount
    spawnParticles(player.x + player.width / 2, player.y + player.height / 2, player.color, 18)
    deathCount += 1
    # Death costs one rune, but it is dropped near the last safe checkpoint so
    # a mistake never forces an unfair backtrack to an inaccessible ledge.
    playerHistory = playerGemHistory[player.element]
    droppedGem = None
    while playerHistory:
        lastGemIndex = playerHistory.pop()
        if lastGemIndex in collectedGems:
            collectedGems.remove(lastGemIndex)
            droppedGem = gemPositions[lastGemIndex]
            break
    player.respawn()
    if droppedGem is not None:
        droppedGem["x"] = clamp(player.checkpointX + 30, 20, WORLD_WIDTH - 20)
        droppedGem["y"] = float(GROUND_Y - 75)
        message = f"{player.name} ПОГИБ — РУНА ПОТЕРЯНА. ПРЫГНИ, ЧТОБЫ ЗАБРАТЬ ЕЁ СНОВА. {message}"
    setStatus(message, 2.8)


def updatePlayer(player: Player, deltaTime: float, keys,
                 keyboardJumpPressed: bool) -> None:
    keyboardHorizontal = float(keys[player.moveKeyRight]) - float(keys[player.moveKeyLeft])
    stickHorizontal, stickJumpPressed = getJoystickInput(player)
    horizontalInput = keyboardHorizontal if keyboardHorizontal != 0 else stickHorizontal
    jumpPressed = keyboardJumpPressed or stickJumpPressed

    if jumpPressed:
        player.jumpBuffer = JUMP_BUFFER_TIME
    else:
        player.jumpBuffer = max(0.0, player.jumpBuffer - deltaTime)

    if player.grounded:
        player.coyoteTimer = COYOTE_TIME
    else:
        player.coyoteTimer = max(0.0, player.coyoteTimer - deltaTime)

    targetVelocity = horizontalInput * PLAYER_SPEED
    acceleration = PLAYER_ACCELERATION if horizontalInput != 0 else PLAYER_FRICTION
    velocityDifference = targetVelocity - player.vx
    player.vx += clamp(velocityDifference, -acceleration * deltaTime, acceleration * deltaTime)
    player.vx = clamp(player.vx, -PLAYER_SPEED, PLAYER_SPEED)

    # Buffered jumping and a short coyote window make platform edges more forgiving.
    if player.jumpBuffer > 0 and player.coyoteTimer > 0:
        player.vy = -JUMP_SPEED
        player.grounded = False
        player.coyoteTimer = 0.0
        player.jumpBuffer = 0.0
        spawnParticles(player.x + player.width / 2, player.y + player.height - 3,
                       player.color, 5)

    # Horizontal collision first. Stop velocity at a wall rather than pushing into it.
    oldX = player.x
    player.x += player.vx * deltaTime
    player.x = clamp(player.x, 0, WORLD_WIDTH - player.width)
    playerRect = player.rect
    for platform in solidPlatforms:
        # Keep ignoring a platform that was head-bumped until the player clears
        # it completely, including during the horizontal part of the update.
        if id(platform) in player.passThroughPlatformIds:
            continue
        if not playerRect.colliderect(platform):
            continue
        if player.vx > 0 and oldX + player.width <= platform.left + 3:
            player.x = platform.left - player.width
            player.vx = 0.0
        elif player.vx < 0 and oldX >= platform.right - 3:
            player.x = platform.right
            player.vx = 0.0
        playerRect = player.rect

    # Vertical collision supports standing, landing, and hitting a platform underside.
    oldY = player.y
    oldBottom = oldY + player.height
    player.y += player.vy * deltaTime
    player.vy = min(player.vy + GRAVITY * deltaTime, MAX_FALL_SPEED)
    player.grounded = False
    playerRect = player.rect

    for platform in solidPlatforms:
        platformId = id(platform)
        if platformId in player.passThroughPlatformIds:
            # Restore collision only after the entire body clears this platform.
            if playerRect.colliderect(platform):
                continue
            player.passThroughPlatformIds.discard(platformId)

        if not playerRect.colliderect(platform):
            continue
        if player.vy >= 0 and oldBottom <= platform.top + 3:
            player.y = float(platform.top - player.height)
            player.vy = 0.0
            player.grounded = True
            playerRect = player.rect
        elif player.vy < 0 and oldY >= platform.bottom - 3:
            # Head-bump rule: don't stop the jump. Temporarily disable collision
            # with this solid platform for this player, so they pass through it.
            # Hazards are checked independently below and remain fully lethal.
            player.passThroughPlatformIds.add(platformId)
            playerRect = player.rect

    # Keep grounded state stable when the player's feet exactly touch a platform
    # (pygame.Rect.colliderect treats edge-touching rectangles as non-overlapping).
    if not player.grounded and player.vy >= 0:
        feetY = player.y + player.height
        for platform in solidPlatforms:
            overlapsHorizontally = player.x + player.width > platform.left and player.x < platform.right
            feetGap = feetY - platform.top
            if overlapsHorizontally and oldBottom <= platform.top + 3 and -1 <= feetGap <= 2:
                player.y = float(platform.top - player.height)
                player.vy = 0.0
                player.grounded = True
                break

    # The correct element can use its own liquid as a temporary floor.
    if not player.grounded and player.vy >= 0:
        newBottom = player.y + player.height
        for hazard in hazards:
            hazardRect = hazard["rect"]
            overlapsHorizontally = player.rect.right > hazardRect.left and player.rect.left < hazardRect.right
            crossedSurface = oldBottom <= hazardRect.top + 3 and newBottom >= hazardRect.top
            if hazard["kind"] != player.weakTo and overlapsHorizontally and crossedSurface:
                player.y = float(hazardRect.top - player.height)
                player.vy = 0.0
                player.grounded = True
                break

    # Wrong-element contact is fatal. The matching liquid remains a safe surface.
    for hazard in hazards:
        if hazard["kind"] == player.weakTo and player.rect.colliderect(hazard["rect"]):
            if player.weakTo == "water":
                respawnPlayer(player, "ОГОНЬ НЕ МОЖЕТ КАСАТЬСЯ ВОДЫ — ПОПРОБУЙ ЕЩЁ РАЗ")
            else:
                respawnPlayer(player, "ВОДА НЕ МОЖЕТ КАСАТЬСЯ ЛАВЫ — ПОПРОБУЙ ЕЩЁ РАЗ")
            return

    for spike in spikeHazards:
        if player.rect.colliderect(spike["rect"]):
            respawnPlayer(player, f"{player.name}: ШИПЫ! ПОПРОБУЙ ПРЫГНУТЬ РАНЬШЕ")
            return

    for saw in sawHazards:
        if player.rect.colliderect(saw["rect"]):
            respawnPlayer(player, f"{player.name}: ПИЛА! ДОЖДИСЬ БЕЗОПАСНОГО МОМЕНТА")
            return

    for gate in laserGates:
        if gate["active"] and player.rect.colliderect(gate["rect"]):
            respawnPlayer(player, f"{player.name}: ЛАЗЕР АКТИВЕН — ДОЖДИСЬ ОКНА")
            return

    if player.y > BASE_HEIGHT + 100:
        respawnPlayer(player, f"{player.name}: ВОЗВРАТ К ПОСЛЕДНЕЙ КОНТРОЛЬНОЙ ТОЧКЕ")
        return

    updateCheckpoints(player)
    player.respawnFlash = max(0.0, player.respawnFlash - deltaTime)


def updateMovingPlatforms(deltaTime: float) -> None:
    """Move Level 3 bridges and carry players who are standing on them."""
    for movingPlatform in movingPlatforms:
        platformRect = movingPlatform["rect"]
        previousX = platformRect.x
        nextX = movingPlatform["positionX"] + movingPlatform["speed"] * movingPlatform["direction"] * deltaTime
        if nextX < movingPlatform["minX"]:
            nextX = movingPlatform["minX"]
            movingPlatform["direction"] = 1.0
        elif nextX > movingPlatform["maxX"]:
            nextX = movingPlatform["maxX"]
            movingPlatform["direction"] = -1.0
        movingPlatform["positionX"] = nextX
        platformRect.x = round(nextX)
        deltaX = platformRect.x - previousX
        if deltaX == 0:
            continue
        for player in players:
            playerBottom = player.y + player.height
            wasStandingOnPlatform = (
                player.grounded
                and abs(playerBottom - platformRect.top) <= 3
                and player.x + player.width > previousX
                and player.x < previousX + platformRect.width
            )
            if wasStandingOnPlatform:
                player.x = clamp(player.x + deltaX, 0, WORLD_WIDTH - player.width)


def updateSaws(deltaTime: float) -> None:
    """Move patrolling saws along authored horizontal tracks."""
    for saw in sawHazards:
        nextX = saw["x"] + saw["speed"] * saw["direction"] * deltaTime
        if nextX < saw["minX"]:
            nextX = saw["minX"]
            saw["direction"] = 1.0
        elif nextX > saw["maxX"]:
            nextX = saw["maxX"]
            saw["direction"] = -1.0
        saw["x"] = nextX
        saw["rect"].x = round(nextX - saw["radius"])


def updateLaserGates() -> None:
    """Advance each independent laser cycle. The visible state matches collision state."""
    for gate in laserGates:
        cyclePosition = (elapsedTime + gate["phase"]) % gate["period"]
        gate["active"] = cyclePosition < gate["activeDuration"]


def updateCrumblePlatforms(deltaTime: float) -> None:
    """Start a short fuse while a player stands on a fragile ledge, then remove it."""
    global solidPlatforms
    for crumble in crumblePlatforms:
        if not crumble["active"]:
            crumble["fallTimer"] = max(0.0, crumble["fallTimer"] - deltaTime)
            continue

        platformRect = crumble["rect"]
        isOccupied = any(
            player.grounded
            and abs((player.y + player.height) - platformRect.top) <= 3
            and player.x + player.width > platformRect.left
            and player.x < platformRect.right
            for player in players
        )
        if isOccupied:
            crumble["triggered"] = True
            crumble["timer"] -= deltaTime
            if crumble["timer"] <= 0:
                crumble["active"] = False
                crumble["fallTimer"] = 0.24
                if platformRect in solidPlatforms:
                    solidPlatforms.remove(platformRect)
                spawnParticles(platformRect.centerx, platformRect.top, (255, 184, 92), 10)
                setStatus("ПЛАТФОРМА РУШИТСЯ! НЕ СТОЙ НА МЕСТЕ", 1.4)


def updateGame(deltaTime: float, keys, keyboardJumpPlayers: set[str]) -> None:
    global elapsedTime, state, cameraX, winTimer, statusTimer, switchActivated
    if state != "PLAY":
        if state == "WIN":
            winTimer += deltaTime
        return

    elapsedTime += deltaTime
    statusTimer = max(0.0, statusTimer - deltaTime)
    updateMovingPlatforms(deltaTime)
    updateSaws(deltaTime)
    updateLaserGates()

    oldFirePosition = (firePlayer.x, firePlayer.y)
    oldWaterPosition = (waterPlayer.x, waterPlayer.y)
    deathsBeforeUpdate = deathCount
    updatePlayer(firePlayer, deltaTime, keys, "fire" in keyboardJumpPlayers)
    updatePlayer(waterPlayer, deltaTime, keys, "water" in keyboardJumpPlayers)
    if activeLevelAttempt is not None and deathCount == deathsBeforeUpdate and deltaTime > 0:
        fireDistance = math.hypot(firePlayer.x - oldFirePosition[0], firePlayer.y - oldFirePosition[1])
        waterDistance = math.hypot(waterPlayer.x - oldWaterPosition[0], waterPlayer.y - oldWaterPosition[1])
        activeLevelAttempt["fire_path_length_px"] += fireDistance
        activeLevelAttempt["water_path_length_px"] += waterDistance
        activeLevelAttempt["fire_max_speed_px_per_second"] = max(
            activeLevelAttempt["fire_max_speed_px_per_second"], fireDistance / deltaTime
        )
        activeLevelAttempt["water_max_speed_px_per_second"] = max(
            activeLevelAttempt["water_max_speed_px_per_second"], waterDistance / deltaTime
        )
    updateCrumblePlatforms(deltaTime)

    # Only the correct character can collect each rune.
    for gemIndex, gem in enumerate(gemPositions):
        if gemIndex in collectedGems:
            continue
        gemRect = pygame.Rect(gem["x"] - 10, gem["y"] - 13, 20, 26)
        ownerPlayer = firePlayer if gem["owner"] == "fire" else waterPlayer
        if ownerPlayer.rect.colliderect(gemRect):
            collectedGems.add(gemIndex)
            playerGemHistory[gem["owner"]].append(gemIndex)
            gemColor = COLOR_FIRE if gem["owner"] == "fire" else COLOR_WATER
            spawnParticles(gem["x"], gem["y"], gemColor, 13)
            playerGemCount = countGems(gem["owner"])
            setStatus(f"{ownerPlayer.name}: РУНА {playerGemCount}/{GEMS_PER_PLAYER}", 1.5)

    fireHasAllGems = countGems("fire") >= GEMS_PER_PLAYER
    waterHasAllGems = countGems("water") >= GEMS_PER_PLAYER

    # Level 2's final gate is a co-op puzzle: after collecting all runes, both
    # heroes must stand on their matching plates at the same time.
    if switchZones and fireHasAllGems and waterHasAllGems and not switchActivated:
        fireOnPlate = firePlayer.rect.colliderect(switchZones["fire"])
        waterOnPlate = waterPlayer.rect.colliderect(switchZones["water"])
        if fireOnPlate and waterOnPlate:
            switchActivated = True
            spawnParticles(switchZones["fire"].centerx, GROUND_Y - 5, COLOR_FIRE, 12)
            spawnParticles(switchZones["water"].centerx, GROUND_Y - 5, COLOR_WATER, 12)
            setStatus("ОБЕ ПЛИТЫ АКТИВИРОВАНЫ — ШЛЮЗ ОТКРЫТ!", 3.5)

    switchesReady = not switchZones or switchActivated
    fireAtExit = firePlayer.rect.colliderect(exitZones["fire"]) and fireHasAllGems and switchesReady
    waterAtExit = waterPlayer.rect.colliderect(exitZones["water"]) and waterHasAllGems and switchesReady

    if fireAtExit and waterAtExit:
        state = "WIN"
        completedLevels.add(selectedLevelIndex)
        capturePlatformerAttempt("completed")
        winTimer = 0.0
        spawnParticles((firePlayer.x + waterPlayer.x) / 2, GROUND_Y - 36,
                       COLOR_NEON_GREEN, 40)
        setStatus("ОБА ГЕРОЯ ДОБРАЛИСЬ ДО ВЫХОДОВ", 10.0)

    # Camera follows the pair, not one character; this keeps cooperation visible.
    pairCenter = (firePlayer.x + waterPlayer.x) / 2 + PLAYER_WIDTH / 2
    targetCamera = clamp(pairCenter - BASE_WIDTH * 0.5, 0, WORLD_WIDTH - BASE_WIDTH)
    cameraX += (targetCamera - cameraX) * min(1.0, deltaTime * 4.6)

    for particle in particles[:]:
        particle["x"] += particle["vx"] * deltaTime
        particle["y"] += particle["vy"] * deltaTime
        particle["vy"] += 360 * deltaTime
        particle["life"] -= deltaTime
        if particle["life"] <= 0:
            particles.remove(particle)


# =============================================================================
# LEVEL SELECT AND MENU RENDERING
# =============================================================================
def drawMiniLevelPreview(rect: pygame.Rect, levelIndex: int, isSelected: bool) -> None:
    """Draw a decorative, compact preview of the selected level's geometry."""
    levelData = LEVEL_DEFINITIONS[levelIndex]
    previewRect = pygame.Rect(rect.x + 12, rect.y + 12, rect.width - 24, 104)
    pygame.draw.rect(screen, (8, 16, 36), previewRect, border_radius=9)
    pygame.draw.rect(screen, (35, 59, 91), previewRect, 1, border_radius=9)
    pygame.draw.circle(screen, (23, 52, 88), (previewRect.right - 27, previewRect.top + 25), 16)
    pygame.draw.circle(screen, (33, 76, 112), (previewRect.right - 27, previewRect.top + 25), 12, 1)

    rawPlatforms = levelData["groundPlatforms"]
    worldWidth = levelData["worldWidth"]
    scaleX = (previewRect.width - 12) / worldWidth
    floorY = previewRect.bottom - 20
    for groundX, _, groundWidth, _ in rawPlatforms:
        x = previewRect.x + 6 + int(groundX * scaleX)
        width = max(3, int(groundWidth * scaleX))
        pygame.draw.rect(screen, (49, 78, 115), (x, floorY, width, 7), border_radius=2)
        pygame.draw.line(screen, COLOR_NEON_BLUE, (x + 1, floorY), (x + width - 1, floorY), 1)
    for hazardX, hazardWidth, hazardKind in levelData["hazards"]:
        x = previewRect.x + 6 + int(hazardX * scaleX)
        width = max(2, int(hazardWidth * scaleX))
        hazardColor = COLOR_WATER if hazardKind == "water" else COLOR_LAVA
        pygame.draw.line(screen, hazardColor, (x, floorY + 4), (x + width, floorY + 4), 3)
    for raisedX, raisedY, raisedWidth, _ in levelData["raisedPlatforms"]:
        x = previewRect.x + 6 + int(raisedX * scaleX)
        y = floorY - max(7, int((GROUND_Y - raisedY) * 0.29))
        width = max(4, int(raisedWidth * scaleX))
        pygame.draw.rect(screen, (69, 98, 137), (x, y, width, 4), border_radius=2)

    for movingX, movingY, movingWidth, _, minX, maxX, _ in levelData.get("movingPlatforms", []):
        railStart = previewRect.x + 6 + int(minX * scaleX)
        railEnd = previewRect.x + 6 + int((maxX + movingWidth) * scaleX)
        railY = floorY - max(7, int((GROUND_Y - movingY) * 0.29)) + 2
        pygame.draw.line(screen, (22, 100, 139), (railStart, railY + 3), (railEnd, railY + 3), 2)
        currentX = previewRect.x + 6 + int(movingX * scaleX)
        currentWidth = max(4, int(movingWidth * scaleX))
        pygame.draw.rect(screen, COLOR_NEON_BLUE, (currentX, railY, currentWidth, 4), border_radius=2)

    # The miniature previews intentionally expose the unique threat profile per level.
    for spikeX, spikeY, spikeWidth, _ in levelData.get("spikes", []):
        x = previewRect.x + 6 + int(spikeX * scaleX)
        width = max(2, int(spikeWidth * scaleX))
        pygame.draw.line(screen, (255, 65, 121), (x, floorY - 3), (x + width, floorY - 3), 2)
    for gateX, gateY, _, _, _, _, _ in levelData.get("laserGates", []):
        x = previewRect.x + 6 + int(gateX * scaleX)
        pygame.draw.line(screen, (255, 61, 115), (x, floorY - 34), (x, floorY + 3), 2)
    for sawX, sawY, radius, _, _, _ in levelData.get("saws", []):
        x = previewRect.x + 6 + int(sawX * scaleX)
        y = floorY - max(7, int((GROUND_Y - sawY) * 0.29))
        pygame.draw.circle(screen, (255, 82, 151), (x, y), 3)
    for crumbleX, crumbleY, crumbleWidth, _, _ in levelData.get("crumblePlatforms", []):
        x = previewRect.x + 6 + int(crumbleX * scaleX)
        y = floorY - max(7, int((GROUND_Y - crumbleY) * 0.29))
        width = max(3, int(crumbleWidth * scaleX))
        pygame.draw.line(screen, (255, 188, 100), (x, y), (x + width, y), 2)

    for switchX, switchY, switchWidth, _ in levelData.get("switches", {}).values():
        x = previewRect.x + 6 + int(switchX * scaleX)
        y = floorY - 3
        width = max(2, int(switchWidth * scaleX))
        pygame.draw.rect(screen, COLOR_NEON_GREEN, (x, y, width, 3), border_radius=1)

    # Tiny element marks frame the preview and clarify the co-op objective.
    pygame.draw.circle(screen, COLOR_FIRE, (previewRect.x + 17, previewRect.y + 18), 4)
    pygame.draw.circle(screen, COLOR_WATER, (previewRect.x + 29, previewRect.y + 18), 4)
    if isSelected:
        pygame.draw.rect(screen, COLOR_NEON_BLUE, previewRect, 1, border_radius=9)


def refreshJoysticks() -> None:
    """Re-enumerate HID controllers without changing a still-connected active device."""
    global joysticks, activeJoystick, selectedControllerIndex
    previousInstanceId = None
    if activeJoystick is not None:
        try:
            previousInstanceId = activeJoystick.get_instance_id()
        except (pygame.error, AttributeError):
            previousInstanceId = None

    joysticks = getConnectedJoysticks()
    matchingJoystick = None
    if previousInstanceId is not None:
        for device in joysticks:
            try:
                if device.get_instance_id() == previousInstanceId:
                    matchingJoystick = device
                    break
            except pygame.error:
                continue

    if matchingJoystick is not None:
        activeJoystick = matchingJoystick
    elif not joysticks:
        activeJoystick = None
    elif activeJoystick is None or previousInstanceId is not None:
        activeJoystick = joysticks[0]

    selectedControllerIndex = min(selectedControllerIndex, len(joysticks))


def confirmControllerSelection() -> None:
    """Commit the selected HID device, or explicitly choose keyboard-only mode."""
    global activeJoystick, state, previousMenuHorizontal, previousMenuVertical
    global previousMenuSelectButton
    if selectedControllerIndex < len(joysticks):
        activeJoystick = joysticks[selectedControllerIndex]
        try:
            activeJoystick.init()
            print(f"Выбран контроллер: {activeJoystick.get_name()}")
            print(f"Оси HID: {activeJoystick.get_numaxes()}, кнопки: {activeJoystick.get_numbuttons()}")
        except pygame.error as error:
            activeJoystick = None
            print(f"Не удалось запустить контроллер: {error}")
    else:
        activeJoystick = None
        print("Выбрано управление с клавиатуры")
    previousMenuHorizontal = 0
    previousMenuVertical = 0
    previousMenuSelectButton = False
    state = "LEVEL_SELECT"


def drawControllerSelectScreen() -> None:
    drawBackground()
    overlay = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    overlay.fill((4, 8, 21, 150))
    screen.blit(overlay, (0, 0))

    blitText(screen, "MOVEUP", fontTiny, COLOR_NEON_BLUE, (38, 25))
    blitText(screen, "ВЫБОР КОНТРОЛЛЕРА", fontHuge, COLOR_TEXT,
             (BASE_WIDTH // 2, 82), center=True)
    blitText(screen, "Выбери HID-устройство для управления двумя персонажами",
             fontSmall, COLOR_MUTED, (BASE_WIDTH // 2, 124), center=True)

    deviceRows = len(joysticks) + 1  # Keyboard-only mode is always available.
    rowHeight = 48
    visibleRows = min(deviceRows, 5)
    listTop = 155
    visibleItems = min(deviceRows, visibleRows)
    for rowIndex in range(visibleItems):
        rowRect = pygame.Rect(150, listTop + rowIndex * (rowHeight + 8), 600, rowHeight)
        isSelected = rowIndex == selectedControllerIndex
        rowFill = (15, 49, 69) if isSelected else (10, 22, 43)
        rowBorder = COLOR_NEON_BLUE if isSelected else (42, 64, 98)
        drawPanel(screen, rowRect, rowFill, rowBorder, radius=9,
                  borderWidth=2 if isSelected else 1)
        if rowIndex < len(joysticks):
            device = joysticks[rowIndex]
            deviceName = device.get_name()
            infoText = f"Оси: {device.get_numaxes()}  •  Кнопки: {device.get_numbuttons()}"
            blitText(screen, f"{rowIndex + 1}. {deviceName[:48]}", font,
                     COLOR_TEXT, (rowRect.x + 16, rowRect.y + 7))
            blitText(screen, infoText, fontTiny, COLOR_MUTED,
                     (rowRect.right - 14, rowRect.centery), center=False)
        else:
            blitText(screen, "Клавиатура", font, COLOR_TEXT,
                     (rowRect.x + 16, rowRect.y + 7))
            blitText(screen, "Без HID-контроллера", fontTiny, COLOR_MUTED,
                     (rowRect.x + 16, rowRect.y + 29))

    startY = min(535, listTop + visibleItems * (rowHeight + 8) + 10)
    startRect = pygame.Rect(286, startY, 328, 48)
    drawPanel(screen, startRect, (13, 54, 78), COLOR_NEON_BLUE, radius=11, borderWidth=2)
    if selectedControllerIndex < len(joysticks):
        startLabel = "ПОДКЛЮЧИТЬ ВЫБРАННЫЙ"
    else:
        startLabel = "ПРОДОЛЖИТЬ С КЛАВИАТУРЫ"
    blitText(screen, startLabel, fontSmall, COLOR_TEXT, startRect.center, center=True)
    blitText(screen, "↑ / ↓ или стик вверх-вниз — выбор  •  Enter — подтвердить  •  F5 — обновить",
             fontTiny, COLOR_MUTED, (BASE_WIDTH // 2, 600), center=True)


def drawLevelSelectScreen() -> None:
    drawBackground()
    overlay = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    overlay.fill((4, 8, 21, 132))
    screen.blit(overlay, (0, 0))

    blitText(screen, "MOVEUP", fontTiny, COLOR_NEON_BLUE, (38, 25))
    blitText(screen, "ELEMENTAL RUN", fontHuge, COLOR_TEXT,
             (BASE_WIDTH // 2, 76), center=True)
    blitText(screen, "ВЫБЕРИ МАРШРУТ ДЛЯ ДВУХ СТИХИЙ", fontSmall, COLOR_MUTED,
             (BASE_WIDTH // 2, 119), center=True)

    cardWidth = 264
    cardHeight = 305
    cardGap = 18
    firstCardX = (BASE_WIDTH - (3 * cardWidth + 2 * cardGap)) // 2
    cardY = 157
    for levelIndex, levelData in enumerate(LEVEL_DEFINITIONS):
        cardX = firstCardX + levelIndex * (cardWidth + cardGap)
        cardRect = pygame.Rect(cardX, cardY, cardWidth, cardHeight)
        isSelected = levelIndex == selectedLevelIndex
        borderColor = COLOR_NEON_BLUE if isSelected else (42, 64, 98)
        fillColor = (16, 30, 56) if isSelected else (10, 21, 43)
        drawPanel(screen, cardRect, fillColor, borderColor, radius=15, borderWidth=2 if isSelected else 1)
        if isSelected:
            pygame.draw.line(screen, COLOR_NEON_BLUE,
                             (cardRect.x + 20, cardRect.y + 2),
                             (cardRect.right - 20, cardRect.y + 2), 3)

        drawMiniLevelPreview(cardRect, levelIndex, isSelected)
        numberColor = COLOR_NEON_BLUE if isSelected else COLOR_MUTED
        blitText(screen, f"УРОВЕНЬ 0{levelIndex + 1}", fontTiny, numberColor,
                 (cardRect.x + 16, cardRect.y + 126))
        blitText(screen, levelData["name"], fontLarge, COLOR_TEXT,
                 (cardRect.x + 15, cardRect.y + 146))
        blitText(screen, levelData["subtitle"], fontTiny, COLOR_MUTED,
                 (cardRect.x + 16, cardRect.y + 181))

        # Difficulty pips are intentionally explicit rather than relying only on color.
        blitText(screen, "СЛОЖНОСТЬ", fontTiny, COLOR_MUTED,
                 (cardRect.x + 16, cardRect.y + 211))
        for pipIndex in range(3):
            pipColor = (COLOR_NEON_GREEN if levelIndex == 0 else
                        COLOR_FIRE if levelIndex == 1 else COLOR_FIRE)
            if pipIndex >= levelData["difficulty"]:
                pipColor = (39, 53, 78)
            pygame.draw.rect(screen, pipColor,
                             (cardRect.x + 16 + pipIndex * 20, cardRect.y + 230, 14, 5),
                             border_radius=2)
        blitText(screen, levelData["description"], fontTiny, COLOR_TEXT,
                 (cardRect.x + 16, cardRect.y + 247))
        objectiveText = levelData.get("objective", "")
        objectiveWords = objectiveText.split()
        objectiveLines = []
        currentLine = ""
        for word in objectiveWords:
            candidateLine = f"{currentLine} {word}".strip()
            if fontTiny.size(candidateLine)[0] > cardRect.width - 32 and currentLine:
                objectiveLines.append(currentLine)
                currentLine = word
            else:
                currentLine = candidateLine
        if currentLine:
            objectiveLines.append(currentLine)
        for lineIndex, objectiveLine in enumerate(objectiveLines[:2]):
            blitText(screen, objectiveLine, fontTiny, COLOR_NEON_BLUE,
                     (cardRect.x + 16, cardRect.y + 266 + lineIndex * 14))

        if levelIndex in completedLevels:
            pygame.draw.circle(screen, COLOR_NEON_GREEN,
                               (cardRect.right - 24, cardRect.y + 139), 8)
            pygame.draw.line(screen, (7, 24, 30),
                             (cardRect.right - 28, cardRect.y + 139),
                             (cardRect.right - 25, cardRect.y + 142), 2)
            pygame.draw.line(screen, (7, 24, 30),
                             (cardRect.right - 25, cardRect.y + 142),
                             (cardRect.right - 20, cardRect.y + 135), 2)

    startRect = pygame.Rect(286, 486, 328, 54)
    drawPanel(screen, startRect, (13, 54, 78), COLOR_NEON_BLUE, radius=12, borderWidth=2)
    blitText(screen, f"НАЧАТЬ: {LEVEL_DEFINITIONS[selectedLevelIndex]['name']}", font,
             COLOR_TEXT, startRect.center, center=True)
    blitText(screen, "A / D или ← / → — выбор     Enter — начать     Esc — выход",
             fontTiny, COLOR_MUTED, (BASE_WIDTH // 2, 572), center=True)
    controllerLabel = activeJoystick.get_name() if activeJoystick else "КЛАВИАТУРА"
    blitText(screen, f"УПРАВЛЕНИЕ: {controllerLabel[:55]}",
             fontTiny, (111, 148, 185), (BASE_WIDTH // 2, 602), center=True)


def startSelectedLevel(levelIndex: Optional[int] = None) -> None:
    global selectedLevelIndex
    if activeLevelAttempt is not None:
        capturePlatformerAttempt("interrupted", shouldMarkSessionAborted=True)
    if levelIndex is not None:
        loadLevel(levelIndex)
    else:
        loadLevel(selectedLevelIndex)
    resetGame()
    beginPlatformerAttempt()


def restartCurrentLevel() -> None:
    if activeLevelAttempt is not None:
        capturePlatformerAttempt("restarted")
    loadLevel(selectedLevelIndex)
    resetGame()
    beginPlatformerAttempt()


def drawWinOverlay() -> None:
    overlay = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    overlay.fill((4, 8, 22, 210))
    screen.blit(overlay, (0, 0))
    panelRect = pygame.Rect(112, 126, 676, 398)
    pygame.draw.rect(screen, (13, 25, 50), panelRect, border_radius=22)
    pygame.draw.rect(screen, COLOR_NEON_GREEN, panelRect, 2, border_radius=22)
    pygame.draw.line(screen, COLOR_FIRE, (panelRect.left + 40, panelRect.top + 2),
                     (panelRect.centerx - 12, panelRect.top + 2), 3)
    pygame.draw.line(screen, COLOR_WATER, (panelRect.centerx + 12, panelRect.top + 2),
                     (panelRect.right - 40, panelRect.top + 2), 3)
    blitText(screen, "УРОВЕНЬ ПРОЙДЕН", fontHuge, COLOR_NEON_GREEN,
             (BASE_WIDTH // 2, 183), center=True)
    blitText(screen, f"{selectedLevelIndex + 1:02d} / {currentLevelName}", fontLarge,
             COLOR_TEXT, (BASE_WIDTH // 2, 229), center=True)
    blitText(screen, "Огонь и Вода сработали как команда", font,
             COLOR_TEXT, (BASE_WIDTH // 2, 273), center=True)
    blitText(screen, f"Время: {elapsedTime:.1f} сек     Руны: {len(collectedGems)}/{len(gemPositions)}     Смерти: {deathCount}",
             font, COLOR_MUTED, (BASE_WIDTH // 2, 309), center=True)
    lastAttempt = lastCompletedLevelAttempt or {}
    blitText(screen,
             f"Траектория Огня: {lastAttempt.get('fire_path_length_px', 0.0):.0f} px   |   Воды: {lastAttempt.get('water_path_length_px', 0.0):.0f} px",
             fontTiny, COLOR_MUTED, (BASE_WIDTH // 2, 337), center=True)

    buttonY = 365
    buttonGap = 12
    buttonWidth = 174
    totalWidth = buttonWidth * 3 + buttonGap * 2
    buttonX = (BASE_WIDTH - totalWidth) // 2
    buttons = [
        (pygame.Rect(buttonX, buttonY, buttonWidth, 46), "ПОВТОРИТЬ", (26, 80, 106)),
        (pygame.Rect(buttonX + buttonWidth + buttonGap, buttonY, buttonWidth, 46),
         "ВЫБОР УРОВНЯ", (26, 65, 91)),
    ]
    for buttonRect, label, fillColor in buttons:
        drawPanel(screen, buttonRect, fillColor, COLOR_NEON_BLUE, radius=9, borderWidth=1)
        blitText(screen, label, fontTiny, COLOR_TEXT, buttonRect.center, center=True)
    if selectedLevelIndex < len(LEVEL_DEFINITIONS) - 1:
        nextRect = pygame.Rect(buttonX + 2 * (buttonWidth + buttonGap), buttonY,
                               buttonWidth, 46)
        drawPanel(screen, nextRect, (25, 76, 50), COLOR_NEON_GREEN, radius=9, borderWidth=1)
        blitText(screen, "СЛЕДУЮЩИЙ", fontTiny, COLOR_NEON_GREEN,
                 nextRect.center, center=True)
    else:
        nextRect = pygame.Rect(buttonX + 2 * (buttonWidth + buttonGap), buttonY,
                               buttonWidth, 46)
        drawPanel(screen, nextRect, (26, 35, 55), (55, 76, 105), radius=9, borderWidth=1)
        blitText(screen, "ФИНАЛ", fontTiny, COLOR_MUTED, nextRect.center, center=True)
    blitText(screen, "R — повторить     L — выбор уровней     N — следующий уровень",
             fontTiny, COLOR_MUTED, (BASE_WIDTH // 2, 443), center=True)


# Start with an explicit controller choice; level 1 is highlighted by default.
loadLevel(0)
resetGame()
state = "CONTROLLER_SELECT"
selectedControllerIndex = 0 if joysticks else len(joysticks)  # keyboard option if no HID was found
previousMenuHorizontal = 0
previousMenuVertical = 0
previousMenuSelectButton = False


# =============================================================================
# MAIN LOOP
# =============================================================================
running = True
while running:
    deltaTime = min(clock.tick(FPS) / 1000.0, 1 / 30)
    keyboardJumpPlayers: set[str] = set()

    # Controller picker uses the vertical axis; level selection uses X.
    menuHorizontal = 0
    menuVertical = 0
    menuSelectPressed = False
    if activeJoystick is not None:
        try:
            if state == "LEVEL_SELECT" and activeJoystick.get_numaxes() > 2:
                menuAxis = float(activeJoystick.get_axis(2))
                if menuAxis < -0.55:
                    menuHorizontal = -1
                elif menuAxis > 0.55:
                    menuHorizontal = 1
            elif state == "CONTROLLER_SELECT" and activeJoystick.get_numaxes() > 3:
                menuAxis = float(activeJoystick.get_axis(3))
                if menuAxis < -0.55:
                    menuVertical = -1
                elif menuAxis > 0.55:
                    menuVertical = 1
            if activeJoystick.get_numbuttons() > 0:
                menuSelectHeld = bool(activeJoystick.get_button(0))
                menuSelectPressed = menuSelectHeld and not previousMenuSelectButton
                previousMenuSelectButton = menuSelectHeld
            else:
                previousMenuSelectButton = False
        except pygame.error:
            previousMenuSelectButton = False

    if state == "CONTROLLER_SELECT" and menuVertical != 0 and menuVertical != previousMenuVertical:
        selectedControllerIndex = (selectedControllerIndex + menuVertical) % (len(joysticks) + 1)
    previousMenuVertical = menuVertical
    if state == "LEVEL_SELECT" and menuHorizontal != 0 and menuHorizontal != previousMenuHorizontal:
        selectedLevelIndex = (selectedLevelIndex + menuHorizontal) % len(LEVEL_DEFINITIONS)
    previousMenuHorizontal = menuHorizontal

    if state == "CONTROLLER_SELECT" and menuSelectPressed:
        confirmControllerSelection()
    elif state == "LEVEL_SELECT" and menuSelectPressed:
        startSelectedLevel(selectedLevelIndex)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if state in ("CONTROLLER_SELECT", "LEVEL_SELECT"):
                    running = False
                else:
                    if state == "PLAY" and activeLevelAttempt is not None:
                        capturePlatformerAttempt("interrupted", shouldMarkSessionAborted=True)
                    state = "LEVEL_SELECT"
            elif state == "CONTROLLER_SELECT":
                if event.key in (pygame.K_UP, pygame.K_w):
                    selectedControllerIndex = (selectedControllerIndex - 1) % (len(joysticks) + 1)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    selectedControllerIndex = (selectedControllerIndex + 1) % (len(joysticks) + 1)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    confirmControllerSelection()
                elif event.key == pygame.K_F5:
                    refreshJoysticks()
            elif state == "LEVEL_SELECT":
                if event.key in (pygame.K_LEFT, pygame.K_a):
                    selectedLevelIndex = (selectedLevelIndex - 1) % len(LEVEL_DEFINITIONS)
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    selectedLevelIndex = (selectedLevelIndex + 1) % len(LEVEL_DEFINITIONS)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    startSelectedLevel(selectedLevelIndex)
                elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    startSelectedLevel(event.key - pygame.K_1)
            elif state == "WIN":
                if event.key == pygame.K_r:
                    restartCurrentLevel()
                elif event.key == pygame.K_l:
                    state = "LEVEL_SELECT"
                elif event.key == pygame.K_n and selectedLevelIndex < len(LEVEL_DEFINITIONS) - 1:
                    startSelectedLevel(selectedLevelIndex + 1)
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if selectedLevelIndex < len(LEVEL_DEFINITIONS) - 1:
                        startSelectedLevel(selectedLevelIndex + 1)
                    else:
                        state = "LEVEL_SELECT"
            else:
                if event.key == pygame.K_r:
                    restartCurrentLevel()
                elif event.key == pygame.K_w:
                    keyboardJumpPlayers.add("fire")
                elif event.key == pygame.K_UP:
                    keyboardJumpPlayers.add("water")
                elif event.key == pygame.K_F1:
                    if activeJoystick:
                        print("Оси HID:", [round(activeJoystick.get_axis(i), 2)
                                           for i in range(activeJoystick.get_numaxes())])
                    else:
                        print("HID не подключён")
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            logicalX = event.pos[0] * BASE_WIDTH / displayWidth
            logicalY = event.pos[1] * BASE_HEIGHT / displayHeight
            clickPos = (int(logicalX), int(logicalY))
            if state == "CONTROLLER_SELECT":
                rowHeight = 48
                listTop = 155
                deviceRows = len(joysticks) + 1
                visibleItems = min(deviceRows, 5)
                for rowIndex in range(visibleItems):
                    rowRect = pygame.Rect(150, listTop + rowIndex * (rowHeight + 8), 600, rowHeight)
                    if rowRect.collidepoint(clickPos):
                        selectedControllerIndex = rowIndex
                        break
                startY = min(535, listTop + visibleItems * (rowHeight + 8) + 10)
                if pygame.Rect(286, startY, 328, 48).collidepoint(clickPos):
                    confirmControllerSelection()
            elif state == "LEVEL_SELECT":
                cardWidth = 264
                cardGap = 18
                firstCardX = (BASE_WIDTH - (3 * cardWidth + 2 * cardGap)) // 2
                for levelIndex in range(len(LEVEL_DEFINITIONS)):
                    cardRect = pygame.Rect(firstCardX + levelIndex * (cardWidth + cardGap), 157,
                                           cardWidth, 305)
                    if cardRect.collidepoint(clickPos):
                        selectedLevelIndex = levelIndex
                        break
                if pygame.Rect(286, 486, 328, 54).collidepoint(clickPos):
                    startSelectedLevel(selectedLevelIndex)
            elif state == "WIN":
                buttonWidth = 174
                buttonGap = 12
                buttonX = (BASE_WIDTH - (buttonWidth * 3 + buttonGap * 2)) // 2
                buttonY = 365
                if pygame.Rect(buttonX, buttonY, buttonWidth, 46).collidepoint(clickPos):
                    restartCurrentLevel()
                elif pygame.Rect(buttonX + buttonWidth + buttonGap, buttonY,
                                 buttonWidth, 46).collidepoint(clickPos):
                    state = "LEVEL_SELECT"
                elif pygame.Rect(buttonX + 2 * (buttonWidth + buttonGap), buttonY,
                                 buttonWidth, 46).collidepoint(clickPos):
                    if selectedLevelIndex < len(LEVEL_DEFINITIONS) - 1:
                        startSelectedLevel(selectedLevelIndex + 1)
        elif event.type == pygame.JOYDEVICEADDED:
            refreshJoysticks()
            if activeJoystick is not None:
                setStatus(f"КОНТРОЛЛЕР ПОДКЛЮЧЁН: {activeJoystick.get_name()}")
        elif event.type == pygame.JOYDEVICEREMOVED:
            refreshJoysticks()
            firePlayer.previousJumpAxis = False
            waterPlayer.previousJumpAxis = False
            previousMenuHorizontal = 0
            previousMenuVertical = 0
            previousMenuSelectButton = False
            if state != "CONTROLLER_SELECT":
                setStatus("HID ОТКЛЮЧЁН — МОЖНО ИГРАТЬ С КЛАВИАТУРЫ")

    keys = pygame.key.get_pressed()
    updateGame(deltaTime, keys, keyboardJumpPlayers)

    if state == "CONTROLLER_SELECT":
        drawControllerSelectScreen()
    elif state == "LEVEL_SELECT":
        drawLevelSelectScreen()
    else:
        drawBackground()
        drawSectionLabels()

        for hazard in hazards:
            drawHazard(hazard, elapsedTime)
        for platform in groundPlatforms + raisedPlatforms:
            drawPlatform(platform)
        for crumblePlatform in crumblePlatforms:
            drawCrumblePlatform(crumblePlatform, elapsedTime)
        for movingPlatform in movingPlatforms:
            drawMovingPlatform(movingPlatform)
        for spike in spikeHazards:
            drawSpikeHazard(spike)
        for gate in laserGates:
            drawLaserGate(gate)
        for saw in sawHazards:
            drawSawHazard(saw, elapsedTime)
        for checkpointIndex, checkpointX in enumerate(checkpointPositions):
            drawCheckpoint(checkpointX, checkpointIndex)
        for switchElement, switchRect in switchZones.items():
            drawSwitch(switchRect, switchElement, switchActivated)

        fireHasGems = countGems("fire") >= GEMS_PER_PLAYER
        waterHasGems = countGems("water") >= GEMS_PER_PLAYER
        switchesReady = not switchZones or switchActivated
        fireUnlocked = fireHasGems and switchesReady
        waterUnlocked = waterHasGems and switchesReady
        drawDoor(exitZones["fire"], COLOR_FIRE, "ОГОНЬ", fireUnlocked,
                 firePlayer.rect.colliderect(exitZones["fire"]) and fireUnlocked)
        drawDoor(exitZones["water"], COLOR_WATER, "ВОДА", waterUnlocked,
                 waterPlayer.rect.colliderect(exitZones["water"]) and waterUnlocked)

        for gemIndex, gem in enumerate(gemPositions):
            drawGem(gem, gemIndex, elapsedTime)
        for player in players:
            drawPlayer(player, elapsedTime)

        for particle in particles:
            alphaRatio = clamp(particle["life"] / max(0.01, particle["maxLife"]), 0, 1)
            radius = max(1, int(particle["radius"] * alphaRatio))
            particleX = worldToScreenX(particle["x"])
            if -10 <= particleX <= BASE_WIDTH + 10:
                pygame.draw.circle(screen, particle["color"],
                                   (particleX, int(particle["y"])), radius)

        drawHud()

        if statusTimer > 0 and state == "PLAY":
            statusColor = COLOR_NEON_GREEN if "РУНА" in statusMessage or "СОХРАНЕНА" in statusMessage else COLOR_NEON_BLUE
            if "НЕ МОЖЕТ" in statusMessage:
                statusColor = COLOR_FIRE if "ОГОНЬ" in statusMessage else COLOR_WATER
            drawStatusToast(statusMessage, statusColor)
        elif state == "PLAY" and (not fireHasGems or not waterHasGems):
            missingElement = "ОГНЯ" if not fireHasGems else "ВОДЫ"
            missingCount = GEMS_PER_PLAYER - (countGems("fire") if not fireHasGems else countGems("water"))
            drawStatusToast(f"СОБЕРИ ЕЩЁ {missingCount} РУН {missingElement} — ВЫХОД ЗАКРЫТ", COLOR_NEON_BLUE)
        elif state == "PLAY" and switchZones and not switchActivated:
            drawStatusToast("ВСТАНЬТЕ НА ДВЕ ПЛИТЫ ОДНОВРЕМЕННО", COLOR_FIRE if fireHasGems else COLOR_NEON_BLUE)
        elif state == "PLAY" and (spikeHazards or sawHazards or laserGates or crumblePlatforms or movingPlatforms):
            if selectedLevelIndex == 1:
                drawStatusToast("ШИПЫ ОПАСНЫ • ЖДИ НЕАКТИВНОГО ЛАЗЕРА И НЕ МЕДЛИ НА ПЛИТАХ", COLOR_FIRE)
            elif selectedLevelIndex == 2:
                drawStatusToast("СЛЕДИ ЗА ДВИЖЕНИЕМ МОСТОВ, ПИЛ И ЦИКЛАМИ ЛАЗЕРОВ", COLOR_FIRE)
            else:
                drawStatusToast("ШИПЫ И ПИЛЫ СМЕРТЕЛЬНЫ • ЛАЗЕРЫ ОТКЛЮЧАЮТСЯ ПО ЦИКЛУ", COLOR_FIRE)
        elif state == "PLAY":
            drawStatusToast("ОБА ВЫХОДА ОТКРЫТЫ — КАЖДЫЙ ГЕРОЙ ИДЁТ К СВОЕЙ ДВЕРИ", COLOR_NEON_GREEN)

        if state == "WIN":
            drawWinOverlay()

    scaledFrame = pygame.transform.smoothscale(screen, (displayWidth, displayHeight))
    displayScreen.blit(scaledFrame, (0, 0))
    pygame.display.flip()

    statsNow = time.monotonic()
    if statsNow - lastStatsSaveTime >= 2.0:
        savePlatformerStatistics("interrupted", includeActiveAttempt=True)
        lastStatsSaveTime = statsNow

finalizePlatformerStatistics()

if activeJoystick:
    try:
        activeJoystick.quit()
    except pygame.error:
        pass
pygame.quit()
sys.exit()
