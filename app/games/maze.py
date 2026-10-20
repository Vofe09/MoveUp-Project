import os
import sys
import time
import math
import random
from typing import List, Optional, Tuple, Dict
import pygame
import atexit
import uuid
from pathlib import Path

_scriptDirectory = Path(__file__).resolve().parent
for _candidateDirectory in (_scriptDirectory, *_scriptDirectory.parents):
    if (_candidateDirectory / "moveup_stats.py").exists():
        sys.path.insert(0, str(_candidateDirectory))
        break
from moveup_stats import MoveUpStatistics, metric

statistics = MoveUpStatistics("Maze", "maze", sys.executable if getattr(sys, "frozen", False) else __file__)
sessionFinalized = False

# =================================================================
#  БЛОК НАСТРОЙКИ УПРАВЛЕНИЯ И ЧУВСТВИТЕЛЬНОСТИ
# =================================================================
# HID-раскладка:
# Л вертикаль = Axis 3
# Л горизонталь = Axis 2
# П вертикаль = Axis 1
# П горизонталь = Axis 0
JOY_SIGN_X = -1
JOY_SIGN_Y = -1

# Прессеты чувствительности (циклируются через joystick3Button)
SENSITIVITY_PRESETS = {
    "Низкая": 0.005,
    "Средняя": 0.011,
    "Высокая": 0.020,
    "Максимальная": 0.035
}
SENSITIVITY_NAMES = list(SENSITIVITY_PRESETS.keys())
# =================================================================

pygame.init()
pygame.joystick.init()

BaseWidth = 900
BaseHeight = 700

DisplayScreen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
DisplayWidth, DisplayHeight = DisplayScreen.get_size()

Screen = pygame.Surface((BaseWidth, BaseHeight), pygame.SRCALPHA)
pygame.display.set_caption("Cyber Maze - Rehabilitation Edition")
Clock = pygame.time.Clock()

FontSmall = pygame.font.SysFont("Segoe UI", 16)
Font = pygame.font.SysFont("Segoe UI", 22, bold=True)
FontBig = pygame.font.SysFont("Segoe UI", 48, bold=True)

# Неоновая Glassmorphic палитра
COLOR_BG_DARK = (15, 12, 30)
COLOR_BG_LIGHT = (25, 30, 60)
COLOR_GLASS = (25, 25, 50, 200)
COLOR_TEXT = (255, 255, 255)
COLOR_TEXT_DARK = (10, 15, 45)
COLOR_TEXT_MUTED = (140, 150, 190)
COLOR_NEON_BLUE = (0, 240, 255)
COLOR_NEON_PINK = (255, 0, 128)
COLOR_NEON_GREEN = (0, 255, 150)

# Стены с выраженным внешним контуром
COLOR_WALL = (65, 55, 115)         
COLOR_WALL_BORDER = (130, 100, 255) 
COLOR_CORRIDOR = (10, 8, 20)

# Глобальное состояние игры
GameState = "PORT_SELECT"  # PORT_SELECT, PLAY, END, REVIEW
SelectedDifficulty = "Средний"
Difficulties = ["Легкий", "Средний", "Сложный"]
DiffIdx = 1

AvailablePorts = []
SelectedPortIndex = 0
Joy: Optional[pygame.joystick.Joystick] = None

# Данные лабиринта
MazeGrid = []
MazeCols, MazeRows = 0, 0
CellSize = 0
OffsetX, OffsetY = 0, 0
MazeWalls = []       
SolutionPath = []    
FinishRect = pygame.Rect(0,0,0,0)
StartX, StartY = 0.0, 0.0

Game = {
    "PlayerX": 0.0, "PlayerY": 0.0,
    "TargetX": 0.0, "TargetY": 0.0,
    "StartTime": 0.0, "EndTime": 0.0,
    "CollisionCount": 0,
    "Trajectory": [],
    "PathLengthPx": 0.0,
    "SpeedSumPxPerSecond": 0.0,
    "SpeedSquaredSum": 0.0,
    "SpeedSamples": 0,
    "CurrentSpeedPxPerSecond": 0.0,
    "MaxSpeedPxPerSecond": 0.0,
    "MovementActiveSeconds": 0.0,
    "ActiveMovementDistancePx": 0.0,
    "DeviationSum": 0.0,
    "DeviationSamples": 0,
    "CurrentDeviation": 0.0,
    "MaxDeviationPx": 0.0,
    "FirstMovementLatencySeconds": None,
    "HintCount": 0,
    "RoundId": "",
    "RoundCaptured": False,
    "RoundSensitivityPresetsUsed": set(),
    "ShowHint": False,
    "HintTime": 0.0
}

PrevBtn1 = 0
PrevBtn2 = 0
PrevBtn3 = 0

# Текущий индекс прессета чувствительности
CurrentSensitivityIdx = 1  # По умолчанию "Средняя"

def GetCurrentSensitivity():
    return SENSITIVITY_PRESETS[SENSITIVITY_NAMES[CurrentSensitivityIdx]]


def RefreshPorts():
    global AvailablePorts, SelectedPortIndex
    AvailablePorts = []
    for index in range(pygame.joystick.get_count()):
        joystick = pygame.joystick.Joystick(index)
        joystick.init()
        AvailablePorts.append(joystick.get_name())
        joystick.quit()
    SelectedPortIndex = 0

