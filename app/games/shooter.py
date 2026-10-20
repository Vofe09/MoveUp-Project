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
from statistics import median
from typing import Optional

import pygame

# =================================================================
#  НАСТРОЙКИ УПРАВЛЕНИЯ И ПРОТОКОЛ MOVEUP
# =================================================================
GAME_TITLE = "Cyber Shooter"
DEFAULT_GAME_ID = "shooter"
DEFAULT_STATS_SCHEMA_VERSION = 1
ROUND_DURATION_SECONDS = 30.0
CURSOR_SMOOTHING_FACTOR = 0.22
MOVEMENT_ACTIVE_THRESHOLD_PX_PER_SECOND = 5.0
STATS_AUTOSAVE_INTERVAL_SECONDS = 2.0

# Лаунчер может передать путь к общей истории. При автономном запуске
# используется statistics.json в общей папке Games (если она найдена).
gameId = os.environ.get("MOVEUP_GAME_ID", DEFAULT_GAME_ID).strip() or DEFAULT_GAME_ID
sessionId = os.environ.get("MOVEUP_SESSION_ID", "").strip() or str(uuid.uuid4())
processStartedAt = datetime.now(timezone.utc)
processStartedMonotonic = time.monotonic()


def resolveStatsFilePath():
    """Resolve the shared history file, independent of how the game was launched."""
    executablePath = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    scriptDirectory = executablePath.parent
    candidateDirectories = [scriptDirectory, *scriptDirectory.parents]

    # Packaged games commonly live in Games/<game>/<game executable>.
    for directory in candidateDirectories:
        if directory.name.casefold() == "games":
            return str(directory / "statistics.json")

    # Source games may sit directly beside a shared Games directory.
    gamesDirectory = next(
        (child for child in scriptDirectory.iterdir()
         if child.is_dir() and child.name.casefold() == "games"),
        None,
    )
    if gamesDirectory is not None:
        return str(gamesDirectory / "statistics.json")

    # Fallback for a standalone copy outside the standard project layout.
    return str(scriptDirectory / "statistics.json")


statsFilePath = resolveStatsFilePath()
# Сохраняем совместимость со старым протоколом: если лаунчер передал отдельный
# файл обмена, записываем туда payload сессии дополнительно к общей истории.
handoffStatsFilePath = os.environ.get("MOVEUP_STATS_FILE", "").strip()
if handoffStatsFilePath:
    handoffStatsFilePath = os.path.abspath(os.path.expanduser(handoffStatsFilePath))
try:
    statsSchemaVersion = int(os.environ.get(
        "MOVEUP_STATS_SCHEMA_VERSION", str(DEFAULT_STATS_SCHEMA_VERSION)
    ))
    if statsSchemaVersion < 1:
        statsSchemaVersion = DEFAULT_STATS_SCHEMA_VERSION
except (TypeError, ValueError):
    statsSchemaVersion = DEFAULT_STATS_SCHEMA_VERSION

# =================================================================
#  БЛОК НАСТРОЙКИ НАПРАВЛЕНИЯ И ЧУВСТВИТЕЛЬНОСТИ ДЖОЙСТИКОВ
# =================================================================
JOY1_SWAP_AXES = False
JOY2_SWAP_AXES = False
JOY1_SIGN_X = 1
JOY1_SIGN_Y = 1
JOY2_SIGN_X = 1
JOY2_SIGN_Y = 1

# Прессеты чувствительности (циклируются через joystick3Button)
SENSITIVITY_PRESETS = {
    "Низкая": 0.007,
    "Средняя": 0.015,
    "Высокая": 0.025,
    "Максимальная": 0.040
}
SENSITIVITY_NAMES = list(SENSITIVITY_PRESETS.keys())
# =================================================================

pygame.init()
pygame.joystick.init()

print("Джойстиков найдено:", pygame.joystick.get_count())

if pygame.joystick.get_count() > 0:
    Joystick = pygame.joystick.Joystick(0)
    Joystick.init()
    print("Используется:", Joystick.get_name())
    print("Оси:", Joystick.get_numaxes())
    print("Кнопки:", Joystick.get_numbuttons())
else:
    print("HID-геймпад не найден")
    Joystick = None

BaseWidth = 900
BaseHeight = 650

DisplayScreen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
DisplayWidth, DisplayHeight = DisplayScreen.get_size()

Screen = pygame.Surface((BaseWidth, BaseHeight), pygame.SRCALPHA)
pygame.display.set_caption("Cyber Shooter - Rehabilitation Edition")
Clock = pygame.time.Clock()

FontSmall = pygame.font.SysFont("Segoe UI", 18)
Font = pygame.font.SysFont("Segoe UI", 26, bold=True)
FontBig = pygame.font.SysFont("Segoe UI", 52, bold=True)

# Неоновая Glassmorphic палитра
COLOR_BG_DARK = (15, 12, 30)
COLOR_BG_LIGHT = (25, 30, 60)
COLOR_GLASS = (25, 25, 50, 180)
COLOR_TEXT = (255, 255, 255)
COLOR_TEXT_DARK = (10, 15, 45)
COLOR_TEXT_MUTED = (160, 170, 210)
COLOR_NEON_BLUE = (0, 240, 255)
COLOR_NEON_PINK = (255, 0, 128)
COLOR_ENEMY = (240, 40, 90)
COLOR_NEON_GREEN = (0, 255, 150)

