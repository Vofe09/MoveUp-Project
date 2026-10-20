import json
import math
import os
import random
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Optional

import pygame

# =====================================================================
# MOVEUP · NEON HIGHWAY
# Прототип аркадной игры: четыре полосы, два направления движения.
# Совместим с контроллером из примера Cyber Shooter и лаунчером MoveUp.
# =====================================================================
GAME_TITLE = "Neon Highway"
GAME_ID = os.environ.get("MOVEUP_GAME_ID", "race").strip() or "neon_highway"
STATS_SCHEMA_VERSION = 1
METRICS_VERSION = 1
STATS_AUTOSAVE_INTERVAL = 2.0

BASE_WIDTH = 1280
BASE_HEIGHT = 720
FPS_LIMIT = 60

HORIZON_Y = 128
ROAD_BOTTOM_Y = 720
ROAD_CENTER_X = BASE_WIDTH // 2
ROAD_HALF_WIDTH_TOP = 118
ROAD_HALF_WIDTH_BOTTOM = 475
PLAYER_Y = 590
PLAYER_WIDTH = 68
PLAYER_HEIGHT = 112

STEERING_PRESETS = {
    "Низкая": 0.62,
    "Средняя": 1.0,
    "Высокая": 1.38,
    "Максимальная": 1.8,
}
STEERING_NAMES = list(STEERING_PRESETS.keys())
DEFAULT_STEERING_INDEX = 1
JOYSTICK_DEADZONE = 0.14
# Как в исходном примере Cyber Shooter: положительное значение оси A2
# смещает цель влево. Для стандартной раскладки можно поставить 1.0.
JOYSTICK_STEERING_SIGN = -1.0

# Palette: midnight blue, cool cyan, warm road lights.
COLORS = {
    "skyTop": (8, 14, 34),
    "skyBottom": (32, 61, 83),
    "text": (241, 248, 255),
    "muted": (155, 178, 199),
    "cyan": (83, 239, 232),
    "cyanDeep": (23, 168, 177),
    "amber": (255, 190, 96),
    "coral": (255, 91, 113),
    "road": (30, 39, 53),
    "roadLight": (47, 57, 70),
    "lane": (209, 224, 230),
    "white": (255, 255, 255),
    "panel": (11, 21, 39),
    "green": (103, 240, 172),
}

pygame.init()
pygame.joystick.init()

pygame.display.set_caption("MoveUp · Neon Highway")
displayInfo = pygame.display.Info()
displayWidth = displayInfo.current_w or BASE_WIDTH
displayHeight = displayInfo.current_h or BASE_HEIGHT
isFullscreen = True
try:
    display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
except pygame.error:
    isFullscreen = False
    display = pygame.display.set_mode((BASE_WIDTH, BASE_HEIGHT), pygame.RESIZABLE)
displayWidth, displayHeight = display.get_size()
logicalSurface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT)).convert()
clock = pygame.time.Clock()

fontSmall = pygame.font.SysFont("dejavusans", 15)
fontTiny = pygame.font.SysFont("dejavusans", 12)
fontBody = pygame.font.SysFont("dejavusans", 19, bold=True)
fontMedium = pygame.font.SysFont("dejavusans", 25, bold=True)
fontLarge = pygame.font.SysFont("dejavusans", 46, bold=True)
fontTitle = pygame.font.SysFont("dejavusans", 74, bold=True)

# ---------------------------------------------------------------------
# Session / statistics compatible with the MoveUp shared statistics file
# ---------------------------------------------------------------------
sessionId = os.environ.get("MOVEUP_SESSION_ID", "").strip() or str(uuid.uuid4())
processStartedAt = datetime.now(timezone.utc)
processStartedMonotonic = time.monotonic()


def resolveStatsFilePath():
    executablePath = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    scriptDirectory = executablePath.parent
    candidateDirectories = [scriptDirectory, *scriptDirectory.parents]

    for directory in candidateDirectories:
        if directory.name.casefold() == "games":
            return str(directory / "statistics.json")

    try:
        gamesDirectory = next(
            (child for child in scriptDirectory.iterdir()
             if child.is_dir() and child.name.casefold() == "games"),
            None,
        )
    except OSError:
        gamesDirectory = None
    if gamesDirectory is not None:
        return str(gamesDirectory / "statistics.json")
    return str(scriptDirectory / "statistics.json")


statsFilePath = resolveStatsFilePath()
handoffStatsFilePath = os.environ.get("MOVEUP_STATS_FILE", "").strip()
if handoffStatsFilePath:
    handoffStatsFilePath = os.path.abspath(os.path.expanduser(handoffStatsFilePath))
try:
    statsSchemaVersion = max(1, int(os.environ.get("MOVEUP_STATS_SCHEMA_VERSION", str(STATS_SCHEMA_VERSION))))
except (TypeError, ValueError):
    statsSchemaVersion = STATS_SCHEMA_VERSION


def makeMetric(label, value, unit):
    if isinstance(value, float):
        value = round(value, 3)
    return {"label": label, "value": value, "unit": unit}


# A run is added to runHistory exactly once when it ends or is interrupted.
runHistory = []
lastStatsSaveTime = 0.0
hasStartedAtLeastOneRun = False


def utcNowString():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------
# Input device discovery
# ---------------------------------------------------------------------
joystick: Optional[pygame.joystick.Joystick] = None
availableJoysticks = []
previousButtons = [0, 0, 0, 0]


def refreshJoysticks():
    global availableJoysticks, joystick
    availableJoysticks = []
    for index in range(pygame.joystick.get_count()):
        try:
            device = pygame.joystick.Joystick(index)
            device.init()
            availableJoysticks.append(device)
        except pygame.error:
            continue
    joystick = availableJoysticks[0] if availableJoysticks else None
    if joystick:
        print("Контроллер MoveUp:", joystick.get_name())
        print("Оси:", joystick.get_numaxes(), "Кнопки:", joystick.get_numbuttons())
    else:
        print("Контроллер не найден — доступно управление с клавиатуры.")


refreshJoysticks()


def readButton(device, buttonIndex):
    if device is None:
        return 0
    try:
        return int(buttonIndex < device.get_numbuttons() and device.get_button(buttonIndex))
    except pygame.error:
        return 0


def readSteeringAxis(device):
    if device is None:
        return 0.0
    try:
        # A2 — основная горизонтальная ось в приложенном примере.
        primaryAxis = device.get_axis(2) if device.get_numaxes() > 2 else 0.0
        secondaryAxis = device.get_axis(0) if device.get_numaxes() > 0 else 0.0
        axisValue = primaryAxis if abs(primaryAxis) > JOYSTICK_DEADZONE else secondaryAxis
        if abs(axisValue) < JOYSTICK_DEADZONE:
            return 0.0
        # Убираем скачок сразу после deadzone, сохраняя полный ход стика.
        normalizedValue = (abs(axisValue) - JOYSTICK_DEADZONE) / (1.0 - JOYSTICK_DEADZONE)
        return math.copysign(min(1.0, normalizedValue), axisValue) * JOYSTICK_STEERING_SIGN
    except pygame.error:
        return 0.0


# ---------------------------------------------------------------------
# Static environment art: skyline, mountain silhouettes and sky gradient
# ---------------------------------------------------------------------
random.seed(24)
skySurface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
for y in range(BASE_HEIGHT):
    ratio = min(1.0, y / (HORIZON_Y + 160))
    red = int(COLORS["skyTop"][0] * (1 - ratio) + COLORS["skyBottom"][0] * ratio)
    green = int(COLORS["skyTop"][1] * (1 - ratio) + COLORS["skyBottom"][1] * ratio)
    blue = int(COLORS["skyTop"][2] * (1 - ratio) + COLORS["skyBottom"][2] * ratio)
    pygame.draw.line(skySurface, (red, green, blue), (0, y), (BASE_WIDTH, y))

# Distant glow at the horizon.
horizonGlow = pygame.Surface((BASE_WIDTH, 180), pygame.SRCALPHA)
for glowIndex in range(22, 0, -1):
    glowAlpha = int(2 + (22 - glowIndex) * 0.5)
    glowRect = pygame.Rect(
        BASE_WIDTH // 2 - glowIndex * 28,
        90 - glowIndex * 3,
        glowIndex * 56,
        glowIndex * 7 + 2,
    )
    pygame.draw.ellipse(horizonGlow, (76, 205, 211, glowAlpha), glowRect)
skySurface.blit(horizonGlow, (0, HORIZON_Y - 100))