RefreshPorts()
roundHistory = []
sensitivityPresetsUsed = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}
sessionAborted = False
lastStatsSaveTime = 0.0


def BuildMaze(difficulty: str):
    global MazeGrid, MazeCols, MazeRows, CellSize, OffsetX, OffsetY, MazeWalls, SolutionPath, FinishRect, StartX, StartY
    
    if difficulty == "Легкий":
        MazeCols, MazeRows = 9, 7
    elif difficulty == "Средний":
        MazeCols, MazeRows = 15, 11
    else:
        MazeCols, MazeRows = 23, 17
        
    CellSize = min((BaseWidth - 60) // MazeCols, (BaseHeight - 160) // MazeRows)
    OffsetX = (BaseWidth - (MazeCols * CellSize)) // 2
    OffsetY = 100 + (BaseHeight - 160 - (MazeRows * CellSize)) // 2
    
    grid = [[1 * 15 for _ in range(MazeRows)] for _ in range(MazeCols)] 
    visited = [[False for _ in range(MazeRows)] for _ in range(MazeCols)]
    
    stack = [(0, 0)]
    visited[0][0] = True
    
    dirs = [(0, -1), (1, 0), (0, 1), (-1, 0)]
    opposite = {0: 2, 1: 3, 2: 0, 3: 1}
    walls_mask = {0: 1, 1: 2, 2: 4, 3: 8} 
    
    while stack:
        cx, cy = stack[-1]
        neighbors = []
        for i, (dx, dy) in enumerate(dirs):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < MazeCols and 0 <= ny < MazeRows and not visited[nx][ny]:
                neighbors.append((i, nx, ny))
        
        if neighbors:
            direction, nx, ny = random.choice(neighbors)
            grid[cx][cy] &= ~walls_mask[direction]
            grid[nx][ny] &= ~walls_mask[opposite[direction]]
            visited[nx][ny] = True
            stack.append((nx, ny))
        else:
            stack.pop()
            
    queue = [[(0, 0)]]
    bfs_visited = {(0, 0)}
    found_path = []
    
    while queue:
        path = queue.pop(0)
        cx, cy = path[-1]
        if cx == MazeCols - 1 and cy == MazeRows - 1:
            found_path = path
            break
        for i, (dx, dy) in enumerate(dirs):
            if not (grid[cx][cy] & walls_mask[i]): 
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in bfs_visited:
                    bfs_visited.add((nx, ny))
                    queue.append(path + [(nx, ny)])
                    
    SolutionPath = [(OffsetX + x * CellSize + CellSize // 2, OffsetY + y * CellSize + CellSize // 2) for x, y in found_path]
    
    MazeWalls = []
    w_thick = 6
    
    # Внешние границы
    MazeWalls.append(pygame.Rect(OffsetX, OffsetY, MazeCols * CellSize, w_thick))
    MazeWalls.append(pygame.Rect(OffsetX, OffsetY, w_thick, MazeRows * CellSize))
    MazeWalls.append(pygame.Rect(OffsetX, OffsetY + MazeRows * CellSize - w_thick, MazeCols * CellSize, w_thick))
    MazeWalls.append(pygame.Rect(OffsetX + MazeCols * CellSize - w_thick, OffsetY, w_thick, MazeRows * CellSize))
    
    for x in range(MazeCols):
        for y in range(MazeRows):
            rx = OffsetX + x * CellSize
            ry = OffsetY + y * CellSize
            if grid[x][y] & 1: 
                MazeWalls.append(pygame.Rect(rx, ry, CellSize, w_thick))
            if grid[x][y] & 2: 
                MazeWalls.append(pygame.Rect(rx + CellSize - w_thick, ry, w_thick, CellSize))
            if grid[x][y] & 4: 
                MazeWalls.append(pygame.Rect(rx, ry + CellSize - w_thick, CellSize, w_thick))
            if grid[x][y] & 8: 
                MazeWalls.append(pygame.Rect(rx, ry, w_thick, CellSize))
                
    FinishRect = pygame.Rect(OffsetX + (MazeCols - 1) * CellSize + w_thick * 2, OffsetY + (MazeRows - 1) * CellSize + w_thick * 2, CellSize - w_thick * 4, CellSize - w_thick * 4)
    StartX = OffsetX + CellSize // 2
    StartY = OffsetY + CellSize // 2

def ResetGame():
    BuildMaze(SelectedDifficulty)
    Game["PlayerX"] = StartX
    Game["PlayerY"] = StartY
    Game["TargetX"] = StartX
    Game["TargetY"] = StartY
    Game["StartTime"] = time.monotonic()
    Game["EndTime"] = 0.0
    Game["CollisionCount"] = 0
    Game["Trajectory"] = [(StartX, StartY)]
    Game["PathLengthPx"] = 0.0
    Game["SpeedSumPxPerSecond"] = 0.0
    Game["SpeedSquaredSum"] = 0.0
    Game["SpeedSamples"] = 0
    Game["CurrentSpeedPxPerSecond"] = 0.0
    Game["MaxSpeedPxPerSecond"] = 0.0
    Game["MovementActiveSeconds"] = 0.0
    Game["ActiveMovementDistancePx"] = 0.0
    Game["DeviationSum"] = 0.0
    Game["DeviationSamples"] = 0
    Game["CurrentDeviation"] = 0.0
    Game["MaxDeviationPx"] = 0.0
    Game["FirstMovementLatencySeconds"] = None
    Game["HintCount"] = 0
    Game["RoundId"] = str(uuid.uuid4())
    Game["RoundCaptured"] = False
    Game["RoundSensitivityPresetsUsed"] = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}
    Game["ShowHint"] = False
    sensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])