Game = {
    "State": "PORT_SELECT",
    "Score": 0,
    "StartTime": 0.0,
    "EndTime": 0.0,
    "TotalShots": 0,
    "Hits": 0,
    "Misses": 0,
    "ShotTimes": [],
    "ShotIntervals": [],
    "TargetAcquisitionTimes": [],
    "PathLengthPx": 0.0,
    "SpeedSumPxPerSecond": 0.0,
    "SpeedSquaredSum": 0.0,
    "SpeedSamples": 0,
    "MaxSpeedPxPerSecond": 0.0,
    "MovementActiveSeconds": 0.0,
    "ActiveMovementDistancePx": 0.0,
    "TargetX": BaseWidth // 2,
    "TargetY": BaseHeight // 2,
    "CurX": BaseWidth // 2,
    "CurY": BaseHeight // 2,
    "Enemies": [],
    "Particles": [],
    "RoundCaptured": False,
    "RoundShouldEnd": False
}

# Каждая законченная или прерванная игровая попытка фиксируется один раз.
# Храним агрегаты по кадрам, а не необработанную траекторию из тысяч координат.
roundHistory = []
# Переменные для отслеживания истории нажатия кнопок (защита от зажима)
PrevBtn1 = 0
PrevBtn2 = 0
PrevBtn3 = 0

# Текущий индекс прессета чувствительности
CurrentSensitivityIdx = 1  # По умолчанию "Средняя"
sensitivityPresetsUsed = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}

def getCurrentSensitivity():
    return SENSITIVITY_PRESETS[SENSITIVITY_NAMES[CurrentSensitivityIdx]]

Joy: Optional[pygame.joystick.Joystick] = Joystick
AvailablePorts = []
SelectedPortIndex = 0

def refreshPorts():
    global AvailablePorts, SelectedPortIndex
    AvailablePorts = [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]
    SelectedPortIndex = 0

refreshPorts()

def spawnEnemy():
    return {
        "x": random.randint(60, BaseWidth - 60),
        "y": random.randint(140, BaseHeight - 80),
        "r": random.randint(20, 40),
        "spawnedAt": time.monotonic()
    }

def toLogicalPos(Position):
    return (
        Position[0] * BaseWidth / DisplayWidth,
        Position[1] * BaseHeight / DisplayHeight,
    )

