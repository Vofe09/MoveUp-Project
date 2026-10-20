import os
import sys
import time
import math
import random
import atexit
import uuid
from pathlib import Path
from typing import Optional

import pygame

_scriptDirectory = Path(__file__).resolve().parent
for _candidateDirectory in (_scriptDirectory, *_scriptDirectory.parents):
    if (_candidateDirectory / "moveup_stats.py").exists():
        sys.path.insert(0, str(_candidateDirectory))
        break
from moveup_stats import MoveUpStatistics, metric

moveUpStatistics = MoveUpStatistics(
    "MoveUp Puzzle", "puzzle", sys.executable if getattr(sys, "frozen", False) else __file__
)
puzzleRoundHistory: list[dict] = []
puzzleSessionAborted = False
puzzleSessionFinalized = False
puzzleLastStatsSaveTime = 0.0


# ================================================================
# MoveUp Puzzle — настройки, совместимые по стилю с shooter.py
# ================================================================
BASE_WIDTH = 900
BASE_HEIGHT = 650
FPS = 60

JOY1_SWAP_AXES = False
JOY2_SWAP_AXES = False
JOY1_SIGN_X = 1
JOY1_SIGN_Y = 1
JOY2_SIGN_X = 1
JOY2_SIGN_Y = 1

SENSITIVITY_PRESETS = {
    "Низкая": 0.007,
    "Средняя": 0.015,
    "Высокая": 0.025,
    "Максимальная": 0.040,
}
SENSITIVITY_NAMES = list(SENSITIVITY_PRESETS.keys())

COLOR_BG_DARK = (15, 12, 30)
COLOR_BG_LIGHT = (25, 30, 60)
COLOR_GLASS = (25, 25, 50, 180)
COLOR_TEXT = (255, 255, 255)
COLOR_TEXT_DARK = (10, 15, 45)
COLOR_TEXT_MUTED = (160, 170, 210)
COLOR_NEON_BLUE = (0, 240, 255)
COLOR_NEON_PINK = (255, 0, 128)
COLOR_NEON_GREEN = (0, 255, 150)
COLOR_GOLD = (255, 205, 70)

pygame.init()
pygame.joystick.init()

DisplayScreen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
DisplayWidth, DisplayHeight = DisplayScreen.get_size()
Screen = pygame.Surface((BASE_WIDTH, BASE_HEIGHT), pygame.SRCALPHA)
pygame.display.set_caption("MoveUp Puzzle — Rehabilitation Edition")
Clock = pygame.time.Clock()

FontSmall = pygame.font.SysFont("Segoe UI", 17)
Font = pygame.font.SysFont("Segoe UI", 24, bold=True)
FontMedium = pygame.font.SysFont("Segoe UI", 30, bold=True)
FontBig = pygame.font.SysFont("Segoe UI", 42, bold=True)

# Ресурсы: поместите изображения в assets/puzzles рядом с этим файлом.
APP_DIR = Path(__file__).resolve().parent
PUZZLE_DIR = APP_DIR / "assets" / "puzzles"