def GetDistanceToIdealPath(px, py):
    if not SolutionPath: return 0.0
    min_d = float('inf')
    for i in range(len(SolutionPath) - 1):
        x1, y1 = SolutionPath[i]
        x2, y2 = SolutionPath[i+1]
        
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            d = math.sqrt((px - x1)**2 + (py - y1)**2)
        else:
            t = ((px - x1) * dx + (py - y1) * dy) / (dx**2 + dy**2)
            t = max(0.0, min(1.0, t))
            nx, ny = x1 + t * dx, y1 + t * dy
            d = math.sqrt((px - nx)**2 + (py - ny)**2)
        if d < min_d: min_d = d
    return min_d


def makeMazeRoundSnapshot(outcome, completed, endTime=None):
    roundEndTime = Game["EndTime"] if endTime is None and Game["EndTime"] > 0 else endTime
    if roundEndTime is None:
        roundEndTime = time.monotonic()
    durationSeconds = max(0.0, roundEndTime - Game["StartTime"]) if Game["StartTime"] > 0 else 0.0
    return {
        "round_id": Game["RoundId"],
        "outcome": outcome,
        "completed": bool(completed),
        "duration_seconds": durationSeconds,
        "difficulty": SelectedDifficulty,
        "wall_collisions": int(Game["CollisionCount"]),
        "hint_count": int(Game["HintCount"]),
        "path_length_px": float(Game["PathLengthPx"]),
        "speed_sum_px_per_second": float(Game["SpeedSumPxPerSecond"]),
        "speed_squared_sum": float(Game["SpeedSquaredSum"]),
        "speed_samples": int(Game["SpeedSamples"]),
        "max_speed_px_per_second": float(Game["MaxSpeedPxPerSecond"]),
        "movement_active_seconds": float(Game["MovementActiveSeconds"]),
        "active_movement_distance_px": float(Game["ActiveMovementDistancePx"]),
        "deviation_sum_px": float(Game["DeviationSum"]),
        "deviation_samples": int(Game["DeviationSamples"]),
        "max_deviation_px": float(Game["MaxDeviationPx"]),
        "first_movement_latency_seconds": Game["FirstMovementLatencySeconds"],
        "sensitivity_presets_used": sorted(Game["RoundSensitivityPresetsUsed"]),
    }


def captureMazeRound(outcome, completed=False):
    if Game["RoundCaptured"] or Game["StartTime"] <= 0:
        return
    if Game["EndTime"] <= 0:
        Game["EndTime"] = time.monotonic()
    roundHistory.append(makeMazeRoundSnapshot(outcome, completed, Game["EndTime"]))
    Game["RoundCaptured"] = True
    saveStatistics("interrupted")