def drawGlowCircle(surf, pos, r, color, glow_r):
    for gr in range(glow_r, 0, -2):
        alpha = int((1.0 - (gr / glow_r)) * 75)
        s = pygame.Surface((r*2 + gr*2, r*2 + gr*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (color[0], color[1], color[2], alpha), (r + gr, r + gr), r + gr)
        surf.blit(s, (pos[0] - r - gr, pos[1] - r - gr))

def drawGlowRect(surface, rect, color, thickness, glow_radius):
    for r in range(glow_radius, 0, -2):
        alpha = int((1.0 - (r / glow_radius)) * 60)
        glow_surf = pygame.Surface((rect.width + r*2, rect.height + r*2), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, (color[0], color[1], color[2], alpha), (0, 0, rect.width + r*2, rect.height + r*2), thickness + r, border_radius=12)
        surface.blit(glow_surf, (rect.x - r, rect.y - r))

def resetGameMetrics():
    global PrevBtn1, PrevBtn2, PrevBtn3
    Game["Score"] = 0
    Game["PathLengthPx"] = 0.0
    Game["SpeedSumPxPerSecond"] = 0.0
    Game["SpeedSquaredSum"] = 0.0
    Game["SpeedSamples"] = 0
    Game["MaxSpeedPxPerSecond"] = 0.0
    Game["MovementActiveSeconds"] = 0.0
    Game["ActiveMovementDistancePx"] = 0.0
    Game["TotalShots"] = 0
    Game["Hits"] = 0
    Game["Misses"] = 0
    Game["ShotTimes"] = []
    Game["ShotIntervals"] = []
    Game["TargetAcquisitionTimes"] = []
    Game["StartTime"] = time.monotonic()
    Game["EndTime"] = 0.0
    Game["RoundCaptured"] = False
    Game["RoundShouldEnd"] = False
    Game["CurX"], Game["CurY"] = BaseWidth // 2, BaseHeight // 2
    Game["TargetX"], Game["TargetY"] = BaseWidth // 2, BaseHeight // 2
    Game["Enemies"] = [spawnEnemy() for _ in range(5)]
    Game["Particles"] = []
    PrevBtn1 = 0
    PrevBtn2 = 0
    PrevBtn3 = 0


def metric(label, value, unit):
    """Return one metric in the launcher-facing label/value/unit format."""
    if isinstance(value, float):
        value = round(value, 3)
    return {"label": label, "value": value, "unit": unit}


def buildRoundSummary(isCompleted, now=None, commit=True):
    """Build a round summary, optionally committing it to the session history."""
    if Game["RoundCaptured"] and commit:
        return None

    capturedAt = time.monotonic() if now is None else now
    endTime = Game["EndTime"] if Game["EndTime"] > 0 else capturedAt
    duration = max(0.0, min(ROUND_DURATION_SECONDS, endTime - Game["StartTime"]))
    shotIntervals = list(Game["ShotIntervals"])
    acquisitionTimes = list(Game["TargetAcquisitionTimes"])

    summary = {
        "completed": bool(isCompleted),
        "durationSeconds": duration,
        "score": int(Game["Score"]),
        "totalShots": int(Game["TotalShots"]),
        "hits": int(Game["Hits"]),
        "misses": int(Game["Misses"]),
        "pathLengthPx": float(Game["PathLengthPx"]),
        "speedSumPxPerSecond": float(Game["SpeedSumPxPerSecond"]),
        "speedSquaredSum": float(Game["SpeedSquaredSum"]),
        "speedSamples": int(Game["SpeedSamples"]),
        "maxSpeedPxPerSecond": float(Game["MaxSpeedPxPerSecond"]),
        "movementActiveSeconds": float(Game["MovementActiveSeconds"]),
        "activeMovementDistancePx": float(Game["ActiveMovementDistancePx"]),
        "shotIntervals": shotIntervals,
        "targetAcquisitionTimes": acquisitionTimes,
        "sensitivityPreset": SENSITIVITY_NAMES[CurrentSensitivityIdx],
    }
    if commit:
        roundHistory.append(summary)
        Game["RoundCaptured"] = True
    return summary


def buildSessionResult(sessionStatus, includeCurrentRound=False):
    """Aggregate attempts in this launcher process into one session payload."""
    roundsForResult = list(roundHistory)
    if includeCurrentRound and Game["State"] == "PLAY" and not Game["RoundCaptured"]:
        currentRound = buildRoundSummary(False, commit=False)
        if currentRound is not None:
            roundsForResult.append(currentRound)

    roundsPlayed = len(roundsForResult)
    completedRounds = sum(1 for roundSummary in roundsForResult if roundSummary["completed"])
    totalDuration = sum(roundSummary["durationSeconds"] for roundSummary in roundsForResult)
    totalShots = sum(roundSummary["totalShots"] for roundSummary in roundsForResult)
    totalHits = sum(roundSummary["hits"] for roundSummary in roundsForResult)
    totalMisses = sum(roundSummary["misses"] for roundSummary in roundsForResult)
    totalScore = sum(roundSummary["score"] for roundSummary in roundsForResult)
    totalPathLength = sum(roundSummary["pathLengthPx"] for roundSummary in roundsForResult)
    speedSampleCount = sum(roundSummary["speedSamples"] for roundSummary in roundsForResult)
    speedSum = sum(roundSummary["speedSumPxPerSecond"] for roundSummary in roundsForResult)
    speedSquaredSum = sum(roundSummary["speedSquaredSum"] for roundSummary in roundsForResult)
    maxSpeed = max(
        (roundSummary["maxSpeedPxPerSecond"] for roundSummary in roundsForResult),
        default=0.0,
    )
    allShotIntervals = [
        interval
        for roundSummary in roundsForResult
        for interval in roundSummary["shotIntervals"]
    ]
    allAcquisitionTimes = [
        acquisitionTime
        for roundSummary in roundsForResult
        for acquisitionTime in roundSummary["targetAcquisitionTimes"]
    ]
    averageSpeed = totalPathLength / totalDuration if totalDuration > 0 else 0.0
    meanSampleSpeed = speedSum / speedSampleCount if speedSampleCount else 0.0
    speedVariance = (
        max(0.0, speedSquaredSum / speedSampleCount - meanSampleSpeed ** 2)
        if speedSampleCount else 0.0
    )
    speedStandardDeviation = math.sqrt(speedVariance)
    accuracy = (totalHits / totalShots * 100.0) if totalShots else None
    hitRatePerMinute = (totalHits / totalDuration * 60.0) if totalDuration > 0 else 0.0
    averageShotInterval = (
        sum(allShotIntervals) / len(allShotIntervals) if allShotIntervals else None
    )
    averageAcquisitionTime = (
        sum(allAcquisitionTimes) / len(allAcquisitionTimes)
        if allAcquisitionTimes else None
    )
    medianAcquisitionTime = median(allAcquisitionTimes) if allAcquisitionTimes else None
    totalActiveMovementSeconds = sum(
        roundSummary["movementActiveSeconds"] for roundSummary in roundsForResult
    )
    totalActiveMovementDistance = sum(
        roundSummary["activeMovementDistancePx"] for roundSummary in roundsForResult
    )
    averageActiveMovementSpeed = (
        totalActiveMovementDistance / totalActiveMovementSeconds
        if totalActiveMovementSeconds > 0 else 0.0
    )

    metrics = {
        "roundsPlayed": metric("Игровые раунды", roundsPlayed, "шт."),
        "roundsCompleted": metric("Завершённые раунды", completedRounds, "шт."),
        "score": metric("Суммарный счёт", totalScore, "очков"),
        "totalShots": metric("Всего выстрелов", totalShots, "шт."),
        "hits": metric("Попадания", totalHits, "шт."),
        "misses": metric("Промахи", totalMisses, "шт."),
        "hitRatePerMinute": metric("Попадания в минуту", hitRatePerMinute, "попаданий/мин"),
        "activeGameplaySeconds": metric("Время активной игры", totalDuration, "сек."),
        "cursorPathLengthPx": metric("Длина траектории курсора", totalPathLength, "px"),
        "averageCursorSpeedPxPerSecond": metric(
            "Средняя скорость курсора за время игры", averageSpeed, "px/сек"
        ),
        "averageActiveMovementSpeedPxPerSecond": metric(
            "Средняя скорость во время движения", averageActiveMovementSpeed, "px/сек"
        ),
        "maxCursorSpeedPxPerSecond": metric("Максимальная скорость курсора", maxSpeed, "px/сек"),
        "cursorSpeedStandardDeviationPxPerSecond": metric(
            "Стандартное отклонение скорости курсора", speedStandardDeviation, "px/сек"
        ),
        "sensitivityPresetsUsedCount": metric(
            "Количество использованных режимов чувствительности", len(sensitivityPresetsUsed), "режимов"
        ),
    }
    if accuracy is not None:
        metrics["accuracyPct"] = metric("Точность попаданий", accuracy, "%")
    if averageShotInterval is not None:
        metrics["averageShotIntervalSeconds"] = metric(
            "Средний интервал между выстрелами", averageShotInterval, "сек."
        )
    if averageAcquisitionTime is not None:
        metrics["averageTargetAcquisitionTimeSeconds"] = metric(
            "Среднее время от появления цели до попадания", averageAcquisitionTime, "сек."
        )
        metrics["medianTargetAcquisitionTimeSeconds"] = metric(
            "Медианное время от появления цели до попадания", medianAcquisitionTime, "сек."
        )

    currentUtcTime = datetime.now(timezone.utc)
    processDurationSeconds = max(0.0, time.monotonic() - processStartedMonotonic)
    return {
        "schema_version": statsSchemaVersion,
        "metrics_version": 1,
        "session_id": sessionId,
        "game_id": gameId,
        "game_title": GAME_TITLE,
        "started_at": processStartedAt.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ended_at": currentUtcTime.isoformat(timespec="seconds").replace("+00:00", "Z"),
        # В общей истории это длительность процесса. При обработке результата
        # лаунчер должен заменить её своей длительностью жизни процесса.
        "duration_seconds": round(processDurationSeconds, 3),
        "status": sessionStatus,
        "metrics": metrics,
        # Контекст нужен для интерпретации кинематических метрик; это не медицинский диагноз.
        "settings": {
            "cursor_smoothing_factor": CURSOR_SMOOTHING_FACTOR,
            "logical_resolution_px": {"width": BaseWidth, "height": BaseHeight},
            "sensitivity_presets_used": sorted(sensitivityPresetsUsed),
            "movement_active_threshold_px_per_second": MOVEMENT_ACTIVE_THRESHOLD_PX_PER_SECOND,
        },
    }


def saveStatsFile(sessionStatus, includeCurrentRound=False):
    """Append/update this session in the shared statistics.json history atomically."""
    statsDirectory = os.path.dirname(os.path.abspath(statsFilePath))
    temporaryPath = None
    try:
        os.makedirs(statsDirectory, exist_ok=True)
        result = buildSessionResult(sessionStatus, includeCurrentRound)

        if os.path.exists(statsFilePath):
            try:
                with open(statsFilePath, "r", encoding="utf-8") as existingFile:
                    history = json.load(existingFile)
            except json.JSONDecodeError as error:
                # Не затираем пользовательскую историю, если файл повреждён.
                raise ValueError(
                    f"файл {statsFilePath} содержит некорректный JSON; история не перезаписана"
                ) from error

            if isinstance(history, dict) and isinstance(history.get("sessions"), list):
                sessions = history["sessions"]
            elif isinstance(history, dict) and history.get("session_id"):
                # Миграция старого формата, где файл содержал одну сессию.
                sessions = [history]
                history = {"schema_version": statsSchemaVersion, "sessions": sessions}
            else:
                raise ValueError(
                    f"неподдерживаемая структура истории в {statsFilePath}; история не перезаписана"
                )
        else:
            history = {"schema_version": statsSchemaVersion, "sessions": []}
            sessions = history["sessions"]

        # Автосохранения обновляют ту же сессию, а не создают дубликаты.
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
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=statsDirectory,
            prefix=".moveup-stats-",
            suffix=".tmp",
            delete=False,
        ) as statsFile:
            json.dump(history, statsFile, ensure_ascii=False, indent=2, allow_nan=False)
            statsFile.write("\n")
            temporaryPath = statsFile.name
        os.replace(temporaryPath, statsFilePath)
        print(f"Статистика сохранена: {statsFilePath}")

        # Если лаунчер ожидает отдельный hand-off файл, сохраняем в него
        # прежний формат одной сессии, не меняя общую историю для всех игр.
        if handoffStatsFilePath and os.path.abspath(handoffStatsFilePath) != os.path.abspath(statsFilePath):
            handoffDirectory = os.path.dirname(handoffStatsFilePath)
            handoffTemporaryPath = None
            try:
                os.makedirs(handoffDirectory, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=handoffDirectory,
                    prefix=".moveup-session-",
                    suffix=".tmp",
                    delete=False,
                ) as handoffFile:
                    json.dump(result, handoffFile, ensure_ascii=False, indent=2, allow_nan=False)
                    handoffFile.write("\n")
                    handoffTemporaryPath = handoffFile.name
                os.replace(handoffTemporaryPath, handoffStatsFilePath)
            except (OSError, TypeError, ValueError) as handoffError:
                print(f"Не удалось сохранить файл передачи лаунчеру: {handoffError}")
                if handoffTemporaryPath:
                    try:
                        os.remove(handoffTemporaryPath)
                    except OSError:
                        pass
    except (OSError, TypeError, ValueError) as error:
        print(f"Не удалось сохранить статистику MoveUp: {error}")
        if temporaryPath:
            try:
                os.remove(temporaryPath)
            except OSError:
                pass


