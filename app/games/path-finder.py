import math
import sys
from pathlib import Path
import random
import time
from typing import List, Optional, Tuple

import pygame
from pygame.math import Vector2

_scriptDirectory = Path(__file__).resolve().parent
for _candidateDirectory in (_scriptDirectory, *_scriptDirectory.parents):
    if (_candidateDirectory / "moveup_stats.py").exists():
        sys.path.insert(0, str(_candidateDirectory))
        break
from moveup_stats import MoveUpStatistics, metric

statistics = MoveUpStatistics("Path finder", "path-finder", sys.executable if getattr(sys, "frozen", False) else __file__)
sessionFinalized = False

# --- НАСТРОЙКИ СИСТЕМЫ И ИЗНАЧАЛЬНАЯ ОРИЕНТАЦИЯ ИЗ ТЗ ---
JoySignX = 1.0
JoySignY = 1.0
SwapJoyAxes = False
SerialBaudRate = 115200

# Прессеты чувствительности (циклируются через joystick3Button)
SENSITIVITY_PRESETS = {
    "Низкая": 0.004,
    "Средняя": 0.009,
    "Высокая": 0.015,
    "Максимальная": 0.025
}
SENSITIVITY_NAMES = list(SENSITIVITY_PRESETS.keys())
CurrentSensitivityIdx = 1  # По умолчанию "Средняя"

def GetCurrentSensitivity():
    return SENSITIVITY_PRESETS[SENSITIVITY_NAMES[CurrentSensitivityIdx]]

# Тайминги подсказок
RevealSeconds = 5.0
HintSeconds = 2.0

# Размеры сетки
GridSize = 10
CellSize = 62
TopBarHeight = 110
BaseWidth = GridSize * CellSize
BaseHeight = TopBarHeight + GridSize * CellSize

pygame.init()
pygame.joystick.init()
pygame.font.init()

DisplayScreen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
DisplayWidth, DisplayHeight = DisplayScreen.get_size()
Screen = pygame.Surface((BaseWidth, BaseHeight), pygame.SRCALPHA)
pygame.display.set_caption("Cyber Minefield - Rehabilitation")
Clock = pygame.time.Clock()

# Адаптированные шрифты для исключения вылезания текста
FontSmall = pygame.font.SysFont("Segoe UI", 15)
Font = pygame.font.SysFont("Segoe UI", 18, bold=True)
FontBig = pygame.font.SysFont("Segoe UI", 30, bold=True)

# Неоновая Glassmorphic Палитра
COLOR_BG_DARK = (15, 12, 30)
COLOR_BG_LIGHT = (25, 30, 60)
COLOR_GLASS = (25, 25, 50, 220)
COLOR_TEXT = (235, 235, 235)
COLOR_TEXT_DARK = (10, 15, 45)
COLOR_TEXT_MUTED = (160, 160, 180)

COLOR_GRID_LINE = (45, 40, 80)
COLOR_START = (0, 180, 255)       
COLOR_FINISH = (255, 180, 40)     
COLOR_PATH = (0, 255, 150, 140)   
COLOR_PLAYER = (240, 220, 60)     
COLOR_EXPLOSION = (255, 0, 128)   
COLOR_NEON_BLUE = (0, 240, 255)
COLOR_NEON_PINK = (255, 0, 128)
COLOR_NEON_GREEN = (0, 255, 150)

class SerialConnection:
    def __init__(self) -> None:
        self.Device: Optional[pygame.joystick.Joystick] = None
        self.DeviceIndex: Optional[int] = None

    def _ResolveIndex(self, PortName: str) -> Optional[int]:
        RawName = str(PortName).strip()
        if ":" in RawName:
            Prefix = RawName.split(":", 1)[0].strip()
            if Prefix.isdigit():
                return int(Prefix)
        if RawName.isdigit():
            return int(RawName)

        for Idx in range(pygame.joystick.get_count()):
            try:
                if pygame.joystick.Joystick(Idx).get_name() == RawName:
                    return Idx
            except Exception:
                pass
        return None

    def Connect(self, PortName: str) -> bool:
        try:
            if self.Device:
                self.Close()

            if not pygame.joystick.get_init():
                pygame.joystick.init()

            Index = self._ResolveIndex(PortName)
            if Index is None or Index < 0 or Index >= pygame.joystick.get_count():
                return False

            self.Device = pygame.joystick.Joystick(Index)
            self.Device.init()
            self.DeviceIndex = Index
            return True
        except Exception:
            self.Device = None
            self.DeviceIndex = None
            return False

    def Close(self) -> None:
        if self.Device:
            try:
                self.Device.quit()
            except Exception:
                pass
            self.Device = None
            self.DeviceIndex = None

    def ReadData(self) -> Tuple[float, float, List[int]]:
        """Чтение актуальных данных с HID-джойстика."""
        if not self.Device:
            return 0.0, 0.0, []

        try:
            pygame.event.pump()

            # Левый стик: Axis 2 / Axis 3
            LeftHorizontal = self.Device.get_axis(2)
            LeftVertical = self.Device.get_axis(3)

            # Правый стик: Axis 0 / Axis 1
            RightHorizontal = self.Device.get_axis(0)
            RightVertical = self.Device.get_axis(1)

            current_speed = GetCurrentSensitivity()
            scale = 512.0

            # Инвертируем обе оси: лево = влево, вверх = вверх
            Dx = -(LeftHorizontal + RightHorizontal) * scale * current_speed
            Dy = -(LeftVertical + RightVertical) * scale * current_speed

            # Общая мёртвая зона для обоих стиков
            if (
                abs(LeftHorizontal) < 0.05 and
                abs(LeftVertical) < 0.05 and
                abs(RightHorizontal) < 0.05 and
                abs(RightVertical) < 0.05
            ):
                Dx, Dy = 0.0, 0.0

            # Читаем все кнопки HID, включая GP8 = HID Button 4 (индекс 3).
            ButtonValues = [
                self.Device.get_button(index)
                for index in range(self.Device.get_numbuttons())
            ]

            return Dx, Dy, ButtonValues

        except Exception:
            return 0.0, 0.0, []