# Mountains in multiple layers.
farMountains = [(0, HORIZON_Y + 3)]
for x in range(0, BASE_WIDTH + 80, 80):
    farMountains.append((x, HORIZON_Y - random.randint(6, 36)))
farMountains.extend([(BASE_WIDTH, HORIZON_Y + 12), (BASE_WIDTH, HORIZON_Y + 60), (0, HORIZON_Y + 60)])
pygame.draw.polygon(skySurface, (29, 52, 76), farMountains)
nearMountains = [(0, HORIZON_Y + 24)]
for x in range(0, BASE_WIDTH + 100, 100):
    nearMountains.append((x, HORIZON_Y + random.randint(-8, 26)))
nearMountains.extend([(BASE_WIDTH, HORIZON_Y + 70), (0, HORIZON_Y + 70)])
pygame.draw.polygon(skySurface, (22, 43, 65), nearMountains)

# A low, soft-edged futuristic skyline.
skylineX = 0
while skylineX < BASE_WIDTH:
    buildingWidth = random.randint(18, 48)
    buildingHeight = random.randint(12, 55)
    buildingRect = pygame.Rect(skylineX, HORIZON_Y - buildingHeight + 12, buildingWidth, buildingHeight)
    pygame.draw.rect(skySurface, (16, 34, 51), buildingRect)
    if buildingWidth > 24:
        for windowX in range(buildingRect.x + 6, buildingRect.right - 4, 10):
            for windowY in range(buildingRect.y + 7, buildingRect.bottom - 3, 12):
                if random.random() < 0.42:
                    pygame.draw.rect(skySurface, (77, 144, 157), (windowX, windowY, 3, 4))
    skylineX += buildingWidth + random.randint(3, 10)

# Tiny lights in the upper sky.
for _ in range(72):
    starX = random.randrange(BASE_WIDTH)
    starY = random.randrange(18, HORIZON_Y - 38)
    starSize = random.choice((1, 1, 2))
    pygame.draw.circle(skySurface, (87, 137, 167), (starX, starY), starSize)


def clamp(value, minValue, maxValue):
    return max(minValue, min(maxValue, value))


def lerp(startValue, endValue, ratio):
    return startValue + (endValue - startValue) * ratio


def roadHalfWidthAtY(y):
    ratio = clamp((y - HORIZON_Y) / max(1, ROAD_BOTTOM_Y - HORIZON_Y), 0.0, 1.0)
    return lerp(ROAD_HALF_WIDTH_TOP, ROAD_HALF_WIDTH_BOTTOM, ratio)


def roadEdgesAtY(y):
    halfWidth = roadHalfWidthAtY(y)
    return ROAD_CENTER_X - halfWidth, ROAD_CENTER_X + halfWidth


def laneCenterAtY(laneIndex, y):
    leftEdge, rightEdge = roadEdgesAtY(y)
    laneWidth = (rightEdge - leftEdge) / 4.0
    return leftEdge + laneWidth * (laneIndex + 0.5)


def laneWidthAtY(y):
    leftEdge, rightEdge = roadEdgesAtY(y)
    return (rightEdge - leftEdge) / 4.0


def projectDepth(depth):
    depth = clamp(depth, 0.0, 1.1)
    normalizedDepth = min(1.0, depth)
    perspectiveRatio = normalizedDepth ** 1.28
    y = HORIZON_Y + perspectiveRatio * (ROAD_BOTTOM_Y - HORIZON_Y)
    scale = 0.20 + perspectiveRatio * 1.18
    return y, scale


def drawText(surface, text, font, color, position, align="topleft"):
    rendered = font.render(str(text), True, color)
    rect = rendered.get_rect()
    setattr(rect, align, position)
    surface.blit(rendered, rect)
    return rect


def drawTextCentered(surface, text, font, color, center, shadowColor=None):
    if shadowColor:
        shadow = font.render(str(text), True, shadowColor)
        shadowRect = shadow.get_rect(center=(center[0] + 2, center[1] + 3))
        surface.blit(shadow, shadowRect)
    rendered = font.render(str(text), True, color)
    rect = rendered.get_rect(center=center)
    surface.blit(rendered, rect)
    return rect


def drawRoundedPanel(surface, rect, fill=(11, 21, 39, 222), outline=(88, 152, 171), radius=18, outlineWidth=1):
    panelSurface = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    pygame.draw.rect(panelSurface, fill, panelSurface.get_rect(), border_radius=radius)
    if outlineWidth:
        pygame.draw.rect(panelSurface, (*outline, 170), panelSurface.get_rect(), outlineWidth, border_radius=radius)
    surface.blit(panelSurface, rect.topleft)


def drawGlowLine(surface, startPoint, endPoint, color, coreWidth=2, glowWidth=10):
    glowSurface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    pygame.draw.line(glowSurface, (*color, 24), startPoint, endPoint, glowWidth)
    pygame.draw.line(glowSurface, (*color, 65), startPoint, endPoint, max(coreWidth + 2, 4))
    surface.blit(glowSurface, (0, 0))
    pygame.draw.line(surface, color, startPoint, endPoint, coreWidth)