def finalizeRound(isCompleted):
    """Capture round metrics and refresh the launcher hand-off file."""
    if buildRoundSummary(isCompleted) is not None:
        saveStatsFile("completed" if isCompleted else "interrupted")

Running = True
lastStatsSaveTime = 0.0
while Running:
    frameDelta = max(0.001, Clock.tick(60) / 1000.0)

    pygame.event.pump()

    
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


            # Чтение кнопок HID
            btnLeft = Joy.get_button(1)    # Button 2 — левый клик
            btnRight = Joy.get_button(0)   # Button 1 — правый клик
            btnCenter = Joy.get_button(3)  # Button 4 — GP8, переключение чувствительности

            # Переключение чувствительности по новому нажатию GP8
            if btnCenter == 1 and PrevBtn3 == 0:
                CurrentSensitivityIdx = (
                    CurrentSensitivityIdx + 1
                ) % len(SENSITIVITY_NAMES)
                sensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])

                print(
                    f"\nЧувствительность: "
                    f"{SENSITIVITY_NAMES[CurrentSensitivityIdx]}"
                )

            if Game["State"] == "PLAY":
                current_speed = getCurrentSensitivity()

                # ===== ТВОЯ ФИНАЛЬНАЯ РАСКЛАДКА =====
                leftVertical = Joy.get_axis(3)      # A3
                leftHorizontal = Joy.get_axis(2)    # A2

                rightVertical = Joy.get_axis(1)     # A1
                rightHorizontal = Joy.get_axis(0)   # A0

                # Левый стик
                if abs(leftHorizontal) > 0.05:
                    Game["TargetX"] -= leftHorizontal * 512 * current_speed * frameDelta * 60.0

                if abs(leftVertical) > 0.05:
                    Game["TargetY"] -= leftVertical * 512 * current_speed * frameDelta * 60.0

                # Правый стик
                if abs(rightHorizontal) > 0.05:
                    Game["TargetX"] -= rightHorizontal * 512 * current_speed * frameDelta * 60.0

                if abs(rightVertical) > 0.05:
                    Game["TargetY"] -= rightVertical * 512 * current_speed * frameDelta * 60.0
                Game["TargetX"] = max(20, min(BaseWidth - 20, Game["TargetX"]))
                Game["TargetY"] = max(100, min(BaseHeight - 20, Game["TargetY"]))

            if (btnLeft == 1 and PrevBtn1 == 0) or (btnRight == 1 and PrevBtn2 == 0):
                ArduinoClicked = True

            PrevBtn1 = btnLeft
            PrevBtn2 = btnRight
            PrevBtn3 = btnCenter

        except Exception as e:
            print("JOY ERROR:", e)
            raise

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            Running = False
        
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                Running = False
                
            if Game["State"] == "PORT_SELECT":
                if event.key == pygame.K_UP:
                    SelectedPortIndex = (SelectedPortIndex - 1) % max(1, len(AvailablePorts))
                elif event.key == pygame.K_DOWN:
                    SelectedPortIndex = (SelectedPortIndex + 1) % max(1, len(AvailablePorts))
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if AvailablePorts:
                        try:
                            Joy = pygame.joystick.Joystick(SelectedPortIndex)
                            Joy.init()
                            time.sleep(0.2)
                        except: pass
                    resetGameMetrics()
                    Game["State"] = "PLAY"
                elif event.key == pygame.K_F5:
                    refreshPorts()
                    
            elif Game["State"] == "END":
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    resetGameMetrics()
                    Game["State"] = "PLAY"

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            LogicalPos = toLogicalPos(event.pos)
            
            if Game["State"] == "PORT_SELECT":
                if pygame.Rect(BaseWidth // 2 - 140, BaseHeight - 140, 280, 46).collidepoint(LogicalPos):
                    refreshPorts()
                for idx, _ in enumerate(AvailablePorts):
                    if pygame.Rect(BaseWidth // 2 - 220, 160 + idx * 52, 440, 44).collidepoint(LogicalPos):
                        SelectedPortIndex = idx
                        if AvailablePorts:
                            try:
                                Joy = pygame.joystick.Joystick(SelectedPortIndex)
                                Joy.init()
                            except: pass
                        resetGameMetrics()
                        Game["State"] = "PLAY"
                        
            elif Game["State"] == "PLAY":
                ArduinoClicked = True
                
            elif Game["State"] == "END":
                if pygame.Rect(35, BaseHeight - 110, 250, 46).collidepoint(LogicalPos):
                    resetGameMetrics()
                    Game["State"] = "PLAY"
                elif pygame.Rect(325, BaseHeight - 110, 250, 46).collidepoint(LogicalPos):
                    Game["State"] = "PORT_SELECT"
                    refreshPorts()
                elif pygame.Rect(615, BaseHeight - 110, 250, 46).collidepoint(LogicalPos):
                    Running = False

    # --- ОТРИСОВКА ---

    if Game["State"] == "PORT_SELECT":
        Title = FontBig.render("НАСТРОЙКА КАНАЛА СВЯЗИ", True, COLOR_TEXT)
        Screen.blit(Title, ((BaseWidth - Title.get_width()) // 2, 50))
        
        PanelRect = pygame.Rect(BaseWidth // 2 - 250, 130, 500, 340)
        pygame.draw.rect(Screen, COLOR_GLASS, PanelRect, border_radius=16)
        pygame.draw.rect(Screen, (255,255,255,30), PanelRect, 1, border_radius=16)
        
        if not AvailablePorts:
            NoPortTxt = Font.render("Порты не найдены. Проверьте Arduino.", True, COLOR_NEON_PINK)
            Screen.blit(NoPortTxt, ((BaseWidth - NoPortTxt.get_width()) // 2, 240))
        else:
            for idx, port in enumerate(AvailablePorts):
                item_rect = pygame.Rect(BaseWidth // 2 - 220, 160 + idx * 52, 440, 44)
                if idx == SelectedPortIndex:
                    drawGlowRect(Screen, item_rect, COLOR_NEON_BLUE, 2, 10)
                    pygame.draw.rect(Screen, COLOR_NEON_BLUE, item_rect, border_radius=10)
                    txt_col = COLOR_TEXT_DARK
                else:
                    pygame.draw.rect(Screen, (255,255,255,10), item_rect, border_radius=10)
                    txt_col = COLOR_TEXT
                
                p_surf = Font.render(f"Синхронизация через {port}", True, txt_col)
                Screen.blit(p_surf, (item_rect.x + 20, item_rect.y + (item_rect.height - p_surf.get_height())//2))

        btn_refresh = pygame.Rect(BaseWidth // 2 - 140, BaseHeight - 140, 280, 46)
        pygame.draw.rect(Screen, COLOR_GLASS, btn_refresh, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_refresh, 1, border_radius=10)
        r_txt = Font.render("Обновить порты [F5]", True, COLOR_TEXT)
        Screen.blit(r_txt, (btn_refresh.x + (btn_refresh.width - r_txt.get_width())//2, btn_refresh.y + (btn_refresh.height - r_txt.get_height())//2))

        Hint = FontSmall.render("Выберите устройство управления и нажмите Enter", True, COLOR_NEON_BLUE)
        Screen.blit(Hint, ((BaseWidth - Hint.get_width()) // 2, BaseHeight - 60))

    elif Game["State"] == "PLAY":
        old_cx, old_cy = Game["CurX"], Game["CurY"]
        dx = Game["TargetX"] - Game["CurX"]
        dy = Game["TargetY"] - Game["CurY"]

        frameSmoothingFactor = 1.0 - (1.0 - CURSOR_SMOOTHING_FACTOR) ** (frameDelta * 60.0)
        Game["CurX"] += dx * frameSmoothingFactor
        Game["CurY"] += dy * frameSmoothingFactor

        # Кинематика относится к отображаемому курсору после сглаживания,
        # а не к прямому измерению физического движения руки.
        deltaX = Game["CurX"] - old_cx
        deltaY = Game["CurY"] - old_cy
        frameDistancePx = math.hypot(deltaX, deltaY)
        cursorSpeedPxPerSecond = frameDistancePx / frameDelta
        Game["PathLengthPx"] += frameDistancePx
        Game["SpeedSumPxPerSecond"] += cursorSpeedPxPerSecond
        Game["SpeedSquaredSum"] += cursorSpeedPxPerSecond ** 2
        Game["SpeedSamples"] += 1
        Game["MaxSpeedPxPerSecond"] = max(
            Game["MaxSpeedPxPerSecond"], cursorSpeedPxPerSecond
        )
        if cursorSpeedPxPerSecond > MOVEMENT_ACTIVE_THRESHOLD_PX_PER_SECOND:
            Game["MovementActiveSeconds"] += frameDelta
            Game["ActiveMovementDistancePx"] += frameDistancePx

        elapsedSeconds = max(0.0, time.monotonic() - Game["StartTime"])
        TimeLeft = ROUND_DURATION_SECONDS - elapsedSeconds
        if TimeLeft <= 0:
            Game["EndTime"] = time.monotonic()
            Game["RoundShouldEnd"] = True

        pygame.draw.rect(Screen, COLOR_GLASS, (0, 0, BaseWidth, 85))
        pygame.draw.line(Screen, COLOR_NEON_BLUE, (0, 85), (BaseWidth, 85), 2)
        
        Screen.blit(Font.render(f"Счет: {Game['Score']}", True, COLOR_TEXT), (30, 26))
        
        timer_color = COLOR_TEXT if TimeLeft > 5 else COLOR_NEON_PINK
        Screen.blit(Font.render(f"Время: {max(0.0, TimeLeft):.1f} сек", True, timer_color), (BaseWidth // 2 - 90, 26))
        
        speedColor = COLOR_NEON_BLUE
        Screen.blit(
            Font.render(f"Скорость курсора: {cursorSpeedPxPerSecond:.0f} px/с", True, speedColor),
            (BaseWidth - 310, 26),
        )
        
        # Отображение текущей чувствительности
        current_sens_name = SENSITIVITY_NAMES[CurrentSensitivityIdx]
        sens_col = COLOR_NEON_GREEN if current_sens_name in ["Средняя"] else (COLOR_NEON_BLUE if current_sens_name == "Низкая" else COLOR_NEON_PINK)
        Screen.blit(FontSmall.render(f"Чувствительность: {current_sens_name}", True, sens_col), (30, 55))

        for e in Game["Enemies"]:
            drawGlowCircle(Screen, (e["x"], e["y"]), e["r"], COLOR_ENEMY, 14)
            pygame.draw.circle(Screen, COLOR_TEXT, (e["x"], e["y"]), e["r"], 2)
            pygame.draw.circle(Screen, COLOR_ENEMY, (e["x"], e["y"]), 7)

        if ArduinoClicked and not Game["RoundShouldEnd"]:
            shotElapsed = max(0.0, time.monotonic() - Game["StartTime"])
            Game["TotalShots"] += 1
            if Game["ShotTimes"]:
                Game["ShotIntervals"].append(shotElapsed - Game["ShotTimes"][-1])
            Game["ShotTimes"].append(shotElapsed)
            hit_detected = False
            for e in Game["Enemies"][:]:
                dist = math.sqrt((Game["CurX"] - e["x"])**2 + (Game["CurY"] - e["y"])**2)
                if dist <= e["r"]:
                    Game["Enemies"].remove(e)
                    Game["TargetAcquisitionTimes"].append(
                        max(0.0, time.monotonic() - e.get("spawnedAt", time.monotonic()))
                    )
                    Game["Score"] += 15
                    Game["Hits"] += 1
                    hit_detected = True
                    for _ in range(15):
                        Game["Particles"].append({
                            "x": e["x"], "y": e["y"],
                            "vx": random.uniform(-5, 5), "vy": random.uniform(-5, 5),
                            "life": 20
                        })
                    Game["Enemies"].append(spawnEnemy())
                    break
            if not hit_detected:
                Game["Score"] = max(0, Game["Score"] - 4)
                Game["Misses"] += 1

        if Game["RoundShouldEnd"]:
            Game["State"] = "END"
            finalizeRound(True)

        for p in Game["Particles"][:]:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["life"] -= 1
            if p["life"] <= 0: Game["Particles"].remove(p)
            else: pygame.draw.circle(Screen, COLOR_NEON_PINK, (int(p["x"]), int(p["y"])), 3)

        cx, cy = int(Game["CurX"]), int(Game["CurY"])
        drawGlowCircle(Screen, (cx, cy), 16, COLOR_NEON_BLUE, 18)
        pygame.draw.line(Screen, COLOR_NEON_BLUE, (cx - 26, cy), (cx + 26, cy), 2)
        pygame.draw.line(Screen, COLOR_NEON_BLUE, (cx, cy - 26), (cx, cy + 26), 2)
        pygame.draw.circle(Screen, COLOR_TEXT, (cx, cy), 3)

    elif Game["State"] == "END":
        PanelRect = pygame.Rect(40, 40, 820, 560)
        drawGlowRect(Screen, PanelRect, COLOR_NEON_PINK, 2, 16)
        pygame.draw.rect(Screen, COLOR_GLASS, PanelRect, border_radius=24)
        pygame.draw.rect(Screen, (255,255,255,20), PanelRect, 1, border_radius=24)

        Title = FontBig.render("СЕССИЯ ЗАВЕРШЕНА", True, COLOR_NEON_PINK)
        Screen.blit(Title, ((BaseWidth - Title.get_width()) // 2, 70))

        Duration = max(0.0, Game["EndTime"] - Game["StartTime"])
        Accuracy = (Game["Hits"] / Game["TotalShots"] * 100.0) if Game["TotalShots"] else None
        AverageShotInterval = (
            sum(Game["ShotIntervals"]) / len(Game["ShotIntervals"])
            if Game["ShotIntervals"] else None
        )
        AverageAcquisitionTime = (
            sum(Game["TargetAcquisitionTimes"]) / len(Game["TargetAcquisitionTimes"])
            if Game["TargetAcquisitionTimes"] else None
        )
        AverageCursorSpeed = Game["PathLengthPx"] / Duration if Duration > 0 else 0.0
        MeanSampleSpeed = (
            Game["SpeedSumPxPerSecond"] / Game["SpeedSamples"]
            if Game["SpeedSamples"] else 0.0
        )
        SpeedVariance = (
            max(0.0, Game["SpeedSquaredSum"] / Game["SpeedSamples"] - MeanSampleSpeed ** 2)
            if Game["SpeedSamples"] else 0.0
        )
        SpeedStandardDeviation = math.sqrt(SpeedVariance)

        Screen.blit(Font.render(f"Итоговый счёт: {Game['Score']}", True, COLOR_TEXT), (70, 175))
        Screen.blit(FontSmall.render(f"Длительность раунда: {Duration:.1f} сек", True, COLOR_TEXT), (70, 222))
        accuracyLabel = f"Точность попаданий: {Accuracy:.1f}%" if Accuracy is not None else "Точность попаданий: —"
        Screen.blit(FontSmall.render(accuracyLabel, True, (100, 255, 150)), (70, 260))
        Screen.blit(FontSmall.render(f"Попадания / выстрелы: {Game['Hits']} / {Game['TotalShots']}", True, COLOR_TEXT), (70, 298))
        Screen.blit(FontSmall.render(f"Промахи: {Game['Misses']}", True, COLOR_TEXT), (70, 336))
        shotIntervalLabel = (
            f"Средний интервал выстрелов: {AverageShotInterval:.2f} сек"
            if AverageShotInterval is not None else "Средний интервал выстрелов: —"
        )
        Screen.blit(FontSmall.render(shotIntervalLabel, True, COLOR_TEXT), (70, 374))
        acquisitionLabel = (
            f"От появления цели до попадания: {AverageAcquisitionTime:.2f} сек"
            if AverageAcquisitionTime is not None else "От появления цели до попадания: —"
        )
        Screen.blit(FontSmall.render(acquisitionLabel, True, COLOR_TEXT), (70, 412))

        pygame.draw.line(Screen, (255,255,255,40), (450, 170), (450, 455), 1)
        Screen.blit(Font.render("ДВИЖЕНИЕ КУРСОРА", True, COLOR_NEON_BLUE), (475, 175))
        Screen.blit(FontSmall.render(f"Длина траектории: {Game['PathLengthPx']:.0f} px", True, COLOR_TEXT), (475, 222))
        Screen.blit(FontSmall.render(f"Средняя скорость: {AverageCursorSpeed:.0f} px/с", True, COLOR_TEXT), (475, 260))
        Screen.blit(FontSmall.render(f"Макс. скорость: {Game['MaxSpeedPxPerSecond']:.0f} px/с", True, COLOR_TEXT), (475, 298))
        Screen.blit(FontSmall.render(f"Разброс скорости: {SpeedStandardDeviation:.0f} px/с", True, COLOR_TEXT), (475, 336))
        Screen.blit(FontSmall.render(f"Время движения: {Game['MovementActiveSeconds']:.1f} сек", True, COLOR_TEXT), (475, 374))
        Screen.blit(FontSmall.render(f"Чувствительность: {SENSITIVITY_NAMES[CurrentSensitivityIdx]}", True, COLOR_TEXT), (475, 412))

        btn_restart = pygame.Rect(35, BaseHeight - 110, 250, 46)
        pygame.draw.rect(Screen, (40, 200, 100, 50), btn_restart, border_radius=10)
        pygame.draw.rect(Screen, (100, 255, 150), btn_restart, 2, border_radius=10)
        lbl1 = Font.render("ПОВТОРИТЬ", True, COLOR_TEXT)
        Screen.blit(lbl1, (btn_restart.x + (btn_restart.width - lbl1.get_width())//2, btn_restart.y + (btn_restart.height - lbl1.get_height())//2))

        btn_ports = pygame.Rect(325, BaseHeight - 110, 250, 46)
        pygame.draw.rect(Screen, (30, 80, 180, 50), btn_ports, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_BLUE, btn_ports, 2, border_radius=10)
        lbl2 = Font.render("ИЗМЕНИТЬ ПОРТ", True, COLOR_TEXT)
        Screen.blit(lbl2, (btn_ports.x + (btn_ports.width - lbl2.get_width())//2, btn_ports.y + (btn_ports.height - lbl2.get_height())//2))

        btn_menu = pygame.Rect(615, BaseHeight - 110, 250, 46)
        pygame.draw.rect(Screen, (200, 40, 100, 50), btn_menu, border_radius=10)
        pygame.draw.rect(Screen, COLOR_NEON_PINK, btn_menu, 2, border_radius=10)
        lbl3 = Font.render("В ГЛАВНОЕ МЕНЮ", True, COLOR_TEXT)
        Screen.blit(lbl3, (btn_menu.x + (btn_menu.width - lbl3.get_width())//2, btn_menu.y + (btn_menu.height - lbl3.get_height())//2))

    ScaledFrame = pygame.transform.smoothscale(Screen, (DisplayWidth, DisplayHeight))
    DisplayScreen.blit(ScaledFrame, (0, 0))
    pygame.display.flip()

    # Периодически сохраняем снимок текущего раунда, чтобы при аварийном
    # завершении лаунчер получил хотя бы частичную статистику.
    currentTime = time.monotonic()
    if (
        Game["State"] == "PLAY"
        and currentTime - lastStatsSaveTime >= STATS_AUTOSAVE_INTERVAL_SECONDS
    ):
        saveStatsFile("interrupted", includeCurrentRound=True)
        lastStatsSaveTime = currentTime

# Сохраняем итог при обычном выходе. Если пользователь вышел посреди раунда,
# записываем частичные данные и помечаем сессию как прерванную.
if Game["State"] == "PLAY" and not Game["RoundCaptured"]:
    Game["EndTime"] = time.monotonic()
    finalizeRound(False)

finalStatus = "completed" if any(r["completed"] for r in roundHistory) and Game["State"] != "PLAY" else "interrupted"
saveStatsFile(finalStatus)

if Joy:
    Joy.quit()
pygame.quit()
sys.exit()