class MinefieldGame:
    def __init__(self) -> None:
        self.Screen = Screen
        self.SerialDevice = SerialConnection()
        self.State = "PORT_SELECT"  
        
        self.AvailablePorts = [f"{Idx}: {pygame.joystick.Joystick(Idx).get_name()}" for Idx in range(pygame.joystick.get_count())]
        self.SelectedPortIdx = 0
        self.GameIsWon = False  
        
        self.GridPath: List[Tuple[int, int]] = []
        self.PathGridSet = set()
        self.PlayerPos = Vector2(0.0, 0.0)
        self.PlayerRadius = 10.0
        
        self.StartTime = 0.0
        self.EndTime = 0.0
        self.TotalHintsTaken = 0
        self.CollisionCount = 0
        
        self.PathLengthPx = 0.0
        self.SpeedSumPxPerSecond = 0.0
        self.SpeedSquaredSum = 0.0
        self.SpeedSamples = 0
        self.CurrentSpeedPxPerSecond = 0.0
        self.MaxSpeedPxPerSecond = 0.0
        self.MovementActiveSeconds = 0.0
        self.ActiveMovementDistancePx = 0.0
        self.DeviationSumPx = 0.0
        self.DeviationSamples = 0
        self.CurrentPathDeviationPx = 0.0
        self.MaxPathDeviationPx = 0.0
        self.FirstMovementLatencySeconds: Optional[float] = None
        self.RoundId = ""
        self.RoundCaptured = False
        self.RoundSensitivityPresetsUsed = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}
        self.RoundHistory: List[dict] = []
        self.SensitivityPresetsUsed = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}
        self.SessionInterrupted = False
        self.LastStatsSaveTime = time.monotonic()
        
        self.RevealEndTime = 0.0
        self.HintEndTime = 0.0
        self.LastButtonsState: List[int] = [] # Храним предыдущие состояния всех кнопок
        self.LastFrameTime = time.monotonic()

    def GenerateRandomPath(self) -> None:
        Path = [(0, 0)]
        Current = (0, 0)
        while Current[0] < GridSize - 1 or Current[1] < GridSize - 1:
            ValidMoves = []
            if Current[0] < GridSize - 1:
                ValidMoves.append((Current[0] + 1, Current[1]))
            if Current[1] < GridSize - 1:
                ValidMoves.append((Current[0], Current[1] + 1))
            Current = random.choice(ValidMoves)
            Path.append(Current)
        self.GridPath = Path
        self.PathGridSet = set(Path)

    def StartNewRound(self) -> None:
        self.GenerateRandomPath()
        self.PlayerPos = Vector2(CellSize / 2.0, TopBarHeight + CellSize / 2.0)
        self.StartTime = time.monotonic()
        self.EndTime = 0.0
        self.TotalHintsTaken = 0
        self.CollisionCount = 0
        self.PathLengthPx = 0.0
        self.SpeedSumPxPerSecond = 0.0
        self.SpeedSquaredSum = 0.0
        self.SpeedSamples = 0
        self.CurrentSpeedPxPerSecond = 0.0
        self.MaxSpeedPxPerSecond = 0.0
        self.MovementActiveSeconds = 0.0
        self.ActiveMovementDistancePx = 0.0
        self.DeviationSumPx = 0.0
        self.DeviationSamples = 0
        self.CurrentPathDeviationPx = 0.0
        self.MaxPathDeviationPx = 0.0
        self.FirstMovementLatencySeconds = None
        self.RoundId = str(__import__("uuid").uuid4())
        self.RoundCaptured = False
        self.RoundSensitivityPresetsUsed = {SENSITIVITY_NAMES[CurrentSensitivityIdx]}
        self.GameIsWon = False
        self.RevealEndTime = time.monotonic() + RevealSeconds
        self.HintEndTime = 0.0
        self.LastButtonsState = []
        self.LastFrameTime = time.monotonic()
        self.SensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])
        self.State = "PLAYING"

    def GetCellCoords(self, GridX: int, GridY: int) -> pygame.Rect:
        return pygame.Rect(GridX * CellSize, TopBarHeight + GridY * CellSize, CellSize, CellSize)

    def GetCellUnderPlayer(self) -> Tuple[int, int]:
        Gx = int(self.PlayerPos.x // CellSize)
        Gy = int((self.PlayerPos.y - TopBarHeight) // CellSize)
        return max(0, min(GridSize - 1, Gx)), max(0, min(GridSize - 1, Gy))

    def ToLogicalPos(self, Pos: Tuple[int, int]) -> Tuple[float, float]:
        return (Pos[0] * BaseWidth / DisplayWidth, Pos[1] * BaseHeight / DisplayHeight)

    def DrawGlowRect(self, Rect: pygame.Rect, Color: Tuple[int,int,int], Thickness: int, GlowRadius: int):
        for r in range(GlowRadius, 0, -2):
            Alpha = int((1.0 - (r / GlowRadius)) * 50)
            GlowSurf = pygame.Surface((Rect.width + r*2, Rect.height + r*2), pygame.SRCALPHA)
            pygame.draw.rect(GlowSurf, (Color[0], Color[1], Color[2], Alpha), (0, 0, Rect.width + r*2, Rect.height + r*2), Thickness + r, border_radius=6)
            self.Screen.blit(GlowSurf, (Rect.x - r, Rect.y - r))

    def DrawPortSelectScreen(self) -> None:
        Txt = FontBig.render("НАСТРОЙКА МИННОГО ПОЛЯ", True, COLOR_TEXT)
        self.Screen.blit(Txt, ((BaseWidth - Txt.get_width()) // 2, 40))
        
        Panel = pygame.Rect(BaseWidth // 2 - 240, 130, 480, 240)
        pygame.draw.rect(self.Screen, COLOR_GLASS, Panel, border_radius=14)
        
        if not self.AvailablePorts:
            Err = Font.render("Контроллеры не найдены! Проверьте USB", True, COLOR_NEON_PINK)
            self.Screen.blit(Err, ((BaseWidth - Err.get_width()) // 2, 220))
        else:
            for idx, port in enumerate(self.AvailablePorts[:4]):
                ItemR = pygame.Rect(BaseWidth // 2 - 210, 160 + idx * 48, 420, 38)
                if idx == self.SelectedPortIdx:
                    pygame.draw.rect(self.Screen, COLOR_NEON_BLUE, ItemR, border_radius=8)
                    Col = COLOR_TEXT_DARK
                else:
                    pygame.draw.rect(self.Screen, (255,255,255,15), ItemR, border_radius=8)
                    Col = COLOR_TEXT
                self.Screen.blit(Font.render(f"Порт: {port}", True, Col), (ItemR.x + 15, ItemR.y + 7))

        BtnStart = pygame.Rect(BaseWidth // 2 - 130, BaseHeight - 120, 260, 46)
        self.DrawGlowRect(BtnStart, COLOR_NEON_BLUE, 2, 10)
        pygame.draw.rect(self.Screen, COLOR_NEON_BLUE, BtnStart, border_radius=10)
        Stxt = Font.render("ПОДКЛЮЧИТЬСЯ", True, COLOR_TEXT_DARK)
        self.Screen.blit(Stxt, (BtnStart.x + (BtnStart.width - Stxt.get_width()) // 2, BtnStart.y + 11))

    def DrawPlayingScreen(self) -> None:
        TimeNow = time.monotonic()
        
        pygame.draw.rect(self.Screen, (10, 8, 22), (0, TopBarHeight, BaseWidth, BaseHeight - TopBarHeight))
        pygame.draw.rect(self.Screen, (0, 50, 100), self.GetCellCoords(0, 0))
        pygame.draw.rect(self.Screen, (100, 60, 0), self.GetCellCoords(GridSize - 1, GridSize - 1))
        
        ShowPath = (TimeNow < self.RevealEndTime) or (TimeNow < self.HintEndTime) or (self.State == "RESULT" and not self.GameIsWon)
        if ShowPath:
            PathSurf = pygame.Surface((BaseWidth, BaseHeight), pygame.SRCALPHA)
            for Gx, Gy in self.GridPath:
                CellR = self.GetCellCoords(Gx, Gy)
                pygame.draw.rect(PathSurf, COLOR_PATH, CellR)
                pygame.draw.rect(PathSurf, COLOR_NEON_GREEN, CellR, 1)
            self.Screen.blit(PathSurf, (0, 0))

        for i in range(GridSize + 1):
            pygame.draw.line(self.Screen, COLOR_GRID_LINE, (i * CellSize, TopBarHeight), (i * CellSize, BaseHeight), 1)
            pygame.draw.line(self.Screen, COLOR_GRID_LINE, (0, TopBarHeight + i * CellSize), (BaseWidth, TopBarHeight + i * CellSize), 1)

        StartR = self.GetCellCoords(0, 0)
        FinishR = self.GetCellCoords(GridSize - 1, GridSize - 1)
        self.Screen.blit(FontSmall.render("СТАРТ", True, COLOR_NEON_BLUE), (StartR.x + 8, StartR.y + 20))
        self.Screen.blit(FontSmall.render("ФИНИШ", True, COLOR_FINISH), (FinishR.x + 5, FinishR.y + 20))

        if self.State == "PLAYING":
            pygame.draw.circle(self.Screen, COLOR_PLAYER, (int(self.PlayerPos.x), int(self.PlayerPos.y)), int(self.PlayerRadius))
            pygame.draw.circle(self.Screen, (255, 255, 255), (int(self.PlayerPos.x), int(self.PlayerPos.y)), int(self.PlayerRadius), 2)

        pygame.draw.rect(self.Screen, COLOR_GLASS, (0, 0, BaseWidth, TopBarHeight - 10))
        pygame.draw.line(self.Screen, COLOR_NEON_BLUE, (0, TopBarHeight - 10), (BaseWidth, TopBarHeight - 10), 2)
        
        ElapsedTime = TimeNow - self.StartTime if self.State == "PLAYING" else self.EndTime - self.StartTime
        
        self.Screen.blit(Font.render(f"Время: {ElapsedTime:.1f}с", True, COLOR_TEXT), (20, 20))
        self.Screen.blit(Font.render(f"Взрывы: {self.CollisionCount}", True, COLOR_NEON_PINK), (180, 20))
        self.Screen.blit(Font.render(f"Подсказки: {self.TotalHintsTaken}", True, COLOR_NEON_GREEN), (340, 20))
        
        SpeedColor = COLOR_NEON_GREEN if self.CurrentSpeedPxPerSecond < 180 else COLOR_NEON_PINK
        self.Screen.blit(Font.render(f"Скорость: {self.CurrentSpeedPxPerSecond:.0f}", True, SpeedColor), (490, 20))
        
        # Отображение текущей чувствительности
        current_sens_name = SENSITIVITY_NAMES[CurrentSensitivityIdx]
        sens_col = COLOR_NEON_GREEN if current_sens_name in ["Средняя"] else (COLOR_NEON_BLUE if current_sens_name == "Низкая" else COLOR_NEON_PINK)
        self.Screen.blit(FontSmall.render(f"Чувствительность: {current_sens_name}", True, sens_col), (20, 80))

        if self.State == "PLAYING":
            if TimeNow < self.RevealEndTime:
                SecLeft = max(0.0, self.RevealEndTime - TimeNow)
                self.Screen.blit(FontSmall.render(f"Запоминайте путь! Осталось: {SecLeft:.1f}с", True, COLOR_NEON_GREEN), (20, 60))
            elif TimeNow < self.HintEndTime:
                SecLeft = max(0.0, self.HintEndTime - TimeNow)
                self.Screen.blit(FontSmall.render(f"Подсказка активна: {SecLeft:.1f}с", True, COLOR_NEON_BLUE), (20, 60))
            else:
                self.Screen.blit(FontSmall.render("Путь скрыт. Нажмите кнопку на джойстике (D2, D3, D4) для подсказки", True, COLOR_TEXT_MUTED), (20, 60))

    def DrawResultScreen(self) -> None:
        PanelR = pygame.Rect(45, 45, BaseWidth - 90, BaseHeight - 140)
        BorderColor = COLOR_NEON_GREEN if self.GameIsWon else COLOR_NEON_PINK
        self.DrawGlowRect(PanelR, BorderColor, 2, 10)
        pygame.draw.rect(self.Screen, COLOR_GLASS, PanelR, border_radius=16)
        
        TextTitle = "ПОЛЕ УСПЕШНО ПРОЙдено!" if self.GameIsWon else "ВЫ ПОДОРВАЛИСЬ НА МИНЕ!"
        Title = FontBig.render(TextTitle, True, BorderColor)
        self.Screen.blit(Title, ((BaseWidth - Title.get_width()) // 2, 65))
        
        Duration = self.EndTime - self.StartTime
        AverageCursorSpeed = self.PathLengthPx / Duration if Duration > 0 else 0.0
        AverageActiveSpeed = self.ActiveMovementDistancePx / self.MovementActiveSeconds if self.MovementActiveSeconds > 0 else 0.0
        AveragePathDeviation = self.DeviationSumPx / max(1, self.DeviationSamples)
        
        self.Screen.blit(Font.render(f"Время: {Duration:.2f} сек", True, COLOR_TEXT), (70, 150))
        self.Screen.blit(Font.render(f"Статус: {'Победа' if self.GameIsWon else 'Взрыв'}", True, BorderColor), (70, 195))
        self.Screen.blit(Font.render(f"Подсказки: {self.TotalHintsTaken}", True, COLOR_NEON_GREEN), (70, 240))
        
        pygame.draw.line(self.Screen, (255,255,255,25), (BaseWidth // 2 - 10, 150), (BaseWidth // 2 - 10, 280), 1)
        
        self.Screen.blit(Font.render("АНАЛИЗ ДВИЖЕНИЯ:", True, COLOR_NEON_BLUE), (BaseWidth // 2 + 15, 150))
        self.Screen.blit(FontSmall.render(f"Длина траектории: {self.PathLengthPx:.0f} px", True, COLOR_TEXT), (BaseWidth // 2 + 15, 195))
        self.Screen.blit(FontSmall.render(f"Средняя скорость: {AverageCursorSpeed:.1f} px/с", True, COLOR_TEXT), (BaseWidth // 2 + 15, 225))
        self.Screen.blit(FontSmall.render(f"Скорость в движении: {AverageActiveSpeed:.1f} px/с", True, COLOR_TEXT), (BaseWidth // 2 + 15, 255))
        self.Screen.blit(FontSmall.render(f"Отклонение от линии пути: {AveragePathDeviation:.1f} px", True, COLOR_TEXT_MUTED), (BaseWidth // 2 + 15, 285))
        
        BtnRetry = pygame.Rect(70, BaseHeight - 170, 140, 40)
        pygame.draw.rect(self.Screen, COLOR_GLASS, BtnRetry, border_radius=10)
        pygame.draw.rect(self.Screen, COLOR_NEON_GREEN, BtnRetry, 1, border_radius=10)
        self.Screen.blit(Font.render("ПОВТОР", True, COLOR_TEXT), (BtnRetry.x + (BtnRetry.width - Font.render("ПОВТОР", True, COLOR_TEXT).get_width())//2, BtnRetry.y + 8))
        
        BtnSetup = pygame.Rect(235, BaseHeight - 170, 150, 40)
        pygame.draw.rect(self.Screen, COLOR_GLASS, BtnSetup, border_radius=10)
        pygame.draw.rect(self.Screen, (255,255,255,30), BtnSetup, 1, border_radius=10)
        self.Screen.blit(Font.render("В НАСТРОЙКИ", True, COLOR_TEXT_MUTED), (BtnSetup.x + (BtnSetup.width - Font.render("В НАСТРОЙКИ", True, COLOR_TEXT_MUTED).get_width())//2, BtnSetup.y + 8))

        BtnMenu = pygame.Rect(410, BaseHeight - 170, 140, 40)
        pygame.draw.rect(self.Screen, COLOR_GLASS, BtnMenu, border_radius=10)
        pygame.draw.rect(self.Screen, COLOR_NEON_PINK, BtnMenu, 1, border_radius=10)
        self.Screen.blit(Font.render("В МЕНЮ", True, COLOR_TEXT), (BtnMenu.x + (BtnMenu.width - Font.render("В МЕНЮ", True, COLOR_TEXT).get_width())//2, BtnMenu.y + 8))

    def GetDistanceToPathCenterline(self, Position: Vector2) -> float:
        """Minimum distance to the generated route's centerline, in logical pixels."""
        if len(self.GridPath) < 2:
            return 0.0
        pointX, pointY = Position.x, Position.y
        minimumDistance = float("inf")
        for segmentIndex in range(len(self.GridPath) - 1):
            startGridX, startGridY = self.GridPath[segmentIndex]
            endGridX, endGridY = self.GridPath[segmentIndex + 1]
            startX = startGridX * CellSize + CellSize / 2.0
            startY = TopBarHeight + startGridY * CellSize + CellSize / 2.0
            endX = endGridX * CellSize + CellSize / 2.0
            endY = TopBarHeight + endGridY * CellSize + CellSize / 2.0
            deltaX, deltaY = endX - startX, endY - startY
            segmentLengthSquared = deltaX * deltaX + deltaY * deltaY
            if segmentLengthSquared <= 0.0:
                nearestX, nearestY = startX, startY
            else:
                projection = ((pointX - startX) * deltaX + (pointY - startY) * deltaY) / segmentLengthSquared
                projection = max(0.0, min(1.0, projection))
                nearestX = startX + projection * deltaX
                nearestY = startY + projection * deltaY
            minimumDistance = min(minimumDistance, math.hypot(pointX - nearestX, pointY - nearestY))
        return 0.0 if math.isinf(minimumDistance) else minimumDistance

    def MakeRoundSnapshot(self, Outcome: str, Terminal: bool, EndTime: Optional[float] = None) -> dict:
        RoundEndTime = self.EndTime if EndTime is None and self.EndTime > 0 else EndTime
        if RoundEndTime is None:
            RoundEndTime = time.monotonic()
        DurationSeconds = max(0.0, RoundEndTime - self.StartTime) if self.StartTime > 0 else 0.0
        return {
            "round_id": self.RoundId,
            "outcome": Outcome,
            "terminal": bool(Terminal),
            "won": bool(self.GameIsWon),
            "duration_seconds": DurationSeconds,
            "collision_count": int(self.CollisionCount),
            "hint_count": int(self.TotalHintsTaken),
            "path_length_px": float(self.PathLengthPx),
            "speed_sum_px_per_second": float(self.SpeedSumPxPerSecond),
            "speed_squared_sum": float(self.SpeedSquaredSum),
            "speed_samples": int(self.SpeedSamples),
            "max_speed_px_per_second": float(self.MaxSpeedPxPerSecond),
            "movement_active_seconds": float(self.MovementActiveSeconds),
            "active_movement_distance_px": float(self.ActiveMovementDistancePx),
            "deviation_sum_px": float(self.DeviationSumPx),
            "deviation_samples": int(self.DeviationSamples),
            "max_path_deviation_px": float(self.MaxPathDeviationPx),
            "first_movement_latency_seconds": self.FirstMovementLatencySeconds,
            "sensitivity_presets_used": sorted(self.RoundSensitivityPresetsUsed),
        }

    def CaptureRound(self, Outcome: str, Terminal: bool) -> None:
        if self.RoundCaptured or self.StartTime <= 0:
            return
        if self.EndTime <= 0:
            self.EndTime = time.monotonic()
        self.RoundHistory.append(self.MakeRoundSnapshot(Outcome, Terminal, self.EndTime))
        self.RoundCaptured = True
        if Outcome == "interrupted":
            self.SessionInterrupted = True
        self.SaveStatistics("interrupted")

    def BuildSessionMetrics(self, IncludeActiveRound: bool = False) -> tuple:
        SessionRounds = list(self.RoundHistory)
        if IncludeActiveRound and self.State == "PLAYING" and self.StartTime > 0 and not self.RoundCaptured:
            SessionRounds.append(self.MakeRoundSnapshot("in_progress", False))

        RoundsPlayed = len(SessionRounds)
        RoundsResolved = sum(1 for Round in SessionRounds if Round["terminal"])
        RoundsWon = sum(1 for Round in SessionRounds if Round["outcome"] == "won")
        RoundsFailed = sum(1 for Round in SessionRounds if Round["outcome"] == "mine_hit")
        RoundsRestarted = sum(1 for Round in SessionRounds if Round["outcome"] == "restarted")
        RoundsInterrupted = sum(1 for Round in SessionRounds if Round["outcome"] in ("interrupted", "in_progress"))
        TotalDuration = sum(Round["duration_seconds"] for Round in SessionRounds)
        TotalHints = sum(Round["hint_count"] for Round in SessionRounds)
        TotalCollisions = sum(Round["collision_count"] for Round in SessionRounds)
        TotalPathLength = sum(Round["path_length_px"] for Round in SessionRounds)
        TotalSpeedSum = sum(Round["speed_sum_px_per_second"] for Round in SessionRounds)
        TotalSpeedSquaredSum = sum(Round["speed_squared_sum"] for Round in SessionRounds)
        TotalSpeedSamples = sum(Round["speed_samples"] for Round in SessionRounds)
        MaxSpeed = max((Round["max_speed_px_per_second"] for Round in SessionRounds), default=0.0)
        ActiveSeconds = sum(Round["movement_active_seconds"] for Round in SessionRounds)
        ActiveDistance = sum(Round["active_movement_distance_px"] for Round in SessionRounds)
        DeviationSum = sum(Round["deviation_sum_px"] for Round in SessionRounds)
        DeviationSamples = sum(Round["deviation_samples"] for Round in SessionRounds)
        MaxDeviation = max((Round["max_path_deviation_px"] for Round in SessionRounds), default=0.0)
        MovementLatencies = [Round["first_movement_latency_seconds"] for Round in SessionRounds if Round["first_movement_latency_seconds"] is not None]
        MeanSampleSpeed = TotalSpeedSum / TotalSpeedSamples if TotalSpeedSamples else 0.0
        SpeedVariance = max(0.0, TotalSpeedSquaredSum / TotalSpeedSamples - MeanSampleSpeed ** 2) if TotalSpeedSamples else 0.0
        AverageSpeed = TotalPathLength / TotalDuration if TotalDuration else 0.0
        AverageActiveSpeed = ActiveDistance / ActiveSeconds if ActiveSeconds else 0.0
        AverageDeviation = DeviationSum / DeviationSamples if DeviationSamples else 0.0

        Metrics = {
            "roundsPlayed": metric("Игровые попытки", RoundsPlayed, "шт."),
            "roundsResolved": metric("Завершённые попытки", RoundsResolved, "шт."),
            "roundsWon": metric("Успешно пройденные поля", RoundsWon, "шт."),
            "roundsFailed": metric("Неудачные попытки (мина)", RoundsFailed, "шт."),
            "roundsRestarted": metric("Перезапущенные попытки", RoundsRestarted, "шт."),
            "roundsInterrupted": metric("Прерванные попытки", RoundsInterrupted, "шт."),
            "totalMineHits": metric("Попадания на мину", TotalCollisions, "шт."),
            "hintCount": metric("Использованные подсказки", TotalHints, "шт."),
            "activeGameplaySeconds": metric("Суммарное время попыток", TotalDuration, "сек."),
            "cursorPathLengthPx": metric("Суммарная длина траектории", TotalPathLength, "px"),
            "averageCursorSpeedPxPerSecond": metric("Средняя скорость курсора за время попыток", AverageSpeed, "px/сек"),
            "averageActiveMovementSpeedPxPerSecond": metric("Средняя скорость во время движения", AverageActiveSpeed, "px/сек"),
            "maxCursorSpeedPxPerSecond": metric("Максимальная скорость курсора", MaxSpeed, "px/сек"),
            "cursorSpeedStandardDeviationPxPerSecond": metric("Стандартное отклонение скорости курсора", math.sqrt(SpeedVariance), "px/сек"),
            "averageDistanceToPathCenterlinePx": metric("Среднее расстояние до центральной линии пути", AverageDeviation, "px"),
            "maxDistanceToPathCenterlinePx": metric("Максимальное расстояние до центральной линии пути", MaxDeviation, "px"),
        }
        if RoundsResolved:
            Metrics["successRatePct"] = metric("Доля побед среди завершённых попыток", RoundsWon / RoundsResolved * 100.0, "%")
        if MovementLatencies:
            Metrics["averageTimeToFirstMovementSeconds"] = metric("Среднее время до первого движения", sum(MovementLatencies) / len(MovementLatencies), "сек.")
        if RoundsResolved:
            Metrics["averageRoundDurationSeconds"] = metric("Средняя длительность завершённой попытки", sum(Round["duration_seconds"] for Round in SessionRounds if Round["terminal"]) / RoundsResolved, "сек.")

        Settings = {
            "logical_resolution_px": {"width": BaseWidth, "height": BaseHeight},
            "grid_size_cells": GridSize,
            "cell_size_px": CellSize,
            "initial_path_reveal_seconds": RevealSeconds,
            "hint_display_seconds": HintSeconds,
            "movement_active_threshold_px_per_second": 5.0,
            "sensitivity_presets_used": sorted(self.SensitivityPresetsUsed),
            "path_deviation_method": "minimum Euclidean distance to any segment of the generated route centerline",
        }
        return Metrics, Settings, {"rounds": SessionRounds}

    def SaveStatistics(self, SessionStatus: str, IncludeActiveRound: bool = False) -> None:
        Metrics, Settings, Details = self.BuildSessionMetrics(IncludeActiveRound)
        statistics.save(SessionStatus, Metrics, Settings, Details)

    def EmergencySaveStatistics(self) -> None:
        if sessionFinalized:
            return
        try:
            if self.State == "PLAYING" and self.StartTime > 0 and not self.RoundCaptured:
                self.CaptureRound("interrupted", False)
            self.SaveStatistics("interrupted", IncludeActiveRound=True)
        except Exception as error:
            print(f"Не удалось сохранить статистику при выходе: {error}")

    def Run(self) -> None:
        global sessionFinalized
        Running = True
        while Running:
            Clock.tick(60)
            TimeNow = time.monotonic()
            Dt = max(0.001, TimeNow - self.LastFrameTime)
            self.LastFrameTime = TimeNow

            for y in range(BaseHeight):
                Ratio = y / BaseHeight
                r = int(COLOR_BG_DARK[0] * (1 - Ratio) + COLOR_BG_LIGHT[0] * Ratio)
                g = int(COLOR_BG_DARK[1] * (1 - Ratio) + COLOR_BG_LIGHT[1] * Ratio)
                b = int(COLOR_BG_DARK[2] * (1 - Ratio) + COLOR_BG_LIGHT[2] * Ratio)
                pygame.draw.line(self.Screen, (r, g, b), (0, y), (BaseWidth, y))

            Dx, Dy, ButtonValues = self.SerialDevice.ReadData()

            if self.State == "PLAYING":
                OldPos = Vector2(self.PlayerPos)
                self.PlayerPos.x += Dx
                self.PlayerPos.y += Dy
                self.PlayerPos.x = max(self.PlayerRadius, min(BaseWidth - self.PlayerRadius, self.PlayerPos.x))
                self.PlayerPos.y = max(TopBarHeight + self.PlayerRadius, min(BaseHeight - self.PlayerRadius, self.PlayerPos.y))

                MovementDistance = OldPos.distance_to(self.PlayerPos)
                self.CurrentSpeedPxPerSecond = MovementDistance / Dt
                self.PathLengthPx += MovementDistance
                self.SpeedSumPxPerSecond += self.CurrentSpeedPxPerSecond
                self.SpeedSquaredSum += self.CurrentSpeedPxPerSecond ** 2
                self.SpeedSamples += 1
                self.MaxSpeedPxPerSecond = max(self.MaxSpeedPxPerSecond, self.CurrentSpeedPxPerSecond)
                if self.CurrentSpeedPxPerSecond >= 5.0:
                    self.MovementActiveSeconds += Dt
                    self.ActiveMovementDistancePx += MovementDistance
                    if self.FirstMovementLatencySeconds is None:
                        self.FirstMovementLatencySeconds = max(0.0, TimeNow - self.StartTime)
                self.CurrentPathDeviationPx = self.GetDistanceToPathCenterline(self.PlayerPos)
                self.DeviationSumPx += self.CurrentPathDeviationPx
                self.DeviationSamples += 1
                self.MaxPathDeviationPx = max(self.MaxPathDeviationPx, self.CurrentPathDeviationPx)

                # --- ОБРАБОТКА HID-КНОПОК ---
                # GP8 = HID Button 4 = индекс 3; переключаем только по фронту нажатия.
                if len(ButtonValues) > 3:
                    PreviousButton4 = (
                        self.LastButtonsState[3]
                        if len(self.LastButtonsState) > 3 else 0
                    )
                    if ButtonValues[3] == 1 and PreviousButton4 == 0:
                        global CurrentSensitivityIdx
                        CurrentSensitivityIdx = (
                            CurrentSensitivityIdx + 1
                        ) % len(SENSITIVITY_NAMES)
                        self.SensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])
                        self.RoundSensitivityPresetsUsed.add(SENSITIVITY_NAMES[CurrentSensitivityIdx])

                # Подсказка активируется по любому изменению кнопки
                if ButtonValues and self.LastButtonsState:
                    Triggered = False
                    # GP8 (индекс 3) переключает чувствительность и не считается подсказкой.
                    for i in range(min(3, len(ButtonValues), len(self.LastButtonsState))):
                        CurV = ButtonValues[i]
                        OldV = self.LastButtonsState[i]
                        if CurV == 1 and OldV == 0:
                            Triggered = True
                            break

                    if Triggered and TimeNow > self.RevealEndTime:
                        self.TotalHintsTaken += 1
                        self.HintEndTime = TimeNow + HintSeconds

                if ButtonValues:
                    self.LastButtonsState = list(ButtonValues)
                # --------------------------------------------

                Gx, Gy = self.GetCellUnderPlayer()
                if (Gx, Gy) not in self.PathGridSet:
                    self.CollisionCount += 1
                    self.EndTime = TimeNow
                    self.GameIsWon = False
                    self.State = "RESULT"
                    self.CaptureRound("mine_hit", True)
                
                if Gx == GridSize - 1 and Gy == GridSize - 1:
                    self.EndTime = TimeNow
                    self.GameIsWon = True
                    self.State = "RESULT"
                    self.CaptureRound("won", True)

            for Event in pygame.event.get():
                if Event.type == pygame.QUIT:
                    if self.State == "PLAYING":
                        self.SessionInterrupted = True
                    Running = False
                elif Event.type == pygame.KEYDOWN:
                    if Event.key == pygame.K_ESCAPE:
                        if self.State == "PLAYING":
                            self.SessionInterrupted = True
                        Running = False
                    if self.State == "PORT_SELECT":
                        if Event.key == pygame.K_UP:
                            self.SelectedPortIdx = (self.SelectedPortIdx - 1) % max(1, len(self.AvailablePorts))
                        elif Event.key == pygame.K_DOWN:
                            self.SelectedPortIdx = (self.SelectedPortIdx + 1) % max(1, len(self.AvailablePorts))
                        elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            if self.AvailablePorts:
                                self.SerialDevice.Connect(self.AvailablePorts[self.SelectedPortIdx])
                            self.StartNewRound()
                    elif self.State == "RESULT":
                        if Event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.StartNewRound()

                elif Event.type == pygame.MOUSEBUTTONDOWN and Event.button == 1:
                    LPos = self.ToLogicalPos(Event.pos)
                    if self.State == "PORT_SELECT":
                        BtnStart = pygame.Rect(BaseWidth // 2 - 130, BaseHeight - 120, 260, 46)
                        if BtnStart.collidepoint(LPos):
                            if self.AvailablePorts:
                                self.SerialDevice.Connect(self.AvailablePorts[self.SelectedPortIdx])
                            self.StartNewRound()
                        for idx in range(len(self.AvailablePorts[:4])):
                            if pygame.Rect(BaseWidth // 2 - 210, 160 + idx * 48, 420, 38).collidepoint(LPos):
                                self.SelectedPortIdx = idx
                                
                    elif self.State == "RESULT":
                        if pygame.Rect(70, BaseHeight - 170, 140, 40).collidepoint(LPos):
                            self.StartNewRound()
                        elif pygame.Rect(235, BaseHeight - 170, 150, 40).collidepoint(LPos):
                            self.State = "PORT_SELECT"
                            self.AvailablePorts = [f"{Idx}: {pygame.joystick.Joystick(Idx).get_name()}" for Idx in range(pygame.joystick.get_count())]
                        elif pygame.Rect(410, BaseHeight - 170, 140, 40).collidepoint(LPos):
                            self.State = "PORT_SELECT"

            if self.State == "PORT_SELECT":
                self.DrawPortSelectScreen()
            elif self.State in ("PLAYING", "RESULT"):
                self.DrawPlayingScreen()
                if self.State == "RESULT":
                    self.DrawResultScreen()

            ScaledFrame = pygame.transform.smoothscale(self.Screen, (DisplayWidth, DisplayHeight))
            DisplayScreen.blit(ScaledFrame, (0, 0))
            pygame.display.flip()

            if TimeNow - self.LastStatsSaveTime >= 2.0:
                self.SaveStatistics("interrupted", IncludeActiveRound=self.State == "PLAYING")
                self.LastStatsSaveTime = TimeNow

        if self.State == "PLAYING" and self.StartTime > 0 and not self.RoundCaptured:
            self.CaptureRound("interrupted", False)
            self.SessionInterrupted = True
        HasResolvedRounds = any(Round["terminal"] for Round in self.RoundHistory)
        FinalStatus = "completed" if HasResolvedRounds and not self.SessionInterrupted else "interrupted"
        self.SaveStatistics(FinalStatus)
        sessionFinalized = True
        self.SerialDevice.Close()
        pygame.quit()

def main() -> None:
    App = MinefieldGame()
    App.Run()

if __name__ == "__main__":
    main()