def saveStatistics(sessionStatus, includeActiveRound=False):
    sessionRounds = list(roundHistory)
    if includeActiveRound and Game["StartTime"] > 0 and not Game["RoundCaptured"] and GameState == "PLAY":
        sessionRounds.append(makeMazeRoundSnapshot("in_progress", False))

    roundsPlayed = len(sessionRounds)
    roundsCompleted = sum(1 for item in sessionRounds if item["outcome"] == "completed")
    roundsRestarted = sum(1 for item in sessionRounds if item["outcome"] == "restarted")
    roundsInterrupted = sum(1 for item in sessionRounds if item["outcome"] in ("interrupted", "in_progress"))
    activeDuration = sum(item["duration_seconds"] for item in sessionRounds)
    completionTimes = [item["duration_seconds"] for item in sessionRounds if item["outcome"] == "completed"]
    collisions = sum(item["wall_collisions"] for item in sessionRounds)
    hints = sum(item["hint_count"] for item in sessionRounds)
    pathLength = sum(item["path_length_px"] for item in sessionRounds)
    speedSum = sum(item["speed_sum_px_per_second"] for item in sessionRounds)
    speedSquaredSum = sum(item["speed_squared_sum"] for item in sessionRounds)
    speedSamples = sum(item["speed_samples"] for item in sessionRounds)
    maxSpeed = max((item["max_speed_px_per_second"] for item in sessionRounds), default=0.0)
    movementSeconds = sum(item["movement_active_seconds"] for item in sessionRounds)
    movementDistance = sum(item["active_movement_distance_px"] for item in sessionRounds)
    deviationSum = sum(item["deviation_sum_px"] for item in sessionRounds)
    deviationSamples = sum(item["deviation_samples"] for item in sessionRounds)
    maxDeviation = max((item["max_deviation_px"] for item in sessionRounds), default=0.0)
    movementLatencies = [item["first_movement_latency_seconds"] for item in sessionRounds if item["first_movement_latency_seconds"] is not None]
    meanSampleSpeed = speedSum / speedSamples if speedSamples else 0.0
    speedVariance = max(0.0, speedSquaredSum / speedSamples - meanSampleSpeed ** 2) if speedSamples else 0.0
    averageSpeed = pathLength / activeDuration if activeDuration else 0.0
    averageMovingSpeed = movementDistance / movementSeconds if movementSeconds else 0.0
    averageDeviation = deviationSum / deviationSamples if deviationSamples else 0.0

    metrics = {
        "roundsPlayed": metric("Попытки прохождения", roundsPlayed, "шт."),
        "roundsCompleted": metric("Успешные прохождения", roundsCompleted, "шт."),
        "roundsRestarted": metric("Перезапущенные попытки", roundsRestarted, "шт."),
        "roundsInterrupted": metric("Прерванные попытки", roundsInterrupted, "шт."),
        "wallCollisionCount": metric("Столкновения со стенами", collisions, "шт."),
        "hintCount": metric("Использованные подсказки", hints, "шт."),
        "activeGameplaySeconds": metric("Суммарное время попыток", activeDuration, "сек."),
        "cursorPathLengthPx": metric("Суммарная длина траектории курсора", pathLength, "px"),
        "averageCursorSpeedPxPerSecond": metric("Средняя скорость курсора за время попыток", averageSpeed, "px/сек"),
        "averageActiveMovementSpeedPxPerSecond": metric("Средняя скорость во время движения", averageMovingSpeed, "px/сек"),
        "maxCursorSpeedPxPerSecond": metric("Максимальная скорость курсора", maxSpeed, "px/сек"),
        "cursorSpeedStandardDeviationPxPerSecond": metric("Стандартное отклонение скорости курсора", math.sqrt(speedVariance), "px/сек"),
        "averageDistanceToShortestRoutePx": metric("Среднее расстояние до кратчайшего маршрута", averageDeviation, "px"),
        "maxDistanceToShortestRoutePx": metric("Максимальное расстояние до кратчайшего маршрута", maxDeviation, "px"),
    }
    if roundsPlayed:
        metrics["completionRatePct"] = metric("Доля успешных прохождений от всех попыток", roundsCompleted / roundsPlayed * 100.0, "%")
    if completionTimes:
        metrics["averageCompletionTimeSeconds"] = metric("Среднее время успешного прохождения", sum(completionTimes) / len(completionTimes), "сек.")
        metrics["fastestCompletionTimeSeconds"] = metric("Лучшее время прохождения", min(completionTimes), "сек.")
    if movementLatencies:
        metrics["averageTimeToFirstMovementSeconds"] = metric("Среднее время до первого движения", sum(movementLatencies) / len(movementLatencies), "сек.")

    settings = {
        "logical_resolution_px": {"width": BaseWidth, "height": BaseHeight},
        "cursor_smoothing_factor": 0.22,
        "movement_active_threshold_px_per_second": 5.0,
        "difficulty_levels_used": sorted({item["difficulty"] for item in sessionRounds}),
        "sensitivity_presets_used": sorted(sensitivityPresetsUsed),
        "route_deviation_method": "minimum Euclidean distance to any segment of the BFS shortest route",
    }
    statistics.save(sessionStatus, metrics, settings, {"rounds": sessionRounds})


def emergencySaveStatistics():
    if sessionFinalized:
        return
    try:
        if GameState == "PLAY" and Game["StartTime"] > 0 and not Game["RoundCaptured"]:
            captureMazeRound("interrupted", False)
        saveStatistics("interrupted", includeActiveRound=True)
    except Exception as error:
        print(f"Не удалось сохранить статистику при выходе: {error}")


atexit.register(emergencySaveStatistics)

def ToLogicalPos(pos):
    return (pos[0] * BaseWidth / DisplayWidth, pos[1] * BaseHeight / DisplayHeight)