def drawRoad(surface, roadFlow, menuMode=False):
    surface.blit(skySurface, (0, 0))

    # Atmospheric haze below the skyline.
    hazeSurface = pygame.Surface((BASE_WIDTH, 115), pygame.SRCALPHA)
    pygame.draw.ellipse(hazeSurface, (59, 139, 151, 24), (90, 0, 1100, 105))
    surface.blit(hazeSurface, (0, HORIZON_Y - 15))

    topLeft, topRight = roadEdgesAtY(HORIZON_Y)
    bottomLeft, bottomRight = roadEdgesAtY(ROAD_BOTTOM_Y)
    roadPolygon = [(topLeft, HORIZON_Y), (topRight, HORIZON_Y), (bottomRight, ROAD_BOTTOM_Y), (bottomLeft, ROAD_BOTTOM_Y)]
    pygame.draw.polygon(surface, (11, 20, 34), [(-40, HORIZON_Y), (topLeft, HORIZON_Y), (bottomLeft, ROAD_BOTTOM_Y), (-40, ROAD_BOTTOM_Y)])
    pygame.draw.polygon(surface, (11, 20, 34), [(topRight, HORIZON_Y), (BASE_WIDTH + 40, HORIZON_Y), (BASE_WIDTH + 40, ROAD_BOTTOM_Y), (bottomRight, ROAD_BOTTOM_Y)])
    pygame.draw.polygon(surface, COLORS["road"], roadPolygon)

    # Asphalt texture: long, subtle panels that flow toward the player.
    for textureIndex in range(12):
        depth = (textureIndex / 12.0 + roadFlow * 0.55) % 1.0
        y, _ = projectDepth(depth)
        leftEdge, rightEdge = roadEdgesAtY(y)
        lineAlphaColor = (34, 45, 59) if textureIndex % 2 else (32, 43, 57)
        pygame.draw.line(surface, lineAlphaColor, (int(leftEdge), int(y)), (int(rightEdge), int(y)), max(1, int(1 + depth * 2)))

    # Outer shoulders.
    for sideSign in (-1, 1):
        points = []
        for y in (HORIZON_Y, ROAD_BOTTOM_Y):
            leftEdge, rightEdge = roadEdgesAtY(y)
            edge = leftEdge if sideSign < 0 else rightEdge
            shoulderWidth = 9 if y == HORIZON_Y else 28
            points.append((edge, y))
            points.append((edge + sideSign * shoulderWidth, y))
        if sideSign < 0:
            shoulderPolygon = [points[0], points[2], points[3], points[1]]
        else:
            shoulderPolygon = [points[0], points[1], points[3], points[2]]
        pygame.draw.polygon(surface, (81, 98, 108), shoulderPolygon)

    # Outer edge strips.
    for sideSign in (-1, 1):
        topX = ROAD_CENTER_X + sideSign * ROAD_HALF_WIDTH_TOP
        bottomX = ROAD_CENTER_X + sideSign * ROAD_HALF_WIDTH_BOTTOM
        pygame.draw.line(surface, (115, 224, 219), (topX, HORIZON_Y), (bottomX, ROAD_BOTTOM_Y), 3)
        pygame.draw.line(surface, (55, 123, 133), (topX + sideSign * 4, HORIZON_Y), (bottomX + sideSign * 14, ROAD_BOTTOM_Y), 2)

    # Three lane boundaries: a bright double amber divider separates traffic directions.
    for dividerIndex in (1, 2, 3):
        topLeftEdge, topRightEdge = roadEdgesAtY(HORIZON_Y)
        lineTopX = topLeftEdge + (topRightEdge - topLeftEdge) * dividerIndex / 4.0
        bottomLeftEdge, bottomRightEdge = roadEdgesAtY(ROAD_BOTTOM_Y)
        lineBottomX = bottomLeftEdge + (bottomRightEdge - bottomLeftEdge) * dividerIndex / 4.0
        if dividerIndex == 2:
            pygame.draw.line(surface, (160, 111, 62), (int(lineTopX - 3), HORIZON_Y), (int(lineBottomX - 3), ROAD_BOTTOM_Y), 4)
            pygame.draw.line(surface, COLORS["amber"], (int(lineTopX + 3), HORIZON_Y), (int(lineBottomX + 3), ROAD_BOTTOM_Y), 3)
        else:
            pygame.draw.line(surface, (62, 78, 91), (int(lineTopX), HORIZON_Y), (int(lineBottomX), ROAD_BOTTOM_Y), 5)
            # Dash marks are depth projected rather than uniformly spaced in screen space.
            for dashIndex in range(10):
                depth = (dashIndex / 10.0 + roadFlow) % 1.0
                if depth < 0.035:
                    continue
                dashEndDepth = min(1.0, depth + 0.045 + depth * 0.03)
                dashY1, _ = projectDepth(depth)
                dashY2, _ = projectDepth(dashEndDepth)
                dashX1 = lerp(ROAD_CENTER_X - roadHalfWidthAtY(dashY1), ROAD_CENTER_X + roadHalfWidthAtY(dashY1), dividerIndex / 4.0)
                dashX2 = lerp(ROAD_CENTER_X - roadHalfWidthAtY(dashY2), ROAD_CENTER_X + roadHalfWidthAtY(dashY2), dividerIndex / 4.0)
                pygame.draw.line(surface, COLORS["lane"], (int(dashX1), int(dashY1)), (int(dashX2), int(dashY2)), max(1, int(1 + depth * 5)))

    # Direction arrows make the four-lane logic immediately readable.
    for laneIndex in range(4):
        arrowDepth = (0.16 + laneIndex * 0.07 + roadFlow * 0.22) % 0.55 + 0.18
        arrowY, arrowScale = projectDepth(arrowDepth)
        arrowX = laneCenterAtY(laneIndex, arrowY)
        size = max(3, int(8 * arrowScale))
        direction = 1 if laneIndex >= 2 else -1
        arrowColor = (83, 173, 177) if laneIndex >= 2 else (176, 118, 83)
        shaftTop = arrowY - size * 1.4 * direction
        shaftBottom = arrowY + size * 1.4 * direction
        pygame.draw.line(surface, arrowColor, (int(arrowX), int(shaftTop)), (int(arrowX), int(shaftBottom)), max(1, int(size / 3)))
        headEndY = shaftTop if direction > 0 else shaftBottom
        headDirection = -1 if direction > 0 else 1
        pygame.draw.line(surface, arrowColor, (int(arrowX), int(headEndY)), (int(arrowX - size * 0.7), int(headEndY + size * headDirection)), max(1, int(size / 3)))
        pygame.draw.line(surface, arrowColor, (int(arrowX), int(headEndY)), (int(arrowX + size * 0.7), int(headEndY + size * headDirection)), max(1, int(size / 3)))

    # Repeating posts / reflectors on both sides add depth and motion cues.
    for postIndex in range(10):
        depth = (postIndex / 10.0 + roadFlow * 0.70) % 1.0
        y, scale = projectDepth(depth)
        leftEdge, rightEdge = roadEdgesAtY(y)
        postHeight = max(4, int(8 + depth * 27))
        sideOffset = 16 + depth * 20
        for sideSign, edge in ((-1, leftEdge), (1, rightEdge)):
            postX = int(edge + sideSign * sideOffset)
            postWidth = max(1, int(2 + scale * 1.4))
            pygame.draw.line(surface, (57, 100, 114), (postX, int(y)), (postX, int(y - postHeight)), postWidth)
            reflectorColor = COLORS["coral"] if postIndex % 3 == 0 else COLORS["cyan"]
            pygame.draw.circle(surface, reflectorColor, (postX, int(y - postHeight)), max(1, int(1 + scale * 1.2)))

    # A thin central highlight creates the long, clean vanishing point.
    pygame.draw.line(surface, (51, 88, 105), (ROAD_CENTER_X, HORIZON_Y), (ROAD_CENTER_X, ROAD_BOTTOM_Y), 1)