def MakePlaceholderImage(title: str, seed: int, size=(640, 420)) -> pygame.Surface:
    """Создаёт уникальную встроенную картину, если пользовательские картинки не найдены."""
    rng = random.Random(seed)
    image = pygame.Surface(size)
    top = (25 + seed * 17 % 60, 55 + seed * 13 % 70, 100 + seed * 19 % 90)
    bottom = (20, 100 + seed * 11 % 90, 90 + seed * 7 % 100)
    for y in range(size[1]):
        ratio = y / max(1, size[1] - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        pygame.draw.line(image, color, (0, y), (size[0], y))
    # Солнце и декоративные холмы
    sun_x = int(size[0] * (0.22 + 0.12 * (seed % 4)))
    sun_y = int(size[1] * 0.24)
    pygame.draw.circle(image, (255, 220, 130), (sun_x, sun_y), 42)
    for layer in range(3):
        points = [(-30, size[1])]
        for x in range(-30, size[0] + 50, 45):
            y = int(size[1] * (0.56 + layer * 0.12) + math.sin((x + seed * 31 + layer * 60) / 75) * (25 + layer * 9))
            points.append((x, y))
        points.extend([(size[0] + 30, size[1]), (-30, size[1])])
        layer_color = [(45, 100, 125), (35, 85, 95), (20, 65, 75)][layer]
        pygame.draw.polygon(image, layer_color, points)
    # Небольшие деревья
    for _ in range(13):
        x = rng.randint(10, size[0] - 10)
        y = rng.randint(int(size[1] * 0.62), size[1] - 12)
        pygame.draw.rect(image, (60, 45, 40), (x - 3, y, 6, 18))
        pygame.draw.circle(image, (30, 125 + rng.randint(0, 55), 90), (x, y), rng.randint(9, 17))
    label = pygame.font.SysFont("Segoe UI", 26, bold=True).render(title, True, (255, 255, 255))
    shadow = pygame.font.SysFont("Segoe UI", 26, bold=True).render(title, True, (10, 15, 35))
    image.blit(shadow, (18, 18))
    image.blit(label, (16, 16))
    return image


def LoadPictures():
    pictures = []
    if PUZZLE_DIR.exists():
        for image_path in sorted(PUZZLE_DIR.iterdir()):
            if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
                continue
            try:
                image = pygame.image.load(str(image_path)).convert()
                pictures.append({"name": image_path.stem.replace("_", " ").title(), "image": image})
            except (pygame.error, OSError) as error:
                print(f"Не удалось загрузить картину {image_path}: {error}")
    if not pictures:
        pictures = [
            {"name": "Горное озеро", "image": MakePlaceholderImage("Горное озеро", 1)},
            {"name": "Лесная долина", "image": MakePlaceholderImage("Лесная долина", 2)},
            {"name": "Закат", "image": MakePlaceholderImage("Закат", 3)},
            {"name": "Зелёные холмы", "image": MakePlaceholderImage("Зелёные холмы", 4)},
        ]
    return pictures


Pictures = LoadPictures()

# Состояние игры
Game = {
    "State": "PORT_SELECT",
    "PictureIndex": 0,
    "GridSize": 3,
    "Pieces": [],
    "StartTime": 0.0,
    "EndTime": 0.0,
    "Moves": 0,
    "Mistakes": 0,
    "Placed": 0,
    "CursorX": BASE_WIDTH // 2,
    "CursorY": BASE_HEIGHT // 2,
    "TargetX": BASE_WIDTH // 2,
    "TargetY": BASE_HEIGHT // 2,
    "SelectedPiece": None,
    "DragOffsetX": 0,
    "DragOffsetY": 0,
    "SensitivityIndex": 1,
    "ShowPreview": True,
    "LastPlacementCorrect": False,
    "PlacementFeedbackUntil": 0.0,
    "RoundCaptured": False,
    "CursorPathLengthPx": 0.0,
    "SpeedSum": 0.0,
    "SpeedSquaredSum": 0.0,
    "SpeedSampleCount": 0,
    "MaxCursorSpeedPxPerSecond": 0.0,
    "ActiveMovementSeconds": 0.0,
    "ActiveMovementDistancePx": 0.0,
    "LastCursorSampleAt": 0.0,
    "LastCursorSampleX": BASE_WIDTH // 2,
    "LastCursorSampleY": BASE_HEIGHT // 2,
}

# HID-контроллер MoveUp: работает как обычный игровой джойстик через Pygame.
# Serial здесь намеренно не используется: HID-устройства не читаются через pyserial.
Joysticks = []
Joy: Optional[pygame.joystick.Joystick] = None
SelectedPortIndex = 0
PortStatusMessage = ""

def RefreshJoysticks():
    """Обновляет список HID-джойстиков, доступных через SDL/Pygame."""
    global Joysticks, SelectedPortIndex
    pygame.joystick.quit()
    pygame.joystick.init()
    Joysticks = []
    for index in range(pygame.joystick.get_count()):
        try:
            joystick = pygame.joystick.Joystick(index)
            joystick.init()
            Joysticks.append({
                "index": index,
                "name": joystick.get_name(),
                "axes": joystick.get_numaxes(),
                "buttons": joystick.get_numbuttons(),
            })
        except pygame.error as error:
            print(f"Не удалось инициализировать HID-устройство {index}: {error}")
    SelectedPortIndex = min(SelectedPortIndex, max(0, len(Joysticks) - 1))

def SelectJoystick(index):
    """Выбирает HID-джойстик из списка."""
    global Joy, PortStatusMessage
    if not 0 <= index < len(Joysticks):
        Joy = None
        PortStatusMessage = "Джойстик не выбран. Доступны мышь и клавиатура."
        return False
    try:
        if Joy is not None:
            Joy.quit()
        Joy = pygame.joystick.Joystick(Joysticks[index]["index"])
        Joy.init()
        PortStatusMessage = f"Подключено: {Joy.get_name()}"
        print(
            f"HID-контроллер: {Joy.get_name()} | "
            f"оси: {Joy.get_numaxes()} | кнопки: {Joy.get_numbuttons()}"
        )
        return True
    except pygame.error as error:
        Joy = None
        PortStatusMessage = f"Ошибка HID: {error}"
        print(PortStatusMessage)
        return False

RefreshJoysticks()
if Joysticks:
    SelectJoystick(0)
else:
    print("HID-геймпад не найден — доступно управление мышью и клавиатурой.")

PrevBtn1 = 0
PrevBtn2 = 0
PrevBtn3 = 0


def GetCurrentSensitivity():
    return SENSITIVITY_PRESETS[SENSITIVITY_NAMES[Game["SensitivityIndex"]]]


def ToLogicalPos(position):
    return (position[0] * BASE_WIDTH / DisplayWidth, position[1] * BASE_HEIGHT / DisplayHeight)


def DrawBackground(surface):
    for y in range(BASE_HEIGHT):
        ratio = y / BASE_HEIGHT
        color = tuple(int(COLOR_BG_DARK[i] * (1 - ratio) + COLOR_BG_LIGHT[i] * ratio) for i in range(3))
        pygame.draw.line(surface, color, (0, y), (BASE_WIDTH, y))
    # Ненавязчивые декоративные точки
    for x in range(20, BASE_WIDTH, 44):
        for y in range(110, BASE_HEIGHT, 44):
            pygame.draw.circle(surface, (255, 255, 255, 12), (x, y), 1)


def DrawGlowRect(surface, rect, color, thickness=2, glow_radius=10):
    for radius in range(glow_radius, 0, -2):
        alpha = int((1.0 - radius / glow_radius) * 65)
        glow_surface = pygame.Surface((rect.width + radius * 2, rect.height + radius * 2), pygame.SRCALPHA)
        pygame.draw.rect(glow_surface, (*color[:3], alpha), (0, 0, rect.width + radius * 2, rect.height + radius * 2), thickness + radius, border_radius=12)
        surface.blit(glow_surface, (rect.x - radius, rect.y - radius))
    pygame.draw.rect(surface, color, rect, thickness, border_radius=12)


def DrawPanel(surface, rect, border_color=COLOR_NEON_BLUE):
    pygame.draw.rect(surface, COLOR_GLASS, rect, border_radius=16)
    pygame.draw.rect(surface, (255, 255, 255, 28), rect, 1, border_radius=16)
    DrawGlowRect(surface, rect, border_color, 1, 7)


def DrawCentered(surface, text, font, color, center):
    rendered = font.render(text, True, color)
    surface.blit(rendered, (int(center[0] - rendered.get_width() / 2), int(center[1] - rendered.get_height() / 2)))


def DrawButton(surface, rect, label, selected=False, color=COLOR_NEON_BLUE, font=Font):
    fill = (color[0], color[1], color[2], 35) if selected else (255, 255, 255, 12)
    pygame.draw.rect(surface, fill, rect, border_radius=10)
    pygame.draw.rect(surface, color if selected else (255, 255, 255, 45), rect, 2 if selected else 1, border_radius=10)
    DrawCentered(surface, label, font, COLOR_TEXT_DARK if selected else COLOR_TEXT, rect.center)


def BuildPuzzle():
    """Режет картину на прямоугольные фрагменты и перемешивает их в нижней/правой зоне."""
    if Game.get("State") == "PLAY" and Game.get("StartTime", 0.0) > 0 and not Game.get("RoundCaptured", False):
        CapturePuzzleRound("restarted")
    picture = Pictures[Game["PictureIndex"]]["image"]
    columns = Game["GridSize"]
    rows = columns
    board = pygame.Rect(45, 130, 510, 430)
    tile_width = board.width / columns
    tile_height = board.height / rows
    fitted_picture = pygame.transform.smoothscale(picture, (board.width, board.height))
    pieces = []
    piece_index = 0
    tray = pygame.Rect(585, 145, 270, 400)
    tray_columns = 2 if columns <= 4 else 3
    tray_rows = math.ceil(columns * columns / tray_columns)
    slot_width = min(82, (tray.width - 18) / tray_columns)
    slot_height = min(66, (tray.height - 18) / max(1, tray_rows))
    slots = []
    for slot_index in range(columns * columns):
        slot_column = slot_index % tray_columns
        slot_row = slot_index // tray_columns
        slot_x = tray.x + 9 + slot_column * slot_width
        slot_y = tray.y + 9 + slot_row * slot_height
        slots.append((int(slot_x), int(slot_y)))
    random.shuffle(slots)

    for row in range(rows):
        for column in range(columns):
            source_rect = pygame.Rect(round(column * tile_width), round(row * tile_height), round((column + 1) * tile_width) - round(column * tile_width), round((row + 1) * tile_height) - round(row * tile_height))
            piece_image = fitted_picture.subsurface(source_rect).copy()
            piece_image = pygame.transform.smoothscale(piece_image, (max(18, round(tile_width)), max(18, round(tile_height))))
            piece_image = piece_image.convert()
            slot_x, slot_y = slots[piece_index]
            piece_width = min(round(tile_width), max(42, int(slot_width - 8)))
            piece_height = min(round(tile_height), max(34, int(slot_height - 8)))
            piece_image = pygame.transform.smoothscale(piece_image, (piece_width, piece_height))
            pieces.append({
                "id": piece_index,
                "image": piece_image,
                "correct_row": row,
                "correct_col": column,
                "x": slot_x,
                "y": slot_y,
                "width": piece_width,
                "height": piece_height,
                "placed": False,
                "original_x": slot_x,
                "original_y": slot_y,
            })
            piece_index += 1
    Game["Pieces"] = pieces
    Game["BoardRect"] = board
    Game["TileWidth"] = tile_width
    Game["TileHeight"] = tile_height
    Game["Placed"] = 0
    Game["Moves"] = 0
    Game["Mistakes"] = 0
    Game["SelectedPiece"] = None
    Game["StartTime"] = time.time()
    Game["EndTime"] = 0.0
    Game["CursorX"] = BASE_WIDTH // 2
    Game["CursorY"] = BASE_HEIGHT // 2
    Game["TargetX"] = BASE_WIDTH // 2
    Game["TargetY"] = BASE_HEIGHT // 2
    Game["State"] = "PLAY"
    Game["RoundId"] = str(uuid.uuid4())
    Game["RoundCaptured"] = False
    Game["CursorPathLengthPx"] = 0.0
    Game["SpeedSum"] = 0.0
    Game["SpeedSquaredSum"] = 0.0
    Game["SpeedSampleCount"] = 0
    Game["MaxCursorSpeedPxPerSecond"] = 0.0
    Game["ActiveMovementSeconds"] = 0.0
    Game["ActiveMovementDistancePx"] = 0.0
    Game["LastCursorSampleAt"] = time.monotonic()
    Game["LastCursorSampleX"] = Game["CursorX"]
    Game["LastCursorSampleY"] = Game["CursorY"]


def PieceRect(piece):
    return pygame.Rect(int(piece["x"]), int(piece["y"]), piece["width"], piece["height"])


def SelectPieceAt(position):
    # Проверяем верхние фрагменты первыми.
    for piece in reversed(Game["Pieces"]):
        if not piece["placed"] and PieceRect(piece).collidepoint(position):
            return piece
    return None


def StartDrag(position):
    piece = SelectPieceAt(position)
    if piece is None:
        return
    Game["SelectedPiece"] = piece["id"]
    Game["DragOffsetX"] = position[0] - piece["x"]
    Game["DragOffsetY"] = position[1] - piece["y"]
    # Переносим захваченный фрагмент наверх списка для отрисовки.
    Game["Pieces"].remove(piece)
    Game["Pieces"].append(piece)



def CurrentPuzzleSnapshot(outcome="in_progress"):
    startTime = Game.get("StartTime", 0.0)
    endTime = Game.get("EndTime", 0.0)
    now = time.time()
    duration = max(0.0, (endTime if endTime > 0 else now) - startTime) if startTime > 0 else 0.0
    totalPieces = max(1, len(Game.get("Pieces", [])))
    moves = int(Game.get("Moves", 0))
    placed = int(Game.get("Placed", 0))
    mistakes = int(Game.get("Mistakes", 0))
    return {
        "round_id": Game.get("RoundId", ""),
        "picture": Pictures[Game["PictureIndex"]]["name"] if Pictures else "",
        "grid_size": int(Game.get("GridSize", 3)),
        "outcome": outcome,
        "duration_seconds": round(duration, 3),
        "total_pieces": totalPieces,
        "pieces_placed": placed,
        "placement_attempts": moves,
        "placement_errors": mistakes,
        "successfulPlacementRatePct": round(min(100.0, placed / moves * 100.0) if moves else 0.0, 3),
        "score": max(0, placed * 100 - mistakes * 15 - max(0, moves - placed) * 3),
        "cursor_path_length_px": round(float(Game.get("CursorPathLengthPx", 0.0)), 3),
        "average_cursor_speed_px_per_second": round(float(Game.get("CursorPathLengthPx", 0.0)) / duration if duration > 0 else 0.0, 3),
        "average_active_movement_speed_px_per_second": round(float(Game.get("ActiveMovementDistancePx", 0.0)) / float(Game.get("ActiveMovementSeconds", 0.0)) if Game.get("ActiveMovementSeconds", 0.0) > 0 else 0.0, 3),
        "active_movement_distance_px": round(float(Game.get("ActiveMovementDistancePx", 0.0)), 3),
        "active_movement_seconds": round(float(Game.get("ActiveMovementSeconds", 0.0)), 3),
        "max_cursor_speed_px_per_second": round(float(Game.get("MaxCursorSpeedPxPerSecond", 0.0)), 3),
        "speed_sum": float(Game.get("SpeedSum", 0.0)),
        "speed_squared_sum": float(Game.get("SpeedSquaredSum", 0.0)),
        "speed_sample_count": int(Game.get("SpeedSampleCount", 0)),
    }


def CapturePuzzleRound(outcome):
    global puzzleSessionAborted
    if Game.get("RoundCaptured") or Game.get("StartTime", 0.0) <= 0:
        return
    snapshot = CurrentPuzzleSnapshot(outcome)
    puzzleRoundHistory.append(snapshot)
    Game["RoundCaptured"] = True
    if outcome in ("interrupted", "restarted"):
        puzzleSessionAborted = True if outcome == "interrupted" else puzzleSessionAborted
    savePuzzleStatistics("interrupted", includeActiveRound=False)


def savePuzzleStatistics(sessionStatus, includeActiveRound=True):
    rounds = list(puzzleRoundHistory)
    activeRound = None
    isActive = (
        includeActiveRound and Game.get("State") == "PLAY" and
        Game.get("StartTime", 0.0) > 0 and not Game.get("RoundCaptured", False)
    )
    if isActive:
        activeRound = CurrentPuzzleSnapshot("in_progress")
    roundsForMetrics = rounds + ([activeRound] if activeRound else [])
    attempts = len(roundsForMetrics)
    completedRounds = [item for item in roundsForMetrics if item.get("outcome") == "completed"]
    interruptedRounds = [item for item in roundsForMetrics if item.get("outcome") == "interrupted"]
    restartedRounds = [item for item in roundsForMetrics if item.get("outcome") == "restarted"]
    totalDuration = sum(float(item.get("duration_seconds", 0.0)) for item in roundsForMetrics)
    totalPieces = sum(int(item.get("total_pieces", 0)) for item in roundsForMetrics)
    piecesPlaced = sum(int(item.get("pieces_placed", 0)) for item in roundsForMetrics)
    placementAttempts = sum(int(item.get("placement_attempts", 0)) for item in roundsForMetrics)
    placementErrors = sum(int(item.get("placement_errors", 0)) for item in roundsForMetrics)
    totalPathLength = sum(float(item.get("cursor_path_length_px", 0.0)) for item in roundsForMetrics)
    totalSpeedSum = sum(float(item.get("speed_sum", 0.0)) for item in roundsForMetrics)
    totalSpeedSquaredSum = sum(float(item.get("speed_squared_sum", 0.0)) for item in roundsForMetrics)
    totalSpeedSamples = sum(int(item.get("speed_sample_count", 0)) for item in roundsForMetrics)
    movementDistance = sum(float(item.get("active_movement_distance_px", 0.0)) for item in roundsForMetrics)
    movementSeconds = sum(float(item.get("active_movement_seconds", 0.0)) for item in roundsForMetrics)
    maxSpeed = max((float(item.get("max_cursor_speed_px_per_second", 0.0)) for item in roundsForMetrics), default=0.0)
    meanSampleSpeed = totalSpeedSum / totalSpeedSamples if totalSpeedSamples else 0.0
    speedStdDev = math.sqrt(max(0.0, totalSpeedSquaredSum / totalSpeedSamples - meanSampleSpeed ** 2)) if totalSpeedSamples else 0.0
    score = sum(int(item.get("score", 0)) for item in roundsForMetrics)
    metrics = {
        "puzzleAttempts": metric("Попытки сборки пазла", attempts, "шт."),
        "puzzlesCompleted": metric("Успешно собранные пазлы", len(completedRounds), "шт."),
        "puzzleAttemptsInterrupted": metric("Прерванные попытки", len(interruptedRounds), "шт."),
        "puzzleAttemptsRestarted": metric("Перезапущенные попытки", len(restartedRounds), "шт."),
        "piecesPlaced": metric("Размещённые детали", piecesPlaced, "шт."),
        "placementAttempts": metric("Попытки размещения деталей", placementAttempts, "шт."),
        "placementErrors": metric("Ошибочные размещения", placementErrors, "шт."),
        "successfulPlacementRatePct": metric("Успешные размещения от всех попыток", piecesPlaced / placementAttempts * 100.0 if placementAttempts else 0.0, "%"),
        "pieceCompletionRatePct": metric("Доля размещённых деталей", piecesPlaced / totalPieces * 100.0 if totalPieces else 0.0, "%"),
        "totalScore": metric("Суммарный игровой счёт", score, "очков"),
        "activeGameplaySeconds": metric("Суммарное время сборки", totalDuration, "сек."),
        "cursorPathLengthPx": metric("Суммарная длина траектории курсора", totalPathLength, "px"),
        "averageCursorSpeedPxPerSecond": metric("Средняя скорость курсора за время сборки", totalPathLength / totalDuration if totalDuration else 0.0, "px/сек"),
        "averageActiveMovementSpeedPxPerSecond": metric("Средняя скорость активного движения", movementDistance / movementSeconds if movementSeconds else 0.0, "px/сек"),
        "maxCursorSpeedPxPerSecond": metric("Максимальная скорость курсора", maxSpeed, "px/сек"),
        "cursorSpeedStandardDeviationPxPerSecond": metric("Стандартное отклонение скорости курсора", speedStdDev, "px/сек"),
    }
    if attempts:
        metrics["puzzleCompletionRatePct"] = metric("Доля завершённых пазлов", len(completedRounds) / attempts * 100.0, "%")
    settings = {
        "logical_resolution_px": {"width": BASE_WIDTH, "height": BASE_HEIGHT},
        "cursor_smoothing_factor": 0.22,
        "movement_active_threshold_px_per_second": 5.0,
        "grid_sizes_used": sorted({int(item.get("grid_size", 0)) for item in roundsForMetrics if item.get("grid_size")}),
        "sensitivity_presets_used": [SENSITIVITY_NAMES[Game.get("SensitivityIndex", 1)]],
    }
    rawMetricFields = {"speed_sum", "speed_squared_sum", "speed_sample_count"}
    details = {"rounds": [{key: value for key, value in item.items() if key not in rawMetricFields} for item in puzzleRoundHistory]}
    if activeRound:
        details["active_round"] = {key: value for key, value in activeRound.items() if key not in rawMetricFields}
    moveUpStatistics.save(sessionStatus, metrics, settings, details)


def finalizePuzzleStatistics():
    global puzzleSessionFinalized
    if puzzleSessionFinalized:
        return
    try:
        if Game.get("State") == "PLAY" and Game.get("StartTime", 0.0) > 0 and not Game.get("RoundCaptured", False):
            CapturePuzzleRound("interrupted")
        status = "completed" if any(item.get("outcome") == "completed" for item in puzzleRoundHistory) and not puzzleSessionAborted else "interrupted"
        savePuzzleStatistics(status, includeActiveRound=False)
    except Exception as error:
        print(f"Не удалось сохранить статистику Puzzle при выходе: {error}")
    puzzleSessionFinalized = True


atexit.register(finalizePuzzleStatistics)


def UpdatePuzzleCursorMetrics():
    now = time.monotonic()
    if Game.get("StartTime", 0.0) <= 0 or Game.get("RoundCaptured", False):
        return
    lastAt = float(Game.get("LastCursorSampleAt", 0.0))
    currentPosition = (float(Game["CursorX"]), float(Game["CursorY"]))
    if lastAt > 0.0:
        deltaSeconds = now - lastAt
        if deltaSeconds > 0.0:
            distance = math.hypot(currentPosition[0] - Game["LastCursorSampleX"], currentPosition[1] - Game["LastCursorSampleY"])
            speed = distance / deltaSeconds
            Game["CursorPathLengthPx"] += distance
            Game["SpeedSum"] += speed
            Game["SpeedSquaredSum"] += speed * speed
            Game["SpeedSampleCount"] += 1
            Game["MaxCursorSpeedPxPerSecond"] = max(Game["MaxCursorSpeedPxPerSecond"], speed)
            if speed >= 5.0:
                Game["ActiveMovementSeconds"] += deltaSeconds
                Game["ActiveMovementDistancePx"] += distance
    Game["LastCursorSampleX"], Game["LastCursorSampleY"] = currentPosition
    Game["LastCursorSampleAt"] = now


def ReleasePiece():
    selected_id = Game["SelectedPiece"]
    if selected_id is None:
        return
    piece = next((item for item in Game["Pieces"] if item["id"] == selected_id), None)
    Game["SelectedPiece"] = None
    if piece is None:
        return
    Game["Moves"] += 1
    board = Game["BoardRect"]
    target_x = board.x + piece["correct_col"] * Game["TileWidth"]
    target_y = board.y + piece["correct_row"] * Game["TileHeight"]
    piece_center = (piece["x"] + piece["width"] / 2, piece["y"] + piece["height"] / 2)
    target_center = (target_x + Game["TileWidth"] / 2, target_y + Game["TileHeight"] / 2)
    distance = math.dist(piece_center, target_center)
    snap_distance = max(35, min(Game["TileWidth"], Game["TileHeight"]) * 0.48)
    if distance <= snap_distance:
        piece["x"] = round(target_x)
        piece["y"] = round(target_y)
        piece["width"] = round(Game["TileWidth"])
        piece["height"] = round(Game["TileHeight"])
        # Возвращаем правильный масштаб изображения на поле.
        picture = Pictures[Game["PictureIndex"]]["image"]
        fitted_picture = pygame.transform.smoothscale(picture, (board.width, board.height))
        source_rect = pygame.Rect(
            round(piece["correct_col"] * Game["TileWidth"]),
            round(piece["correct_row"] * Game["TileHeight"]),
            round((piece["correct_col"] + 1) * Game["TileWidth"]) - round(piece["correct_col"] * Game["TileWidth"]),
            round((piece["correct_row"] + 1) * Game["TileHeight"]) - round(piece["correct_row"] * Game["TileHeight"]),
        )
        piece["image"] = fitted_picture.subsurface(source_rect).copy()
        piece["placed"] = True
        Game["Placed"] += 1
        Game["LastPlacementCorrect"] = True
        Game["PlacementFeedbackUntil"] = time.time() + 0.55
        if Game["Placed"] == len(Game["Pieces"]):
            Game["EndTime"] = time.time()
            Game["State"] = "END"
            CapturePuzzleRound("completed")
    else:
        Game["Mistakes"] += 1
        Game["LastPlacementCorrect"] = False
        Game["PlacementFeedbackUntil"] = time.time() + 0.55


def HandleControllerInput():
    """Читает HID-геймпад через Pygame. Никакого serial-чтения."""
    global PrevBtn1, PrevBtn2, PrevBtn3
    button_left = button_right = button_center = 0
    move_x = move_y = 0.0

    if Joy is not None:
        try:
            pygame.event.pump()
            button_left = Joy.get_button(1) if Joy.get_numbuttons() > 1 else 0
            button_right = Joy.get_button(0) if Joy.get_numbuttons() > 0 else 0
            button_center = Joy.get_button(3) if Joy.get_numbuttons() > 3 else 0  # GP8 = HID Button 4

            # Точная раскладка кнопок: GP8 = HID Button 4 (индекс 3).\n    # Точная раскладка из shooter.py:
            # левый стик X/Y = axes 2/3, правый стик X/Y = axes 0/1.
            axis_map = (
                (2, 3, JOY1_SWAP_AXES, JOY1_SIGN_X, JOY1_SIGN_Y),
                (0, 1, JOY2_SWAP_AXES, JOY2_SIGN_X, JOY2_SIGN_Y),
            )
            for axis_x, axis_y, swap_axes, sign_x, sign_y in axis_map:
                if Joy.get_numaxes() <= max(axis_x, axis_y):
                    continue
                raw_x = Joy.get_axis(axis_x)
                raw_y = Joy.get_axis(axis_y)
                if swap_axes:
                    raw_x, raw_y = raw_y, raw_x
                if abs(raw_x) > 0.05:
                    move_x += raw_x * sign_x
                if abs(raw_y) > 0.05:
                    move_y += raw_y * sign_y
        except (pygame.error, IndexError) as error:
            print("Ошибка чтения HID-контроллера:", error)

    if button_center and not PrevBtn3:
        Game["SensitivityIndex"] = (Game["SensitivityIndex"] + 1) % len(SENSITIVITY_NAMES)

    if Game["State"] == "PLAY":
        sensitivity = GetCurrentSensitivity()
        if abs(move_x) > 0.05:
            Game["TargetX"] += move_x * 512 * sensitivity
        if abs(move_y) > 0.05:
            Game["TargetY"] += move_y * 512 * sensitivity
        Game["TargetX"] = max(8, min(BASE_WIDTH - 8, Game["TargetX"]))
        Game["TargetY"] = max(100, min(BASE_HEIGHT - 8, Game["TargetY"]))

    if (button_left and not PrevBtn1) or (button_right and not PrevBtn2):
        if Game["State"] == "PLAY":
            if Game["SelectedPiece"] is None:
                StartDrag((int(Game["CursorX"]), int(Game["CursorY"])))
            else:
                ReleasePiece()

    PrevBtn1, PrevBtn2, PrevBtn3 = button_left, button_right, button_center


def DrawHeader(title, subtitle=""):
    pygame.draw.rect(Screen, COLOR_GLASS, (0, 0, BASE_WIDTH, 100))
    pygame.draw.line(Screen, COLOR_NEON_BLUE, (0, 99), (BASE_WIDTH, 99), 2)
    Screen.blit(FontBig.render(title, True, COLOR_TEXT), (30, 17))
    if subtitle:
        Screen.blit(FontSmall.render(subtitle, True, COLOR_TEXT_MUTED), (33, 68))


def DrawPortSelect():
    DrawHeader("ВЫБОР HID-КОНТРОЛЛЕРА", "MoveUp подключается как игровой джойстик — serial-порт не нужен")
    panel = pygame.Rect(145, 130, 610, 365)
    DrawPanel(Screen, panel)
    DrawCentered(Screen, "НАЙДЕННЫЕ HID-УСТРОЙСТВА", Font, COLOR_TEXT, (BASE_WIDTH // 2, 157))

    if not Joysticks:
        DrawCentered(Screen, "HID-джойстики не найдены", Font, COLOR_NEON_PINK, (BASE_WIDTH // 2, 230))
        DrawCentered(Screen, "Проверьте подключение контроллера и нажмите F5", FontSmall, COLOR_TEXT_MUTED, (BASE_WIDTH // 2, 265))
    else:
        for index, joystick_info in enumerate(Joysticks[:5]):
            rect = pygame.Rect(185, 185 + index * 48, 530, 40)
            selected = index == SelectedPortIndex
            label = f"{joystick_info['name'][:34]}  ·  осей: {joystick_info['axes']}  кнопок: {joystick_info['buttons']}"
            DrawButton(Screen, rect, label, selected)

    refresh_rect = pygame.Rect(185, 435, 245, 42)
    continue_rect = pygame.Rect(450, 435, 265, 42)
    DrawButton(Screen, refresh_rect, "ОБНОВИТЬ  [F5]", False, COLOR_NEON_BLUE)
    DrawButton(Screen, continue_rect, "ПРОДОЛЖИТЬ БЕЗ HID  →", True, COLOR_NEON_GREEN)
    if PortStatusMessage:
        status_color = COLOR_NEON_GREEN if "Подключено" in PortStatusMessage else COLOR_TEXT_MUTED
        DrawCentered(Screen, PortStatusMessage[:88], FontSmall, status_color, (BASE_WIDTH // 2, 525))
    DrawCentered(Screen, "↑ / ↓ — выбор   ENTER — подключиться   ESC — выход", FontSmall, COLOR_TEXT_MUTED, (BASE_WIDTH // 2, 575))


def ConfirmSelectedPort():
    """Выбирает HID-джойстик и переводит игру к выбору картины."""
    global Joy, PortStatusMessage
    if Joysticks and 0 <= SelectedPortIndex < len(Joysticks):
        SelectJoystick(SelectedPortIndex)
    else:
        Joy = None
        PortStatusMessage = "Продолжение без HID: управление мышью/клавиатурой"
    Game["State"] = "IMAGE_SELECT"


def DrawImageSelect():
    DrawHeader("ВЫБОР КАРТИНЫ", "Выберите изображение для сборки пазла")
    card_width, card_height = 190, 175
    gap = 20
    total_width = len(Pictures) * card_width + (len(Pictures) - 1) * gap
    start_x = (BASE_WIDTH - total_width) // 2
    start_y = 170
    for index, picture in enumerate(Pictures):
        x = start_x + index * (card_width + gap)
        rect = pygame.Rect(x, start_y, card_width, card_height)
        selected = index == Game["PictureIndex"]
        pygame.draw.rect(Screen, COLOR_GLASS, rect, border_radius=14)
        if selected:
            DrawGlowRect(Screen, rect, COLOR_NEON_BLUE, 2, 12)
        else:
            pygame.draw.rect(Screen, (255, 255, 255, 35), rect, 1, border_radius=14)
        thumbnail = pygame.transform.smoothscale(picture["image"], (card_width - 16, 118))
        Screen.blit(thumbnail, (x + 8, start_y + 8))
        DrawCentered(Screen, picture["name"][:22], FontSmall, COLOR_TEXT, (rect.centerx, start_y + 146))
    DrawCentered(Screen, "← / → или стик: выбор     ENTER / кнопка: подтвердить", FontSmall, COLOR_NEON_BLUE, (BASE_WIDTH // 2, 410))
    DrawCentered(Screen, "Можно добавить свои изображения в assets/puzzles", FontSmall, COLOR_TEXT_MUTED, (BASE_WIDTH // 2, 445))
    DrawButton(Screen, pygame.Rect(310, 510, 280, 50), "ПРОДОЛЖИТЬ  →", True)
    DrawCentered(Screen, "ESC — выход", FontSmall, COLOR_TEXT_MUTED, (BASE_WIDTH // 2, 590))


def DrawDifficultySelect():
    DrawHeader("СЛОЖНОСТЬ", f"Картина: {Pictures[Game['PictureIndex']]['name']}")
    preview = pygame.transform.smoothscale(Pictures[Game["PictureIndex"]]["image"], (300, 190))
    Screen.blit(preview, (75, 175))
    pygame.draw.rect(Screen, COLOR_NEON_BLUE, (75, 175, 300, 190), 2, border_radius=10)
    DrawCentered(Screen, "КОЛИЧЕСТВО ДЕТАЛЕЙ", Font, COLOR_TEXT, (620, 185))
    options = [(2, "4 детали"), (3, "9 деталей"), (4, "16 деталей"), (5, "25 деталей"), (6, "36 деталей")]
    for index, (grid_size, label) in enumerate(options):
        rect = pygame.Rect(475, 220 + index * 55, 290, 44)
        DrawButton(Screen, rect, label, Game["GridSize"] == grid_size)
    DrawButton(Screen, pygame.Rect(70, 490, 220, 48), "← НАЗАД", False, COLOR_NEON_PINK)
    DrawButton(Screen, pygame.Rect(610, 490, 220, 48), "НАЧАТЬ СБОРКУ", True, COLOR_NEON_GREEN)
    DrawCentered(Screen, "Меньше деталей — проще. Больше деталей — сложнее.", FontSmall, COLOR_TEXT_MUTED, (BASE_WIDTH // 2, 575))


def DrawPuzzle():
    board = Game["BoardRect"]
    pygame.draw.rect(Screen, (9, 12, 29), board, border_radius=8)
    pygame.draw.rect(Screen, (255, 255, 255, 40), board, 1, border_radius=8)
    # Силуэт-подсказка и сетка целевых ячеек.
    if Game["ShowPreview"]:
        preview = pygame.transform.smoothscale(Pictures[Game["PictureIndex"]]["image"], board.size)
        preview.set_alpha(45)
        Screen.blit(preview, board.topleft)
    for row in range(Game["GridSize"]):
        for column in range(Game["GridSize"]):
            cell = pygame.Rect(round(board.x + column * Game["TileWidth"]), round(board.y + row * Game["TileHeight"]), round(Game["TileWidth"]), round(Game["TileHeight"]))
            pygame.draw.rect(Screen, (255, 255, 255, 35), cell, 1)
    pygame.draw.rect(Screen, COLOR_GLASS, (575, 125, 290, 445), border_radius=14)
    pygame.draw.rect(Screen, (255, 255, 255, 28), (575, 125, 290, 445), 1, border_radius=14)
    DrawCentered(Screen, "ФРАГМЕНТЫ", Font, COLOR_TEXT, (720, 135))

    for piece in Game["Pieces"]:
        rect = PieceRect(piece)
        Screen.blit(piece["image"], rect)
        border_color = COLOR_NEON_GREEN if piece["placed"] else (235, 240, 255)
        pygame.draw.rect(Screen, border_color, rect, 2 if piece["placed"] else 1)
        if Game["SelectedPiece"] == piece["id"]:
            DrawGlowRect(Screen, rect, COLOR_NEON_BLUE, 2, 8)

    # Верхняя панель с показателями.
    pygame.draw.rect(Screen, COLOR_GLASS, (0, 0, BASE_WIDTH, 90))
    pygame.draw.line(Screen, COLOR_NEON_BLUE, (0, 90), (BASE_WIDTH, 90), 2)
    elapsed = time.time() - Game["StartTime"]
    Screen.blit(Font.render(f"Собрано: {Game['Placed']}/{len(Game['Pieces'])}", True, COLOR_TEXT), (24, 14))
    Screen.blit(Font.render(f"Время: {int(elapsed // 60):02}:{int(elapsed % 60):02}", True, COLOR_TEXT), (330, 14))
    Screen.blit(Font.render(f"Ошибки: {Game['Mistakes']}", True, COLOR_NEON_PINK if Game["Mistakes"] else COLOR_TEXT), (650, 14))
    sensitivity_name = SENSITIVITY_NAMES[Game["SensitivityIndex"]]
    Screen.blit(FontSmall.render(f"Чувствительность: {sensitivity_name}", True, COLOR_NEON_BLUE), (25, 55))
    Screen.blit(FontSmall.render("ЛКМ / кнопка: взять и отпустить  •  H: подсказка  •  ESC: меню", True, COLOR_TEXT_MUTED), (330, 58))

    if time.time() < Game["PlacementFeedbackUntil"]:
        feedback_text = "Верно!" if Game["LastPlacementCorrect"] else "Поднесите фрагмент ближе к его месту"
        feedback_color = COLOR_NEON_GREEN if Game["LastPlacementCorrect"] else COLOR_NEON_PINK
        DrawCentered(Screen, feedback_text, FontSmall, feedback_color, (BASE_WIDTH // 2, 112))

    # Курсор-прицел
    cursor_x, cursor_y = int(Game["CursorX"]), int(Game["CursorY"])
    pygame.draw.circle(Screen, COLOR_NEON_BLUE, (cursor_x, cursor_y), 12, 2)
    pygame.draw.line(Screen, COLOR_NEON_BLUE, (cursor_x - 18, cursor_y), (cursor_x + 18, cursor_y), 2)
    pygame.draw.line(Screen, COLOR_NEON_BLUE, (cursor_x, cursor_y - 18), (cursor_x, cursor_y + 18), 2)


def DrawResults():
    duration = max(0.0, Game["EndTime"] - Game["StartTime"])
    total_pieces = max(1, len(Game["Pieces"]))
    placementRate = min(100.0, Game["Placed"] / max(1, Game["Moves"]) * 100.0)
    cursorPathLength = float(Game.get("CursorPathLengthPx", 0.0))
    averageCursorSpeed = cursorPathLength / duration if duration > 0 else 0.0
    averageActiveSpeed = Game.get("ActiveMovementDistancePx", 0.0) / Game.get("ActiveMovementSeconds", 1.0) if Game.get("ActiveMovementSeconds", 0.0) > 0 else 0.0
    score = max(0, Game["Placed"] * 100 - Game["Mistakes"] * 15 - max(0, Game["Moves"] - Game["Placed"]) * 3)
    panel = pygame.Rect(45, 28, 810, 594)
    pygame.draw.rect(Screen, COLOR_GLASS, panel, border_radius=24)
    DrawGlowRect(Screen, panel, COLOR_NEON_GREEN, 2, 14)
    DrawCentered(Screen, "ПАЗЛ СОБРАН!", FontBig, COLOR_NEON_GREEN, (BASE_WIDTH // 2, 78))
    DrawCentered(Screen, Pictures[Game["PictureIndex"]]["name"], Font, COLOR_TEXT, (BASE_WIDTH // 2, 119))
    stats = [
        ("Время сборки", f"{int(duration // 60):02}:{int(duration % 60):02}"),
        ("Деталей размещено", f"{Game['Placed']} / {total_pieces}"),
        ("Попыток размещения", str(Game["Moves"])),
        ("Ошибок размещения", str(Game["Mistakes"])),
        ("Успешные размещения", f"{placementRate:.1f}%"),
        ("Итоговый счёт", str(score)),
        ("Длина траектории курсора", f"{cursorPathLength:.0f} px"),
        ("Средняя скорость курсора", f"{averageCursorSpeed:.1f} px/сек"),
        ("Скорость в движении", f"{averageActiveSpeed:.1f} px/сек"),
    ]
    for index, (label, value) in enumerate(stats):
        column = index % 3
        row = index // 3
        x = 72 + column * 260
        y = 158 + row * 82
        pygame.draw.rect(Screen, (255, 255, 255, 12), (x, y, 242, 66), border_radius=10)
        Screen.blit(FontSmall.render(label, True, COLOR_TEXT_MUTED), (x + 10, y + 7))
        Screen.blit(Font.render(value, True, COLOR_NEON_GREEN if index in (1, 4, 5) else COLOR_TEXT), (x + 10, y + 34))
    DrawButton(Screen, pygame.Rect(75, 550, 230, 48), "ЕЩЁ РАЗ", True, COLOR_NEON_GREEN)
    DrawButton(Screen, pygame.Rect(335, 550, 230, 48), "ДРУГАЯ КАРТИНА", False, COLOR_NEON_BLUE)
    DrawButton(Screen, pygame.Rect(595, 550, 230, 48), "ВЫХОД", False, COLOR_NEON_PINK)


def BeginPlay():
    BuildPuzzle()


Running = True
while Running:
    Clock.tick(FPS)
    HandleControllerInput()

    # Плавное движение курсора контроллера.
    if Game["State"] == "PLAY":
        Game["CursorX"] += (Game["TargetX"] - Game["CursorX"]) * 0.22
        Game["CursorY"] += (Game["TargetY"] - Game["CursorY"]) * 0.22
        if Game["SelectedPiece"] is not None:
            selected_piece = next((piece for piece in Game["Pieces"] if piece["id"] == Game["SelectedPiece"]), None)
            if selected_piece:
                selected_piece["x"] = int(Game["CursorX"] - Game["DragOffsetX"])
                selected_piece["y"] = int(Game["CursorY"] - Game["DragOffsetY"])

    DrawBackground(Screen)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            Running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if Game["State"] == "PLAY":
                    CapturePuzzleRound("interrupted")
                    Game["State"] = "DIFFICULTY_SELECT"
                    Game["SelectedPiece"] = None
                elif Game["State"] == "DIFFICULTY_SELECT":
                    Game["State"] = "IMAGE_SELECT"
                else:
                    Running = False
            elif Game["State"] == "PORT_SELECT":
                if event.key in (pygame.K_UP, pygame.K_w) and Joysticks:
                    SelectedPortIndex = (SelectedPortIndex - 1) % len(Joysticks)
                elif event.key in (pygame.K_DOWN, pygame.K_s) and Joysticks:
                    SelectedPortIndex = (SelectedPortIndex + 1) % len(Joysticks)
                elif event.key == pygame.K_F5:
                    RefreshJoysticks()
                    PortStatusMessage = f"Найдено HID-устройств: {len(Joysticks)}"
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    ConfirmSelectedPort()
            elif Game["State"] == "IMAGE_SELECT":
                if event.key in (pygame.K_RIGHT, pygame.K_d):
                    Game["PictureIndex"] = (Game["PictureIndex"] + 1) % len(Pictures)
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    Game["PictureIndex"] = (Game["PictureIndex"] - 1) % len(Pictures)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    Game["State"] = "DIFFICULTY_SELECT"
            elif Game["State"] == "DIFFICULTY_SELECT":
                if event.key in (pygame.K_UP, pygame.K_w):
                    Game["GridSize"] = max(2, Game["GridSize"] - 1)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    Game["GridSize"] = min(6, Game["GridSize"] + 1)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    BeginPlay()
            elif Game["State"] == "PLAY":
                if event.key == pygame.K_h:
                    Game["ShowPreview"] = not Game["ShowPreview"]
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if Game["SelectedPiece"] is None:
                        StartDrag((int(Game["CursorX"]), int(Game["CursorY"])))
                    else:
                        ReleasePiece()
                elif event.key == pygame.K_TAB:
                    Game["SensitivityIndex"] = (Game["SensitivityIndex"] + 1) % len(SENSITIVITY_NAMES)
            elif Game["State"] == "END":
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    BeginPlay()
        elif event.type == pygame.MOUSEMOTION:
            if Game["State"] == "PLAY" and Joy is None:
                logical_x, logical_y = ToLogicalPos(event.pos)
                Game["CursorX"], Game["CursorY"] = logical_x, logical_y
                Game["TargetX"], Game["TargetY"] = logical_x, logical_y
                if Game["SelectedPiece"] is not None:
                    selected_piece = next((piece for piece in Game["Pieces"] if piece["id"] == Game["SelectedPiece"]), None)
                    if selected_piece:
                        selected_piece["x"] = int(logical_x - Game["DragOffsetX"])
                        selected_piece["y"] = int(logical_y - Game["DragOffsetY"])
        elif event.type == pygame.MOUSEBUTTONDOWN:
            logical_pos = ToLogicalPos(event.pos)
            if Game["State"] == "PORT_SELECT" and event.button == 1:
                if pygame.Rect(185, 435, 245, 42).collidepoint(logical_pos):
                    RefreshJoysticks()
                    PortStatusMessage = f"Найдено HID-устройств: {len(Joysticks)}"
                elif pygame.Rect(450, 435, 265, 42).collidepoint(logical_pos):
                    Joy = None
                    Game["State"] = "IMAGE_SELECT"
                else:
                    for index, joystick_info in enumerate(Joysticks[:5]):
                        if pygame.Rect(185, 185 + index * 48, 530, 40).collidepoint(logical_pos):
                            SelectedPortIndex = index
                            ConfirmSelectedPort()
                            break
            elif Game["State"] == "IMAGE_SELECT" and event.button == 1:
                card_width, card_height = 190, 175
                gap = 20
                total_width = len(Pictures) * card_width + (len(Pictures) - 1) * gap
                start_x = (BASE_WIDTH - total_width) // 2
                for index in range(len(Pictures)):
                    rect = pygame.Rect(start_x + index * (card_width + gap), 170, card_width, card_height)
                    if rect.collidepoint(logical_pos):
                        Game["PictureIndex"] = index
                        Game["State"] = "DIFFICULTY_SELECT"
                if pygame.Rect(310, 510, 280, 50).collidepoint(logical_pos):
                    Game["State"] = "DIFFICULTY_SELECT"
            elif Game["State"] == "DIFFICULTY_SELECT" and event.button == 1:
                for grid_size, _label in [(2, "4 детали"), (3, "9 деталей"), (4, "16 деталей"), (5, "25 деталей"), (6, "36 деталей")]:
                    index = grid_size - 2
                    rect = pygame.Rect(475, 220 + index * 55, 290, 44)
                    if rect.collidepoint(logical_pos):
                        Game["GridSize"] = grid_size
                if pygame.Rect(70, 490, 220, 48).collidepoint(logical_pos):
                    Game["State"] = "IMAGE_SELECT"
                elif pygame.Rect(610, 490, 220, 48).collidepoint(logical_pos):
                    BeginPlay()
            elif Game["State"] == "PLAY":
                if event.button == 1:
                    Game["CursorX"], Game["CursorY"] = logical_pos
                    if Game["SelectedPiece"] is None:
                        StartDrag(logical_pos)
                    else:
                        ReleasePiece()
                elif event.button == 3:
                    Game["ShowPreview"] = not Game["ShowPreview"]
            elif Game["State"] == "END" and event.button == 1:
                if pygame.Rect(75, 550, 230, 48).collidepoint(logical_pos):
                    BeginPlay()
                elif pygame.Rect(335, 550, 230, 48).collidepoint(logical_pos):
                    Game["State"] = "IMAGE_SELECT"
                elif pygame.Rect(595, 550, 230, 48).collidepoint(logical_pos):
                    Running = False

    UpdatePuzzleCursorMetrics()
    statsNow = time.monotonic()
    if statsNow - puzzleLastStatsSaveTime >= 2.0:
        savePuzzleStatistics("interrupted", includeActiveRound=True)
        puzzleLastStatsSaveTime = statsNow

    # Рендер текущего состояния
    if Game["State"] == "PORT_SELECT":
        DrawPortSelect()
    elif Game["State"] == "IMAGE_SELECT":
        DrawImageSelect()
    elif Game["State"] == "DIFFICULTY_SELECT":
        DrawDifficultySelect()
    elif Game["State"] == "PLAY":
        DrawPuzzle()
    elif Game["State"] == "END":
        DrawResults()

    ScaledFrame = pygame.transform.smoothscale(Screen, (DisplayWidth, DisplayHeight))
    DisplayScreen.blit(ScaledFrame, (0, 0))
    pygame.display.flip()

finalizePuzzleStatistics()
if Joy:
    Joy.quit()
pygame.quit()
sys.exit()