def DrawGlowCircle(surf, pos, r, color, glow_r):
    for gr in range(glow_r, 0, -2):
        alpha = int((1.0 - (gr / glow_r)) * 65)
        s = pygame.Surface((r*2 + gr*2, r*2 + gr*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (color[0], color[1], color[2], alpha), (r + gr, r + gr), r + gr)
        surf.blit(s, (pos[0] - r - gr, pos[1] - r - gr))

def DrawGlowRect(surface, rect, color, thickness, glow_radius):
    for r in range(glow_radius, 0, -2):
        alpha = int((1.0 - (r / glow_radius)) * 50)
        glow_surf = pygame.Surface((rect.width + r*2, rect.height + r*2), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, (color[0], color[1], color[2], alpha), (0, 0, rect.width + r*2, rect.height + r*2), thickness + r, border_radius=8)
        surface.blit(glow_surf, (rect.x - r, rect.y - r))

Running = True
while Running:
    FrameDelta = max(0.001, Clock.tick(60) / 1000.0)
    
    for y in range(BaseHeight):
        ratio = y / BaseHeight
        r = int(COLOR_BG_DARK[0] * (1 - ratio) + COLOR_BG_LIGHT[0] * ratio)
        g = int(COLOR_BG_DARK[1] * (1 - ratio) + COLOR_BG_LIGHT[1] * ratio)
        b = int(COLOR_BG_DARK[2] * (1 - ratio) + COLOR_BG_LIGHT[2] * ratio)
        pygame.draw.line(Screen, (r, g, b), (0, y), (BaseWidth, y))

    ArduinoClicked = False
    if Joy:
        try:
            pygame.event.pump()

            # HID-кнопки
            btn1 = Joy.get_button(1)   # Л клик
            btn2 = Joy.get_button(0)   # П клик
            btn3 = Joy.get_button(3) if Joy.get_numbuttons() > 3 else 0  # GP8 = HID Button 4

            # Центральная кнопка циклирует чувствительность
            if btn3 == 1 and PrevBtn3 == 0:
                CurrentSensitivityIdx = (CurrentSensitivityIdx + 1) % len(SENSITIVITY_NAMES)
                sensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])
                if Game["StartTime"] > 0 and not Game["RoundCaptured"]:
                    Game["RoundSensitivityPresetsUsed"].add(SENSITIVITY_NAMES[CurrentSensitivityIdx])

            if GameState == "PLAY":
                current_speed = GetCurrentSensitivity()

                # HID-раскладка:
                # Л вертикаль = Axis 3
                # Л горизонталь = Axis 2
                # П вертикаль = Axis 1
                # П горизонталь = Axis 0
                left_vertical = Joy.get_axis(3)
                left_horizontal = Joy.get_axis(2)
                right_vertical = Joy.get_axis(1)
                right_horizontal = Joy.get_axis(0)

                # Левый стик
                if abs(left_horizontal) > 0.05:
                    Game["TargetX"] += int(left_horizontal * 512 * current_speed * JOY_SIGN_X)
                if abs(left_vertical) > 0.05:
                    Game["TargetY"] += int(left_vertical * 512 * current_speed * JOY_SIGN_Y)

                # Правый стик
                if abs(right_horizontal) > 0.05:
                    Game["TargetX"] += int(right_horizontal * 512 * current_speed * JOY_SIGN_X)
                if abs(right_vertical) > 0.05:
                    Game["TargetY"] += int(right_vertical * 512 * current_speed * JOY_SIGN_Y)

                Game["TargetX"] = max(OffsetX, min(OffsetX + MazeCols * CellSize, Game["TargetX"]))
                Game["TargetY"] = max(OffsetY, min(OffsetY + MazeRows * CellSize, Game["TargetY"]))

            if (btn1 == 1 and PrevBtn1 == 0) or (btn2 == 1 and PrevBtn2 == 0):
                ArduinoClicked = True

            PrevBtn1, PrevBtn2, PrevBtn3 = btn1, btn2, btn3
        except Exception as e:
            print("JOY ERROR:", repr(e))
            raise

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            if GameState == "PLAY":
                sessionAborted = True
                captureMazeRound("interrupted", False)
            Running = False

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if GameState == "PLAY":
                    sessionAborted = True
                    captureMazeRound("interrupted", False)
                Running = False
            if GameState == "PORT_SELECT":
                if event.key == pygame.K_UP: SelectedPortIndex = (SelectedPortIndex - 1) % max(1, len(AvailablePorts))
                elif event.key == pygame.K_DOWN: SelectedPortIndex = (SelectedPortIndex + 1) % max(1, len(AvailablePorts))
                elif event.key == pygame.K_LEFT: DiffIdx = (DiffIdx - 1) % 3; SelectedDifficulty = Difficulties[DiffIdx]
                elif event.key == pygame.K_RIGHT: DiffIdx = (DiffIdx + 1) % 3; SelectedDifficulty = Difficulties[DiffIdx]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if AvailablePorts:
                        try:
                            Joy = pygame.joystick.Joystick(SelectedPortIndex)
                            Joy.init()
                        except:
                            pass
                    ResetGame()
                    GameState = "PLAY"
            elif GameState in ("END", "REVIEW"):
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    ResetGame()
                    GameState = "PLAY"

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            LPos = ToLogicalPos(event.pos)
            
            if GameState == "PORT_SELECT":
                for idx, _ in enumerate(AvailablePorts):
                    if pygame.Rect(BaseWidth // 2 - 220, 140 + idx * 48, 440, 40).collidepoint(LPos):
                        SelectedPortIndex = idx
                for idx, diff in enumerate(Difficulties):
                    if pygame.Rect(BaseWidth // 2 - 220 + idx * 150, 390, 140, 42).collidepoint(LPos):
                        SelectedDifficulty = diff
                        DiffIdx = idx
                if pygame.Rect(BaseWidth // 2 - 120, BaseHeight - 110, 240, 46).collidepoint(LPos):
                    if AvailablePorts:
                        try:
                            Joy = pygame.joystick.Joystick(SelectedPortIndex)
                            Joy.init()
                        except:
                            pass
                    ResetGame()
                    GameState = "PLAY"
                    
            elif GameState == "PLAY":
                if pygame.Rect(15, 23, 210, 40).collidepoint(LPos):
                    captureMazeRound("restarted", False)
                    ResetGame()
                if pygame.Rect(240, 23, 100, 40).collidepoint(LPos):
                    Game["ShowHint"] = True
                    Game["HintTime"] = time.time()
                    Game["HintCount"] += 1
                    
            elif GameState == "END":
                if pygame.Rect(25, BaseHeight - 100, 190, 46).collidepoint(LPos):
                    ResetGame()
                    GameState = "PLAY"
                elif pygame.Rect(235, BaseHeight - 100, 210, 46).collidepoint(LPos):
                    GameState = "REVIEW"
                elif pygame.Rect(465, BaseHeight - 100, 190, 46).collidepoint(LPos):
                    GameState = "PORT_SELECT"
                    RefreshPorts()
                # Обработка клика по кнопке «В ГЛАВНОЕ МЕНЮ»
                elif pygame.Rect(675, BaseHeight - 100, 200, 46).collidepoint(LPos):
                    Running = False

            elif GameState == "REVIEW":
                if pygame.Rect(BaseWidth // 2 - 120, BaseHeight - 75, 240, 44).collidepoint(LPos):
                    GameState = "END"

    # --- ЛОГИКА И ОТРИСОВКА СОСТОЯНИЙ ---
    
    if GameState == "PORT_SELECT":
        Txt = FontBig.render("НАСТРОЙКА ЛАБИРИНТА", True, COLOR_TEXT)
        Screen.blit(Txt, ((BaseWidth - Txt.get_width()) // 2, 40))
        
        pygame.draw.rect(Screen, COLOR_GLASS, (BaseWidth//2 - 250, 110, 500, 220), border_radius=12)
        if not AvailablePorts:
            Screen.blit(Font.render("Устройства не найдены! Проверьте HID", True, COLOR_NEON_PINK), (BaseWidth//2 - 210, 140))
        else:
            for idx, port in enumerate(AvailablePorts[:4]):
                item_r = pygame.Rect(BaseWidth // 2 - 220, 140 + idx * 48, 440, 40)
                if idx == SelectedPortIndex:
                    pygame.draw.rect(Screen, COLOR_NEON_BLUE, item_r, border_radius=8)
                    col = COLOR_TEXT_DARK
                else:
                    pygame.draw.rect(Screen, (255,255,255,15), item_r, border_radius=8)
                    col = COLOR_TEXT
                Screen.blit(Font.render(f"Устройство: {port}", True, col), (item_r.x + 15, item_r.y + 8))
                
        Screen.blit(Font.render("ВЫБЕРИТЕ СЛОЖНОСТЬ:", True, COLOR_NEON_BLUE), (BaseWidth//2 - 250, 355))
        for idx, diff in enumerate(Difficulties):
            btn_d = pygame.Rect(BaseWidth // 2 - 250 + idx * 170, 390, 160, 42)
            if diff == SelectedDifficulty:
                pygame.draw.rect(Screen, COLOR_NEON_GREEN, btn_d, border_radius=8)
                col = COLOR_TEXT_DARK
            else:
                pygame.draw.rect(Screen, COLOR_GLASS, btn_d, border_radius=8)
                pygame.draw.rect(Screen, (255,255,255,30), btn_d, 1, border_radius=8)
                col = COLOR_TEXT_MUTED
            Screen.blit(Font.render(diff, True, col), (btn_d.x + (btn_d.width - Font.render(diff, True, col).get_width())//2, btn_d.y + 8))
            
        btn_s = pygame.Rect(BaseWidth // 2 - 120, BaseHeight - 110, 240, 46)
        DrawGlowRect(Screen, btn_s, COLOR_NEON_BLUE, 2, 12)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_s, border_radius=10)
        stxt = Font.render("ЗАПУСТИТЬ ИГРУ", True, COLOR_TEXT_DARK)
        Screen.blit(stxt, (btn_s.x + (btn_s.width - stxt.get_width())//2, btn_s.y + 10))

    elif GameState == "PLAY":
        old_px, old_py = Game["PlayerX"], Game["PlayerY"]
        Game["PlayerX"] += (Game["TargetX"] - Game["PlayerX"]) * 0.22
        Game["PlayerY"] += (Game["TargetY"] - Game["PlayerY"]) * 0.22
        
        movementDistance = math.hypot(Game["PlayerX"] - old_px, Game["PlayerY"] - old_py)
        speedPxPerSecond = movementDistance / FrameDelta
        Game["CurrentSpeedPxPerSecond"] = speedPxPerSecond
        Game["PathLengthPx"] += movementDistance
        Game["SpeedSumPxPerSecond"] += speedPxPerSecond
        Game["SpeedSquaredSum"] += speedPxPerSecond ** 2
        Game["SpeedSamples"] += 1
        Game["MaxSpeedPxPerSecond"] = max(Game["MaxSpeedPxPerSecond"], speedPxPerSecond)
        if speedPxPerSecond >= 5.0:
            Game["MovementActiveSeconds"] += FrameDelta
            Game["ActiveMovementDistancePx"] += movementDistance
            if Game["FirstMovementLatencySeconds"] is None:
                Game["FirstMovementLatencySeconds"] = max(0.0, time.monotonic() - Game["StartTime"])

        if int(Game["PlayerX"]) != int(old_px) or int(Game["PlayerY"]) != int(old_py):
            Game["Trajectory"].append((Game["PlayerX"], Game["PlayerY"]))

        Game["CurrentDeviation"] = GetDistanceToIdealPath(Game["PlayerX"], Game["PlayerY"])
        Game["DeviationSum"] += Game["CurrentDeviation"]
        Game["DeviationSamples"] += 1
        Game["MaxDeviationPx"] = max(Game["MaxDeviationPx"], Game["CurrentDeviation"])
        
        player_circle_r = 8
        p_rect = pygame.Rect(Game["PlayerX"] - player_circle_r, Game["PlayerY"] - player_circle_r, player_circle_r * 2, player_circle_r * 2)
        
        hit_wall = False
        for wall in MazeWalls:
            if wall.colliderect(p_rect):
                hit_wall = True
                break
                
        if hit_wall:
            Game["CollisionCount"] += 1
            Game["PlayerX"], Game["PlayerY"] = StartX, StartY
            Game["TargetX"], Game["TargetY"] = StartX, StartY
            Game["Trajectory"] = [(StartX, StartY)]
            
        if FinishRect.collidepoint(Game["PlayerX"], Game["PlayerY"]):
            Game["EndTime"] = time.monotonic()
            GameState = "END"
            captureMazeRound("completed", True)

        # Верхняя панель
        pygame.draw.rect(Screen, COLOR_GLASS, (0, 0, BaseWidth, 85))
        pygame.draw.line(Screen, COLOR_NEON_BLUE, (0, 85), (BaseWidth, 85), 2)
        
        btn_rand = pygame.Rect(15, 22, 210, 40)
        pygame.draw.rect(Screen, (255, 255, 255, 180), btn_rand, border_radius=6)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_rand, 1, border_radius=6)
        Screen.blit(FontSmall.render("НОВЫЙ ЛАБИРИНТ", True, COLOR_TEXT_DARK), (45, 31))
        
        btn_hint = pygame.Rect(240, 22, 85, 40)
        pygame.draw.rect(Screen, (255, 255, 255, 15), btn_hint, border_radius=6)
        pygame.draw.rect(Screen, COLOR_NEON_GREEN, btn_hint, 1, border_radius=6)
        Screen.blit(FontSmall.render("HINT", True, COLOR_NEON_GREEN), (265, 31))

        TimeElapsed = time.monotonic() - Game["StartTime"]
        Screen.blit(Font.render(f"Время: {TimeElapsed:.1f}с", True, COLOR_TEXT), (345, 28))
        Screen.blit(Font.render(f"Ошибки: {Game['CollisionCount']}", True, COLOR_NEON_PINK), (495, 28))
        
        speedColor = COLOR_NEON_GREEN if Game["CurrentSpeedPxPerSecond"] < 180 else COLOR_NEON_PINK
        Screen.blit(Font.render(f"Скорость: {Game['CurrentSpeedPxPerSecond']:.0f}", True, speedColor), (635, 28))
        Screen.blit(FontSmall.render(f"Отклонение: {Game['CurrentDeviation']:.1f}px", True, COLOR_TEXT_MUTED), (775, 32))
        
        # Отображение текущей чувствительности
        current_sens_name = SENSITIVITY_NAMES[CurrentSensitivityIdx]
        sens_col = COLOR_NEON_GREEN if current_sens_name in ["Средняя"] else (COLOR_NEON_BLUE if current_sens_name == "Низкая" else COLOR_NEON_PINK)
        Screen.blit(FontSmall.render(f"Чувствительность: {current_sens_name}", True, sens_col), (45, 650))

        # Поле лабиринта
        pygame.draw.rect(Screen, COLOR_CORRIDOR, (OffsetX, OffsetY, MazeCols * CellSize, MazeRows * CellSize))
        
        if Game["ShowHint"]:
            if time.time() - Game["HintTime"] < 2.0:
                if len(SolutionPath) > 1:
                    pygame.draw.lines(Screen, (0, 180, 255, 120), False, SolutionPath, 6)
            else:
                Game["ShowHint"] = False

        for wall in MazeWalls:
            pygame.draw.rect(Screen, COLOR_WALL, wall)
            pygame.draw.rect(Screen, COLOR_WALL_BORDER, wall, 1)
            
        DrawGlowRect(Screen, FinishRect, COLOR_NEON_GREEN, 2, 10)
        pygame.draw.rect(Screen, COLOR_NEON_GREEN, FinishRect, border_radius=6)
        Screen.blit(FontSmall.render("F", True, COLOR_TEXT_DARK), (FinishRect.x + FinishRect.width//2 - 5, FinishRect.y + 6))
        
        DrawGlowCircle(Screen, (int(Game["PlayerX"]), int(Game["PlayerY"])), 8, COLOR_NEON_BLUE, 14)
        pygame.draw.circle(Screen, COLOR_TEXT, (int(Game["PlayerX"]), int(Game["PlayerY"])), 8)

    elif GameState == "END":
        PanelRect = pygame.Rect(40, 40, 820, 580)
        DrawGlowRect(Screen, PanelRect, COLOR_NEON_GREEN, 2, 14)
        pygame.draw.rect(Screen, COLOR_GLASS, PanelRect, border_radius=20)
        
        Title = FontBig.render("ЛАБИРИНТ ПРОЙДЕН!", True, COLOR_NEON_GREEN)
        Screen.blit(Title, ((BaseWidth - Title.get_width()) // 2, 70))
        
        Duration = Game["EndTime"] - Game["StartTime"]
        AvgCursorSpeed = Game["PathLengthPx"] / Duration if Duration > 0 else 0.0
        AvgMovingSpeed = Game["ActiveMovementDistancePx"] / Game["MovementActiveSeconds"] if Game["MovementActiveSeconds"] > 0 else 0.0
        AvgDev = Game["DeviationSum"] / max(1, Game["DeviationSamples"])
        
        Screen.blit(Font.render(f"Сложность: {SelectedDifficulty}", True, COLOR_TEXT_MUTED), (80, 180))
        Screen.blit(Font.render(f"Время прохождения: {Duration:.2f} сек", True, COLOR_TEXT), (80, 240))
        Screen.blit(Font.render(f"Касаний стен (ошибок): {Game['CollisionCount']}", True, COLOR_NEON_PINK), (80, 300))
        
        pygame.draw.line(Screen, (255,255,255,30), (480, 180), (480, 420), 1)
        Screen.blit(Font.render("АНАЛИЗ ДВИЖЕНИЙ:", True, COLOR_NEON_BLUE), (520, 180))
        Screen.blit(Font.render(f"Длина траектории: {Game['PathLengthPx']:.0f} px", True, COLOR_TEXT), (520, 240))
        Screen.blit(Font.render(f"Средняя скорость: {AvgCursorSpeed:.1f} px/с", True, COLOR_TEXT), (520, 300))
        Screen.blit(FontSmall.render(f"Средняя скорость в движении: {AvgMovingSpeed:.1f} px/с", True, COLOR_TEXT_MUTED), (520, 345))
        Screen.blit(FontSmall.render(f"Расстояние до кратчайшего маршрута: {AvgDev:.1f} px", True, COLOR_TEXT_MUTED), (520, 380))
        Screen.blit(FontSmall.render(f"Подсказки: {Game['HintCount']}   Макс. отклонение: {Game['MaxDeviationPx']:.1f} px", True, COLOR_TEXT_MUTED), (520, 415))
        
        # Перераспределили размеры кнопок в ряд, чтобы гармонично вместить новую кнопку
        btn_rep = pygame.Rect(25, BaseHeight - 100, 190, 46)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_rep, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_GREEN, btn_rep, 1, border_radius=10)
        Screen.blit(Font.render("ПОВТОР", True, COLOR_TEXT), (btn_rep.x + (btn_rep.width - Font.render("ПОВТОР", True, COLOR_TEXT).get_width())//2, BaseHeight - 92))
        
        btn_rev = pygame.Rect(235, BaseHeight - 100, 210, 46)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_rev, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_rev, 1, border_radius=10)
        Screen.blit(Font.render("ПОКАЗАТЬ ПУТЬ", True, COLOR_TEXT), (btn_rev.x + (btn_rev.width - Font.render("ПОКАЗАТЬ ПУТЬ", True, COLOR_TEXT).get_width())//2, BaseHeight - 92))
        
        btn_men = pygame.Rect(465, BaseHeight - 100, 190, 46)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_men, border_radius=10)
        pygame.draw.rect(Screen, (255,255,255,30), btn_men, 1, border_radius=10)
        Screen.blit(Font.render("В НАСТРОЙКИ", True, COLOR_TEXT_MUTED), (btn_men.x + (btn_men.width - Font.render("В НАСТРОЙКИ", True, COLOR_TEXT_MUTED).get_width())//2, BaseHeight - 92))

        # НОВАЯ КНОПКА: Выход в главное меню выбора игр
        btn_main_menu = pygame.Rect(675, BaseHeight - 100, 200, 46)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_main_menu, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_PINK, btn_main_menu, 1, border_radius=10)
        Screen.blit(Font.render("В ГЛАВНОЕ МЕНЮ", True, COLOR_TEXT), (btn_main_menu.x + (btn_main_menu.width - Font.render("В ГЛАВНОЕ МЕНЮ", True, COLOR_TEXT).get_width())//2, BaseHeight - 92))

    elif GameState == "REVIEW":
        pygame.draw.rect(Screen, COLOR_CORRIDOR, (OffsetX, OffsetY, MazeCols * CellSize, MazeRows * CellSize))
        
        if len(SolutionPath) > 1:
            pygame.draw.lines(Screen, (255,255,255,35), False, SolutionPath, 2)
            
        for wall in MazeWalls:
            pygame.draw.rect(Screen, COLOR_WALL, wall)
            pygame.draw.rect(Screen, COLOR_WALL_BORDER, wall, 1) 
            
        pygame.draw.rect(Screen, COLOR_NEON_GREEN, FinishRect, border_radius=6)
        
        if len(Game["Trajectory"]) > 1:
            pygame.draw.lines(Screen, COLOR_NEON_PINK, False, Game["Trajectory"], 3)
            
        btn_back = pygame.Rect(BaseWidth // 2 - 120, BaseHeight - 75, 240, 44)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_back, border_radius=8)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_back, 1, border_radius=8)
        Screen.blit(Font.render("ВЕРНУТЬСЯ НА ЭКРАН", True, COLOR_TEXT), (BaseWidth//2 - 105, BaseHeight - 67))

    ScaledFrame = pygame.transform.smoothscale(Screen, (DisplayWidth, DisplayHeight))
    DisplayScreen.blit(ScaledFrame, (0, 0))
    pygame.display.flip()

    statsNow = time.monotonic()
    if statsNow - lastStatsSaveTime >= 2.0:
        saveStatistics("interrupted", includeActiveRound=True)
        lastStatsSaveTime = statsNow

if GameState == "PLAY" and not Game["RoundCaptured"]:
    sessionAborted = True
    captureMazeRound("interrupted", False)
finalStatus = "completed" if (not sessionAborted and any(item["outcome"] == "completed" for item in roundHistory)) else "interrupted"
saveStatistics(finalStatus)
sessionFinalized = True

if Joy:
    Joy.quit()
pygame.quit()
sys.exit()