def drawCar(surface, centerX, bottomY, carWidth, carHeight, bodyColor, isPlayer=False, isOncoming=False, tilt=0.0, damaged=False):
    centerX = int(centerX)
    bottomY = int(bottomY)
    carWidth = max(18, int(carWidth))
    carHeight = max(24, int(carHeight))
    left = centerX - carWidth // 2
    right = centerX + carWidth // 2
    top = bottomY - carHeight
    tiltOffset = int(tilt * carWidth * 0.12)

    # Drop shadow.
    shadowRect = pygame.Rect(left - 5, bottomY - 7, carWidth + 10, max(8, carHeight // 4))
    pygame.draw.ellipse(surface, (5, 10, 19), shadowRect)

    # Wheel shapes.
    wheelWidth = max(3, int(carWidth * 0.12))
    wheelHeight = max(7, int(carHeight * 0.22))
    wheelY1 = top + int(carHeight * 0.28)
    wheelY2 = top + int(carHeight * 0.62)
    for wheelX in (left - wheelWidth // 2, right - wheelWidth // 2):
        pygame.draw.rect(surface, (7, 12, 20), (wheelX, wheelY1, wheelWidth, wheelHeight), border_radius=max(1, wheelWidth // 3))
        pygame.draw.rect(surface, (7, 12, 20), (wheelX, wheelY2, wheelWidth, wheelHeight), border_radius=max(1, wheelWidth // 3))

    # Main body, slightly tapered at the roof and front.
    bodyPolygon = [
        (centerX - carWidth * 0.37 + tiltOffset, top + carHeight * 0.12),
        (centerX + carWidth * 0.37 + tiltOffset, top + carHeight * 0.12),
        (right + tiltOffset // 2, top + carHeight * 0.43),
        (right - carWidth * 0.05, bottomY - carHeight * 0.06),
        (centerX + carWidth * 0.28, bottomY),
        (centerX - carWidth * 0.28, bottomY),
        (left + carWidth * 0.05, bottomY - carHeight * 0.06),
        (left - tiltOffset // 2, top + carHeight * 0.43),
    ]
    if damaged:
        bodyColor = (118, 129, 139)
    pygame.draw.polygon(surface, bodyColor, bodyPolygon)
    outlineColor = (153, 255, 248) if isPlayer else tuple(min(255, channel + 38) for channel in bodyColor)
    pygame.draw.polygon(surface, outlineColor, bodyPolygon, max(1, int(carWidth * 0.025)))

    # Hood / trunk highlight.
    panelColor = tuple(min(255, int(channel * 1.20 + 12)) for channel in bodyColor)
    panelPolygon = [
        (centerX - carWidth * 0.27, top + carHeight * 0.50),
        (centerX + carWidth * 0.27, top + carHeight * 0.50),
        (centerX + carWidth * 0.21, bottomY - carHeight * 0.18),
        (centerX - carWidth * 0.21, bottomY - carHeight * 0.18),
    ]
    pygame.draw.polygon(surface, panelColor, panelPolygon)

    # Cabin / windshield.
    cabinTop = top + carHeight * 0.20
    cabinBottom = top + carHeight * 0.49
    cabinPolygon = [
        (centerX - carWidth * 0.25 + tiltOffset // 2, cabinTop),
        (centerX + carWidth * 0.25 + tiltOffset // 2, cabinTop),
        (centerX + carWidth * 0.34, cabinBottom),
        (centerX - carWidth * 0.34, cabinBottom),
    ]
    windowColor = (13, 37, 53) if not isOncoming else (20, 42, 58)
    pygame.draw.polygon(surface, windowColor, cabinPolygon)
    pygame.draw.polygon(surface, (83, 151, 167), cabinPolygon, max(1, int(carWidth * 0.025)))
    windowHighlightY = cabinTop + (cabinBottom - cabinTop) * 0.28
    pygame.draw.line(surface, (81, 143, 159), (int(centerX - carWidth * 0.18), int(windowHighlightY)), (int(centerX + carWidth * 0.18), int(windowHighlightY)), max(1, int(carWidth * 0.018)))

    if isOncoming:
        # Bright headlights on cars travelling toward the player.
        lampY = top + carHeight * 0.69
        lampWidth = max(3, int(carWidth * 0.16))
        for lampX in (centerX - carWidth * 0.29, centerX + carWidth * 0.29):
            pygame.draw.ellipse(surface, (255, 222, 153), (int(lampX - lampWidth / 2), int(lampY), lampWidth, max(3, int(carHeight * 0.07))))
            pygame.draw.line(surface, (255, 205, 122), (int(lampX), int(lampY + carHeight * 0.06)), (int(lampX), int(lampY + carHeight * 0.15)), max(1, int(carWidth * 0.025)))
        grilleRect = pygame.Rect(centerX - carWidth * 0.17, int(bottomY - carHeight * 0.20), carWidth * 0.34, max(2, carHeight * 0.045))
        pygame.draw.rect(surface, (7, 17, 26), grilleRect, border_radius=2)
    else:
        # Rear lights and bumper.
        lampY = bottomY - carHeight * 0.19
        lampWidth = max(3, int(carWidth * 0.15))
        for lampX in (centerX - carWidth * 0.29, centerX + carWidth * 0.29):
            pygame.draw.rect(surface, (255, 77, 94), (int(lampX - lampWidth / 2), int(lampY), lampWidth, max(4, int(carHeight * 0.08))), border_radius=2)
        pygame.draw.line(surface, (24, 42, 53), (left + carWidth * 0.18, int(bottomY - carHeight * 0.08)), (right - carWidth * 0.18, int(bottomY - carHeight * 0.08)), max(1, int(carWidth * 0.035)))
        if isPlayer:
            pygame.draw.line(surface, COLORS["cyan"], (left + carWidth * 0.28, int(bottomY - carHeight * 0.10)), (right - carWidth * 0.28, int(bottomY - carHeight * 0.10)), max(2, int(carWidth * 0.035)))


def drawCoin(surface, centerX, bottomY, size, pulsePhase):
    centerX = int(centerX)
    bottomY = int(bottomY)
    radius = max(4, int(size))
    glowRadius = radius + int(3 + 2 * math.sin(pulsePhase))
    glowSurface = pygame.Surface((glowRadius * 4, glowRadius * 4), pygame.SRCALPHA)
    pygame.draw.circle(glowSurface, (255, 188, 78, 28), (glowRadius * 2, glowRadius * 2), glowRadius * 2)
    pygame.draw.circle(glowSurface, (255, 188, 78, 48), (glowRadius * 2, glowRadius * 2), int(glowRadius * 1.35))
    surface.blit(glowSurface, (centerX - glowRadius * 2, bottomY - glowRadius * 2))
    pygame.draw.circle(surface, (255, 184, 76), (centerX, bottomY), radius)
    pygame.draw.circle(surface, (255, 232, 167), (centerX, bottomY), max(2, int(radius * 0.63)), 2)
    pygame.draw.line(surface, (255, 246, 204), (centerX, bottomY - radius // 2), (centerX, bottomY + radius // 2), max(1, radius // 5))


# ---------------------------------------------------------------------
# Gameplay state and run metrics
# ---------------------------------------------------------------------
game = {
    "state": "MENU",
    "score": 0,
    "distanceMeters": 0.0,
    "elapsedSeconds": 0.0,
    "crashes": 0,
    "nearMisses": 0,
    "overtakes": 0,
    "bonusPickups": 0,
    "laneChanges": 0,
    "steeringPathPx": 0.0,
    "maxSpeedKmh": 0.0,
    "speedKmh": 132.0,
    "playerX": laneCenterAtY(2, PLAYER_Y + PLAYER_HEIGHT * 0.28),
    "playerTilt": 0.0,
    "traffic": [],
    "pickups": [],
    "roadFlow": 0.0,
    "trafficSpawnTimer": 0.7,
    "pickupSpawnTimer": 1.9,
    "collisionCooldown": 0.0,
    "crashFlash": 0.0,
    "runStartedAt": 0.0,
    "runId": None,
    "runStartedAtUtc": None,
    "runCaptured": False,
    "previousLane": 2,
    "lastPlayerX": laneCenterAtY(2, PLAYER_Y + PLAYER_HEIGHT * 0.28),
    "messageTimer": 0.0,
    "message": "",
    "difficultyBoosts": 0,
    "trafficIntensity": 1.0,
}
steeringPresetIndex = DEFAULT_STEERING_INDEX


def getPlayerBounds():
    # The player may use all four lanes, including the oncoming side.
    leftEdge, rightEdge = roadEdgesAtY(PLAYER_Y + PLAYER_HEIGHT * 0.50)
    halfCar = PLAYER_WIDTH * 0.47
    minPlayerX = leftEdge + halfCar + 10
    maxPlayerX = rightEdge - halfCar - 10
    return minPlayerX, maxPlayerX


def getPlayerLane():
    lanePositions = [laneCenterAtY(index, PLAYER_Y + PLAYER_HEIGHT * 0.30) for index in range(4)]
    return min(range(4), key=lambda index: abs(lanePositions[index] - game["playerX"]))


def showMessage(message, duration=1.4):
    game["message"] = message
    game["messageTimer"] = duration


def resetRun():
    global steeringPresetIndex, hasStartedAtLeastOneRun
    minPlayerX, maxPlayerX = getPlayerBounds()
    game.update({
        "score": 0,
        "distanceMeters": 0.0,
        "elapsedSeconds": 0.0,
        "crashes": 0,
        "nearMisses": 0,
        "overtakes": 0,
        "bonusPickups": 0,
        "laneChanges": 0,
        "steeringPathPx": 0.0,
        "maxSpeedKmh": 132.0,
        "speedKmh": 132.0,
        "playerX": laneCenterAtY(2, PLAYER_Y + PLAYER_HEIGHT * 0.28),
        "playerTilt": 0.0,
        "traffic": [],
        "pickups": [],
        "roadFlow": 0.0,
        "trafficSpawnTimer": 0.35,
        "pickupSpawnTimer": 1.7,
        "collisionCooldown": 0.0,
        "crashFlash": 0.0,
        "runStartedAt": time.monotonic(),
        "runId": str(uuid.uuid4()),
        "runStartedAtUtc": utcNowString(),
        "runCaptured": False,
        "previousLane": 2,
        "lastPlayerX": laneCenterAtY(2, PLAYER_Y + PLAYER_HEIGHT * 0.28),
        "messageTimer": 0.0,
        "message": "",
        "difficultyBoosts": 0,
        "trafficIntensity": 1.0,
    })
    # Keep controller sensitivity selection across retries, as in Cyber Shooter.
    hasStartedAtLeastOneRun = True
    game["state"] = "PLAY"


def spawnTrafficCar():
    laneIndex = random.choices((0, 1, 2, 3), weights=(0.25, 0.25, 0.27, 0.23), k=1)[0]
    carStyles = [
        (183, 202, 215),
        (239, 105, 109),
        (95, 166, 193),
        (222, 168, 93),
        (143, 133, 202),
        (114, 202, 166),
        (219, 218, 196),
    ]
    return {
        "lane": laneIndex,
        "depth": random.uniform(0.02, 0.10),
        "speedFactor": random.uniform(0.82, 1.12) if laneIndex >= 2 else random.uniform(1.20, 1.55),
        "color": random.choice(carStyles),
        "sizeFactor": random.uniform(0.87, 1.14),
        "passed": False,
        "nearMissCounted": False,
        "oncoming": laneIndex < 2,
    }


def spawnPickup():
    return {"lane": random.choice((0, 1, 2, 3)), "depth": random.uniform(0.03, 0.08), "phase": random.random() * math.tau}


def buildRunSummary(completed):
    return {
        "run_id": game.get("runId"),
        "run_number": len(runHistory) + 1,
        "started_at": game.get("runStartedAtUtc") or utcNowString(),
        "ended_at": utcNowString(),
        "status": "completed" if completed else "interrupted",
        "completed": bool(completed),
        "durationSeconds": round(max(0.0, game["elapsedSeconds"]), 3),
        "score": int(game["score"]),
        "distanceMeters": round(game["distanceMeters"], 3),
        "crashes": int(game["crashes"]),
        "nearMisses": int(game["nearMisses"]),
        "overtakes": int(game["overtakes"]),
        "bonusPickups": int(game["bonusPickups"]),
        "laneChanges": int(game["laneChanges"]),
        "difficultyBoosts": int(game["difficultyBoosts"]),
        "maxTrafficIntensity": round(game["trafficIntensity"], 3),
        "steeringPathPx": round(game["steeringPathPx"], 3),
        "maxSpeedKmh": round(game["maxSpeedKmh"], 3),
        "averageSpeedKmh": round(game["distanceMeters"] / max(game["elapsedSeconds"], 0.001) * 3.6, 3),
        "steeringPreset": STEERING_NAMES[steeringPresetIndex],
    }


def captureRun(completed):
    if game["runCaptured"]:
        return
    runHistory.append(buildRunSummary(completed))
    game["runCaptured"] = True
    saveStatsFile("completed" if completed else "interrupted")


def buildSessionResult(sessionStatus, includeCurrentRun=False):
    runs = list(runHistory)
    if includeCurrentRun and game["state"] in ("PLAY", "PAUSE") and not game["runCaptured"]:
        runs.append(buildRunSummary(False))

    runsPlayed = len(runs)
    completedRuns = sum(1 for run in runs if run["completed"])
    totalDuration = sum(run["durationSeconds"] for run in runs)
    totalDistance = sum(run["distanceMeters"] for run in runs)
    totalCrashes = sum(run["crashes"] for run in runs)
    totalNearMisses = sum(run["nearMisses"] for run in runs)
    totalOvertakes = sum(run["overtakes"] for run in runs)
    totalPickups = sum(run["bonusPickups"] for run in runs)
    totalLaneChanges = sum(run["laneChanges"] for run in runs)
    totalDifficultyBoosts = sum(run.get("difficultyBoosts", 0) for run in runs)
    maxTrafficIntensity = max((run.get("maxTrafficIntensity", 1.0) for run in runs), default=1.0)
    totalSteeringPath = sum(run["steeringPathPx"] for run in runs)
    totalScore = sum(run["score"] for run in runs)
    maxSpeed = max((run["maxSpeedKmh"] for run in runs), default=0.0)
    averageSpeed = totalDistance / totalDuration * 3.6 if totalDuration > 0 else 0.0
    sensitivityUsed = sorted({run["steeringPreset"] for run in runs})

    metrics = {
        "runsPlayed": makeMetric("Игровые заезды", runsPlayed, "шт."),
        "runsCompleted": makeMetric("Завершённые заезды", completedRuns, "шт."),
        "score": makeMetric("Суммарный счёт", totalScore, "очков"),
        "distanceKm": makeMetric("Общая дистанция", totalDistance / 1000.0, "км"),
        "activeGameplaySeconds": makeMetric("Время за рулём", totalDuration, "сек."),
        "collisions": makeMetric("Столкновения", totalCrashes, "шт."),
        "nearMisses": makeMetric("Опасные сближения", totalNearMisses, "шт."),
        "overtakes": makeMetric("Обгоны", totalOvertakes, "шт."),
        "bonusPickups": makeMetric("Собранные бонусы", totalPickups, "шт."),
        "laneChanges": makeMetric("Смены полосы", totalLaneChanges, "шт."),
        "difficultyBoosts": makeMetric("Уровни усложнения", totalDifficultyBoosts, "нажатий"),
        "maxTrafficIntensity": makeMetric("Максимальная интенсивность трафика", maxTrafficIntensity, "x"),
        "averageSpeedKmh": makeMetric("Средняя скорость", averageSpeed, "км/ч"),
        "maxSpeedKmh": makeMetric("Максимальная скорость", maxSpeed, "км/ч"),
        "steeringPathPx": makeMetric("Траектория руления", totalSteeringPath, "px"),
        "sensitivityPresetsUsedCount": makeMetric("Режимы чувствительности", len(sensitivityUsed), "режимов"),
    }
    return {
        "schema_version": statsSchemaVersion,
        "metrics_version": METRICS_VERSION,
        "session_id": sessionId,
        "game_id": GAME_ID,
        "game_title": GAME_TITLE,
        "started_at": processStartedAt.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ended_at": utcNowString(),
        "duration_seconds": round(max(0.0, time.monotonic() - processStartedMonotonic), 3),
        "status": sessionStatus,
        "metrics": metrics,
        "details": {"rounds": runs},
        "settings": {
            "logical_resolution_px": {"width": BASE_WIDTH, "height": BASE_HEIGHT},
            "lane_count": 4,
            "same_direction_lanes": [3, 4],
            "oncoming_lanes": [1, 2],
            "player_can_enter_oncoming_lanes": True,
            "steering_preset_used": sensitivityUsed,
            "joystick_deadzone": JOYSTICK_DEADZONE,
            "difficulty_boost_button_index": 2,
            "difficulty_boost_stacks_per_run": True,
            "player_can_enter_oncoming_lanes": True,
        },
    }


def writeJsonAtomically(destinationPath, content):
    directoryPath = os.path.dirname(os.path.abspath(destinationPath))
    temporaryPath = None
    os.makedirs(directoryPath, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=directoryPath,
            prefix=".moveup-stats-", suffix=".tmp", delete=False,
        ) as temporaryFile:
            json.dump(content, temporaryFile, ensure_ascii=False, indent=2, allow_nan=False)
            temporaryFile.write("\n")
            temporaryPath = temporaryFile.name
        os.replace(temporaryPath, destinationPath)
    finally:
        if temporaryPath and os.path.exists(temporaryPath):
            try:
                os.remove(temporaryPath)
            except OSError:
                pass


def saveStatsFile(sessionStatus, includeCurrentRun=False):
    result = buildSessionResult(sessionStatus, includeCurrentRun)
    try:
        if os.path.exists(statsFilePath):
            with open(statsFilePath, "r", encoding="utf-8") as existingFile:
                history = json.load(existingFile)
            if isinstance(history, dict) and isinstance(history.get("sessions"), list):
                sessions = history["sessions"]
            elif isinstance(history, dict) and history.get("session_id"):
                sessions = [history]
                history = {"schema_version": statsSchemaVersion, "sessions": sessions}
            else:
                raise ValueError(f"Неподдерживаемый формат файла статистики: {statsFilePath}")
        else:
            history = {"schema_version": statsSchemaVersion, "sessions": []}
            sessions = history["sessions"]

        existingIndex = next(
            (index for index, item in enumerate(sessions)
             if isinstance(item, dict) and item.get("session_id") == sessionId),
            None,
        )
        if existingIndex is None:
            sessions.append(result)
        else:
            sessions[existingIndex] = result
        history["schema_version"] = statsSchemaVersion
        history["sessions"] = sessions
        writeJsonAtomically(statsFilePath, history)

        if handoffStatsFilePath and os.path.abspath(handoffStatsFilePath) != os.path.abspath(statsFilePath):
            writeJsonAtomically(handoffStatsFilePath, result)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"Не удалось сохранить статистику MoveUp: {error}")


# ---------------------------------------------------------------------
# Drawing the interface
# ---------------------------------------------------------------------
def drawLives(surface, lives):
    drawText(surface, "СОСТОЯНИЕ", fontTiny, COLORS["muted"], (34, 111))
    for index in range(3):
        centerX = 43 + index * 27
        centerY = 140
        color = COLORS["coral"] if index < lives else (54, 66, 82)
        pygame.draw.circle(surface, color, (centerX - 4, centerY - 2), 7)
        pygame.draw.circle(surface, color, (centerX + 4, centerY - 2), 7)
        pygame.draw.polygon(surface, color, [(centerX - 11, centerY), (centerX + 11, centerY), (centerX, centerY + 13)])


def drawHud(surface):
    # HUD is made of small translucent panels, avoiding a huge opaque banner.
    drawRoundedPanel(surface, pygame.Rect(22, 20, 228, 94), fill=(8, 19, 34, 214), outline=(63, 125, 142), radius=16)
    drawText(surface, "DISTANCE", fontTiny, COLORS["muted"], (40, 36))
    drawText(surface, f"{game['distanceMeters'] / 1000.0:05.2f}", fontMedium, COLORS["text"], (38, 54))
    drawText(surface, "KM", fontSmall, COLORS["cyan"], (158, 67))
    drawText(surface, f"SCORE  {game['score']:,}".replace(",", " "), fontBody, COLORS["amber"], (40, 88))

    drawRoundedPanel(surface, pygame.Rect(BASE_WIDTH - 225, 20, 202, 94), fill=(8, 19, 34, 214), outline=(63, 125, 142), radius=16)
    drawText(surface, "SPEED", fontTiny, COLORS["muted"], (BASE_WIDTH - 205, 36))
    drawText(surface, f"{int(game['speedKmh'])}", fontLarge, COLORS["text"], (BASE_WIDTH - 204, 48))
    drawText(surface, "KM/H", fontSmall, COLORS["cyan"], (BASE_WIDTH - 108, 72))

    drawRoundedPanel(surface, pygame.Rect(22, 126, 112, 60), fill=(8, 19, 34, 196), outline=(63, 125, 142), radius=13)
    drawLives(surface, 3 - game["crashes"])

    # Small directional label explains the two-way road.
    drawRoundedPanel(surface, pygame.Rect(BASE_WIDTH // 2 - 183, 20, 366, 42), fill=(8, 19, 34, 166), outline=(49, 99, 121), radius=13)
    drawText(surface, "←  ONCOMING", fontTiny, (255, 167, 117), (BASE_WIDTH // 2 - 157, 34), align="midleft")
    pygame.draw.line(surface, COLORS["amber"], (BASE_WIDTH // 2, 28), (BASE_WIDTH // 2, 54), 2)
    drawText(surface, "SAME WAY  →", fontTiny, COLORS["cyan"], (BASE_WIDTH // 2 + 15, 34), align="midleft")

    drawText(surface, f"ОБГОНЫ  {game['overtakes']}", fontSmall, COLORS["text"], (30, BASE_HEIGHT - 38))
    drawText(surface, f"ОПАСНЫЕ СБЛИЖЕНИЯ  {game['nearMisses']}", fontSmall, COLORS["text"], (220, BASE_HEIGHT - 38))
    drawText(surface, f"РУЛЕНИЕ: {STEERING_NAMES[steeringPresetIndex].upper()}", fontSmall, COLORS["muted"], (BASE_WIDTH - 290, BASE_HEIGHT - 58))
    boostColor = COLORS["coral"] if game["difficultyBoosts"] else COLORS["muted"]
    drawText(surface, f"ТРАФИК ×{game['trafficIntensity']:.1f}  ·  УСЛОЖНЕНИЕ {game['difficultyBoosts']}", fontSmall, boostColor, (BASE_WIDTH - 390, BASE_HEIGHT - 35))


def drawMenu(surface, animationTime):
    drawRoad(surface, game["roadFlow"], menuMode=True)
    # Slight dark veil keeps title readable without flattening the background.
    veil = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(veil, (4, 12, 27, 112), (0, 0, BASE_WIDTH, BASE_HEIGHT))
    surface.blit(veil, (0, 0))

    # Brand tag.
    drawRoundedPanel(surface, pygame.Rect(54, 44, 188, 38), fill=(8, 23, 40, 205), outline=(68, 165, 169), radius=12)
    pygame.draw.circle(surface, COLORS["cyan"], (75, 63), 5)
    drawText(surface, "MOVEUP  /  ARCADE", fontTiny, COLORS["text"], (91, 56))

    drawText(surface, "NEON", fontTitle, COLORS["text"], (BASE_WIDTH // 2, 130), align="midtop")
    drawText(surface, "HIGHWAY", fontTitle, COLORS["cyan"], (BASE_WIDTH // 2, 203), align="midtop")
    drawTextCentered(surface, "ЧЕТЫРЕ ПОЛОСЫ. ДВА НАПРАВЛЕНИЯ. ТВОЙ МАРШРУТ.", fontSmall, (203, 223, 235), (BASE_WIDTH // 2, 285))

    # Decorative player car; it sits on the same-direction side.
    playerX = laneCenterAtY(2, PLAYER_Y + 6) + math.sin(animationTime * 1.2) * 5
    drawCar(surface, playerX, 592, PLAYER_WIDTH + 8, PLAYER_HEIGHT + 12, (40, 190, 195), isPlayer=True, tilt=math.sin(animationTime) * 0.06)

    # Instructions card.
    instructionRect = pygame.Rect(BASE_WIDTH // 2 - 278, 390, 556, 128)
    drawRoundedPanel(surface, instructionRect, fill=(6, 17, 32, 221), outline=(67, 143, 159), radius=20, outlineWidth=2)
    drawTextCentered(surface, "УПРАВЛЕНИЕ", fontBody, COLORS["text"], (BASE_WIDTH // 2, 416))
    drawTextCentered(surface, "ДЖОЙСТИК  ← / →     ИЛИ     A / D,  ← / →", fontSmall, COLORS["cyan"], (BASE_WIDTH // 2, 445))
    drawTextCentered(surface, "Встречная полоса открыта: пересекай центр дороги на свой риск.", fontSmall, COLORS["muted"], (BASE_WIDTH // 2, 474))
    drawTextCentered(surface, "КНОПКА 3 / F — навсегда усилить трафик в этом заезде", fontSmall, COLORS["coral"], (BASE_WIDTH // 2, 500))

    pulseAlpha = 0.70 + 0.30 * math.sin(animationTime * 3.0)
    promptColor = (int(104 * pulseAlpha), int(248 * pulseAlpha), int(233 * pulseAlpha))
    drawTextCentered(surface, "ENTER  /  КНОПКА 1  —  НАЧАТЬ", fontBody, promptColor, (BASE_WIDTH // 2, 566))
    controllerLabel = f"Контроллер: {joystick.get_name()}" if joystick else "Контроллер не найден · доступна клавиатура"
    drawTextCentered(surface, controllerLabel, fontTiny, (159, 184, 197), (BASE_WIDTH // 2, 614))
    drawTextCentered(surface, "ESC — выход     F11 — полноэкранный режим", fontTiny, (131, 155, 173), (BASE_WIDTH // 2, 647))


def drawEndScreen(surface, animationTime):
    drawRoad(surface, game["roadFlow"])
    veil = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(veil, (4, 9, 20, 185), (0, 0, BASE_WIDTH, BASE_HEIGHT))
    surface.blit(veil, (0, 0))

    cardRect = pygame.Rect(BASE_WIDTH // 2 - 365, 72, 730, 570)
    drawRoundedPanel(surface, cardRect, fill=(8, 19, 35, 244), outline=(78, 160, 166), radius=26, outlineWidth=2)
    drawTextCentered(surface, "ЗАЕЗД ЗАВЕРШЁН", fontLarge, COLORS["text"], (BASE_WIDTH // 2, 126))
    drawTextCentered(surface, "Маршрут пройден. Вот твой результат.", fontSmall, COLORS["muted"], (BASE_WIDTH // 2, 165))

    # Three headline metrics.
    headlineY = 205
    headlineCards = [
        (cardRect.x + 34, "СЧЁТ", f"{game['score']:,}".replace(",", " "), COLORS["amber"]),
        (cardRect.x + 255, "ДИСТАНЦИЯ", f"{game['distanceMeters'] / 1000.0:.2f} км", COLORS["cyan"]),
        (cardRect.x + 476, "ВРЕМЯ", f"{int(game['elapsedSeconds']) // 60:02}:{int(game['elapsedSeconds']) % 60:02}", COLORS["text"]),
    ]
    for cardX, label, value, valueColor in headlineCards:
        miniRect = pygame.Rect(cardX, headlineY, 205, 100)
        drawRoundedPanel(surface, miniRect, fill=(18, 35, 52, 200), outline=(63, 112, 133), radius=16)
        drawTextCentered(surface, label, fontTiny, COLORS["muted"], (miniRect.centerx, miniRect.y + 24))
        drawTextCentered(surface, value, fontMedium, valueColor, (miniRect.centerx, miniRect.y + 61))

    lineY = 332
    pygame.draw.line(surface, (60, 98, 119), (cardRect.x + 36, lineY), (cardRect.right - 36, lineY), 1)
    leftStats = [
        ("Столкновения", f"{game['crashes']} / 3"),
        ("Обгоны", str(game["overtakes"])),
        ("Опасные сближения", str(game["nearMisses"])),
    ]
    rightStats = [
        ("Собрано импульсов", str(game["bonusPickups"])),
        ("Смены полосы", str(game["laneChanges"])),
        ("Усложнение трафика", f"×{game['trafficIntensity']:.1f}  ({game['difficultyBoosts']})"),
    ]
    for index, (label, value) in enumerate(leftStats):
        y = 365 + index * 43
        drawText(surface, label, fontSmall, COLORS["muted"], (cardRect.x + 45, y))
        drawText(surface, value, fontBody, COLORS["text"], (cardRect.x + 300, y), align="topright")
    for index, (label, value) in enumerate(rightStats):
        y = 365 + index * 43
        drawText(surface, label, fontSmall, COLORS["muted"], (cardRect.x + 385, y))
        drawText(surface, value, fontBody, COLORS["text"], (cardRect.right - 45, y), align="topright")

    buttonY = 535
    retryRect = pygame.Rect(BASE_WIDTH // 2 - 288, buttonY, 265, 55)
    menuRect = pygame.Rect(BASE_WIDTH // 2 + 23, buttonY, 265, 55)
    drawRoundedPanel(surface, retryRect, fill=(18, 81, 87, 235), outline=COLORS["cyan"], radius=14, outlineWidth=2)
    drawRoundedPanel(surface, menuRect, fill=(24, 38, 54, 235), outline=(94, 125, 149), radius=14, outlineWidth=2)
    drawTextCentered(surface, "ENTER  ·  ЕЩЁ ЗАЕЗД", fontBody, COLORS["text"], retryRect.center)
    drawTextCentered(surface, "ESC  ·  В ГЛАВНОЕ МЕНЮ", fontSmall, COLORS["text"], menuRect.center)
    drawTextCentered(surface, "Статистика сохраняется автоматически в statistics.json", fontTiny, (135, 160, 178), (BASE_WIDTH // 2, 613))


def drawPauseOverlay(surface):
    overlaySurface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(overlaySurface, (3, 10, 24, 175), (0, 0, BASE_WIDTH, BASE_HEIGHT))
    surface.blit(overlaySurface, (0, 0))
    panelRect = pygame.Rect(BASE_WIDTH // 2 - 215, BASE_HEIGHT // 2 - 100, 430, 200)
    drawRoundedPanel(surface, panelRect, fill=(8, 19, 35, 245), outline=(76, 176, 177), radius=24, outlineWidth=2)
    drawTextCentered(surface, "ПАУЗА", fontLarge, COLORS["text"], (BASE_WIDTH // 2, BASE_HEIGHT // 2 - 38))
    drawTextCentered(surface, "P / КНОПКА 2 — ПРОДОЛЖИТЬ", fontSmall, COLORS["cyan"], (BASE_WIDTH // 2, BASE_HEIGHT // 2 + 18))
    drawTextCentered(surface, "ESC — выйти в меню", fontTiny, COLORS["muted"], (BASE_WIDTH // 2, BASE_HEIGHT // 2 + 50))


def drawFloatingMessage(surface):
    if game["messageTimer"] <= 0 or not game["message"]:
        return
    alphaFactor = min(1.0, game["messageTimer"] / 0.24)
    messageSurface = fontBody.render(game["message"], True, COLORS["text"])
    messageSurface.set_alpha(int(255 * alphaFactor))
    messageRect = messageSurface.get_rect(center=(BASE_WIDTH // 2, 195))
    panelRect = messageRect.inflate(36, 20)
    panelSurface = pygame.Surface(panelRect.size, pygame.SRCALPHA)
    pygame.draw.rect(panelSurface, (7, 18, 32, int(205 * alphaFactor)), panelSurface.get_rect(), border_radius=14)
    pygame.draw.rect(panelSurface, (*COLORS["cyan"], int(150 * alphaFactor)), panelSurface.get_rect(), 1, border_radius=14)
    surface.blit(panelSurface, panelRect.topleft)
    surface.blit(messageSurface, messageRect)


def updatePlayState(frameDelta, steerInput):
    # Difficulty rises gently with distance, but steering remains continuous.
    previousElapsed = game["elapsedSeconds"]
    game["elapsedSeconds"] += frameDelta
    game["speedKmh"] = min(188.0, 132.0 + game["elapsedSeconds"] * 0.72)
    game["maxSpeedKmh"] = max(game["maxSpeedKmh"], game["speedKmh"])
    game["distanceMeters"] += (game["speedKmh"] / 3.6) * frameDelta
    game["score"] += int(frameDelta * 11)
    game["roadFlow"] = (game["roadFlow"] + frameDelta * (0.15 + game["speedKmh"] / 210.0)) % 1.0
    game["collisionCooldown"] = max(0.0, game["collisionCooldown"] - frameDelta)
    game["crashFlash"] = max(0.0, game["crashFlash"] - frameDelta)
    game["messageTimer"] = max(0.0, game["messageTimer"] - frameDelta)

    # Joystick and keys are blended, so a keyboard nudge remains possible.
    keys = pygame.key.get_pressed()
    keyboardSteer = float(keys[pygame.K_RIGHT] or keys[pygame.K_d]) - float(keys[pygame.K_LEFT] or keys[pygame.K_a])
    if abs(keyboardSteer) > 0:
        steerInput = keyboardSteer

    previousX = game["playerX"]
    steeringSpeed = 365.0 * STEERING_PRESETS[STEERING_NAMES[steeringPresetIndex]]
    game["playerX"] += steerInput * steeringSpeed * frameDelta
    minPlayerX, maxPlayerX = getPlayerBounds()
    game["playerX"] = clamp(game["playerX"], minPlayerX, maxPlayerX)
    deltaX = game["playerX"] - previousX
    game["steeringPathPx"] += abs(deltaX)
    targetTilt = clamp(deltaX / max(1.0, steeringSpeed * frameDelta), -1.0, 1.0) * 0.30
    game["playerTilt"] = lerp(game["playerTilt"], targetTilt, min(1.0, frameDelta * 7.0))

    currentLane = getPlayerLane()
    if currentLane != game["previousLane"]:
        game["laneChanges"] += 1
        game["previousLane"] = currentLane

    # Traffic stream: far cars become larger as they approach.
    game["trafficSpawnTimer"] -= frameDelta
    if game["trafficSpawnTimer"] <= 0:
        game["traffic"].append(spawnTrafficCar())
        # Each optional boost stacks: spawn traffic more frequently and increase
        # the relative closing speed of both same-direction and oncoming cars.
        spawnInterval = random.uniform(0.66, 1.12) * max(0.64, 1.0 - game["elapsedSeconds"] * 0.003)
        spawnInterval /= game["trafficIntensity"]
        game["trafficSpawnTimer"] = max(0.24, spawnInterval)

    game["pickupSpawnTimer"] -= frameDelta
    if game["pickupSpawnTimer"] <= 0:
        if random.random() < 0.83:
            game["pickups"].append(spawnPickup())
        game["pickupSpawnTimer"] = random.uniform(2.2, 3.6)

    # The player is moving forward automatically; opposing traffic closes faster.
    for trafficCar in game["traffic"][:]:
        relativeRate = 0.19 + game["speedKmh"] / 520.0
        relativeRate *= trafficCar["speedFactor"] * game["trafficIntensity"]
        if trafficCar["oncoming"]:
            relativeRate += 0.22 * game["trafficIntensity"]
        trafficCar["depth"] += relativeRate * frameDelta
        carY, carScale = projectDepth(trafficCar["depth"])
        carX = laneCenterAtY(trafficCar["lane"], carY)
        carWidth = laneWidthAtY(carY) * 0.47 * trafficCar["sizeFactor"]
        carHeight = carWidth * 1.28

        # Collisions are active on all four lanes, including the oncoming side.
        if (
            game["collisionCooldown"] <= 0
            and 0.79 <= trafficCar["depth"] <= 0.98
            and abs(game["playerX"] - carX) < (PLAYER_WIDTH * 0.42 + carWidth * 0.39)
        ):
            game["crashes"] += 1
            game["collisionCooldown"] = 1.45
            game["crashFlash"] = 0.42
            game["score"] = max(0, game["score"] - 180)
            game["traffic"].remove(trafficCar)
            showMessage("СТОЛКНОВЕНИЕ  −180", 1.25)
            if game["crashes"] >= 3:
                game["state"] = "END"
                captureRun(True)
                return
            continue

        if trafficCar["depth"] >= 1.025:
            # Passing a same-direction car counts as an overtake; oncoming cars do not.
            if trafficCar["lane"] >= 2 and not trafficCar["passed"]:
                trafficCar["passed"] = True
                game["overtakes"] += 1
                game["score"] += 55
                if abs(game["playerX"] - carX) < PLAYER_WIDTH * 0.95 and not trafficCar["nearMissCounted"]:
                    game["nearMisses"] += 1
                    game["score"] += 35
                    showMessage("ЧИСТОЕ РАСХОЖДЕНИЕ  +35", 0.9)
            game["traffic"].remove(trafficCar)

    for pickup in game["pickups"][:]:
        pickup["depth"] += (0.19 + game["speedKmh"] / 520.0) * frameDelta
        pickupY, pickupScale = projectDepth(pickup["depth"])
        pickupX = laneCenterAtY(pickup["lane"], pickupY)
        if 0.82 <= pickup["depth"] <= 0.98 and abs(game["playerX"] - pickupX) < PLAYER_WIDTH * 0.59:
            game["bonusPickups"] += 1
            game["score"] += 125
            game["pickups"].remove(pickup)
            showMessage("ИМПУЛЬС  +125", 0.95)
        elif pickup["depth"] > 1.03:
            game["pickups"].remove(pickup)

    if game["crashFlash"] > 0:
        game["roadFlow"] = (game["roadFlow"] + frameDelta * 0.28) % 1.0


def drawGameplay(surface):
    drawRoad(surface, game["roadFlow"])

    # Opposing traffic is drawn first, then same-direction traffic for clearer overlap.
    for trafficCar in sorted(game["traffic"], key=lambda item: item["depth"]):
        y, scale = projectDepth(trafficCar["depth"])
        laneIndex = trafficCar["lane"]
        centerX = laneCenterAtY(laneIndex, y)
        laneWidth = laneWidthAtY(y)
        carWidth = laneWidth * 0.47 * trafficCar["sizeFactor"]
        carHeight = carWidth * 1.28
        drawCar(
            surface,
            centerX,
            y + carHeight * 0.26,
            carWidth,
            carHeight,
            trafficCar["color"],
            isPlayer=False,
            isOncoming=trafficCar["oncoming"],
        )

    for pickup in game["pickups"]:
        y, scale = projectDepth(pickup["depth"])
        centerX = laneCenterAtY(pickup["lane"], y)
        drawCoin(surface, centerX, y, max(4, int(7 * scale)), time.monotonic() * 3.0 + pickup["phase"])

    # Player car with a subtle cyan aura.
    if game["collisionCooldown"] <= 0 or int(time.monotonic() * 12) % 2 == 0:
        glowRect = pygame.Rect(int(game["playerX"] - PLAYER_WIDTH * 0.65), PLAYER_Y - PLAYER_HEIGHT, int(PLAYER_WIDTH * 1.3), PLAYER_HEIGHT + 25)
        glowSurface = pygame.Surface(glowRect.size, pygame.SRCALPHA)
        pygame.draw.ellipse(glowSurface, (53, 232, 229, 27), (0, 15, glowRect.width, glowRect.height - 5))
        surface.blit(glowSurface, glowRect.topleft)
        drawCar(
            surface,
            game["playerX"],
            PLAYER_Y + PLAYER_HEIGHT * 0.35,
            PLAYER_WIDTH,
            PLAYER_HEIGHT,
            (38, 177, 190),
            isPlayer=True,
            tilt=game["playerTilt"],
        )

    drawHud(surface)
    drawFloatingMessage(surface)

    if game["crashFlash"] > 0:
        flashSurface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
        pygame.draw.rect(flashSurface, (255, 65, 75, int(60 * game["crashFlash"] / 0.42)), (0, 0, BASE_WIDTH, BASE_HEIGHT))
        surface.blit(flashSurface, (0, 0))


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------
running = True
animationTime = 0.0
while running:
    frameDelta = min(0.05, max(0.001, clock.tick(FPS_LIMIT) / 1000.0))
    animationTime += frameDelta
    game["roadFlow"] = (game["roadFlow"] + frameDelta * 0.065) % 1.0 if game["state"] == "MENU" else game["roadFlow"]
    steerInput = readSteeringAxis(joystick)
    clicked = False

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.JOYDEVICEADDED or event.type == pygame.JOYDEVICEREMOVED:
            refreshJoysticks()
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if game["state"] == "PLAY" or game["state"] == "PAUSE":
                    if game["state"] in ("PLAY", "PAUSE") and not game["runCaptured"]:
                        captureRun(False)
                    game["state"] = "MENU"
                elif game["state"] == "END":
                    game["state"] = "MENU"
                else:
                    running = False
            elif event.key == pygame.K_F11:
                if isFullscreen:
                    display = pygame.display.set_mode((BASE_WIDTH, BASE_HEIGHT), pygame.RESIZABLE)
                    isFullscreen = False
                else:
                    display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                    isFullscreen = True
                displayWidth, displayHeight = display.get_size()
            elif game["state"] == "MENU" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                resetRun()
            elif game["state"] == "END" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                resetRun()
            elif game["state"] == "PLAY" and event.key in (pygame.K_p, pygame.K_PAUSE):
                game["state"] = "PAUSE"
            elif game["state"] == "PLAY" and event.key == pygame.K_f:
                game["difficultyBoosts"] += 1
                game["trafficIntensity"] = 1.0 + game["difficultyBoosts"] * 0.25
                showMessage(f"ТРАФИК УСИЛЕН  ×{game['trafficIntensity']:.2f}", 1.3)
            elif game["state"] == "PAUSE" and event.key in (pygame.K_p, pygame.K_PAUSE, pygame.K_RETURN):
                game["state"] = "PLAY"
            elif game["state"] == "END" and event.key == pygame.K_BACKSPACE:
                game["state"] = "MENU"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            clicked = True
            logicalX = event.pos[0] / max(1, displayWidth) * BASE_WIDTH
            logicalY = event.pos[1] / max(1, displayHeight) * BASE_HEIGHT
            if game["state"] in ("MENU", "END"):
                resetRun()

    # Controller buttons: 1 starts, 2 pauses, 3 permanently boosts traffic,
    # and 4 cycles steering sensitivity. Boosts stack until the run is reset.
    currentButtons = [readButton(joystick, index) for index in range(4)]
    pressedButtons = [bool(currentButtons[index] and not previousButtons[index]) for index in range(4)]
    if game["state"] == "MENU" and (pressedButtons[0] or pressedButtons[1] or pressedButtons[3]):
        resetRun()
    elif game["state"] == "END" and (pressedButtons[0] or pressedButtons[1]):
        resetRun()
    elif game["state"] == "PLAY":
        if pressedButtons[1]:
            game["state"] = "PAUSE"
        if pressedButtons[2]:
            game["difficultyBoosts"] += 1
            game["trafficIntensity"] = 1.0 + game["difficultyBoosts"] * 0.25
            showMessage(f"ТРАФИК УСИЛЕН  ×{game['trafficIntensity']:.2f}", 1.3)
        if pressedButtons[3]:
            steeringPresetIndex = (steeringPresetIndex + 1) % len(STEERING_NAMES)
            showMessage(f"РУЛЕНИЕ: {STEERING_NAMES[steeringPresetIndex].upper()}", 1.0)
    elif game["state"] == "PAUSE" and (pressedButtons[1] or pressedButtons[0]):
        game["state"] = "PLAY"
    previousButtons = currentButtons

    if game["state"] == "PLAY":
        updatePlayState(frameDelta, steerInput)
        if time.monotonic() - lastStatsSaveTime >= STATS_AUTOSAVE_INTERVAL:
            saveStatsFile("interrupted", includeCurrentRun=True)
            lastStatsSaveTime = time.monotonic()
        drawGameplay(logicalSurface)
    elif game["state"] == "PAUSE":
        drawGameplay(logicalSurface)
        drawPauseOverlay(logicalSurface)
    elif game["state"] == "END":
        drawEndScreen(logicalSurface, animationTime)
    else:
        drawMenu(logicalSurface, animationTime)

    # Keep the 16:9 logical canvas undistorted on any screen size.
    display.fill((3, 7, 16))
    scaleFactor = min(displayWidth / BASE_WIDTH, displayHeight / BASE_HEIGHT)
    scaledWidth = max(1, int(BASE_WIDTH * scaleFactor))
    scaledHeight = max(1, int(BASE_HEIGHT * scaleFactor))
    scaledSurface = pygame.transform.smoothscale(logicalSurface, (scaledWidth, scaledHeight))
    drawX = (displayWidth - scaledWidth) // 2
    drawY = (displayHeight - scaledHeight) // 2
    display.blit(scaledSurface, (drawX, drawY))
    pygame.display.flip()

# Store an unfinished run as interrupted and preserve any results already collected.
if game["state"] in ("PLAY", "PAUSE") and not game["runCaptured"]:
    captureRun(False)
if runHistory:
    finalStatus = "completed" if any(run["completed"] for run in runHistory) else "interrupted"
    saveStatsFile(finalStatus)

for device in availableJoysticks:
    try:
        device.quit()
    except pygame.error:
        pass
pygame.quit()
sys.exit()
