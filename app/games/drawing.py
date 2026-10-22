import math
import time
import sys
import atexit
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import pygame
import numpy as np

_scriptDirectory = Path(__file__).resolve().parent
for _candidateDirectory in (_scriptDirectory, *_scriptDirectory.parents):
    if (_candidateDirectory / "moveup_stats.py").exists():
        sys.path.insert(0, str(_candidateDirectory))
        break
from moveup_stats import MoveUpStatistics, metric

moveUpStatistics = MoveUpStatistics(
    "Drawing", "drawing", sys.executable if getattr(sys, "frozen", False) else __file__
)
drawingRoundHistory: list[dict] = []
activeDrawingSnapshot: Optional[dict] = None
drawingSessionFinalized = False
drawingLastStatsSaveTime = 0.0

WindowWidth = 1000  
WindowHeight = 750

PointRadius = 6    
DeadZone = 0.05            
InputFilterStrength = 0.12

# GP8 = HID Button 4 = индекс 3 в Pygame.
SENSITIVITY_PRESETS = {
    "Низкая": 0.55,
    "Средняя": 0.75,
    "Высокая": 1.0,
    "Максимальная": 1.25,
}
SENSITIVITY_NAMES = list(SENSITIVITY_PRESETS)
CurrentSensitivityIdx = 1
PreviousSensitivityButton = 0

# Mapping for the updated Pico firmware, confirmed in gyro-tester:
#   Ry increases when tilting away and decreases when tilting toward the user.
#   Rz increases when tilting left and decreases when tilting right.
# Pitch is calculated from Ry and Roll from Rz. Keep the axes unswapped:
#   Rz -> horizontal cursor movement (positive Rz means cursor left)
#   Ry -> vertical cursor movement (positive Ry means cursor up)
RightSign = -1.0
ForwardSign = 1.0
SwapAxes = False

# HID axes used for the synthetic tilt input.
TiltAxisX = 4
TiltAxisY = 5
TiltAxisZ = 6

MaxSpeedEasy = 190.0
MaxSpeedMedium = 170.0
MaxSpeedHard = 150.0

Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]

COLOR_BG = (10, 11, 16)         
COLOR_PANEL = (18, 20, 28)      
COLOR_PRIMARY = (0, 229, 255)    
COLOR_SUCCESS = (0, 230, 118)    
COLOR_WARNING = (255, 193, 7)    
COLOR_ACCENT = (255, 0, 128)     
COLOR_TEXT_MAIN = (240, 243, 250)
COLOR_TEXT_MUTED = (110, 118, 142)
COLOR_BORDER = (35, 41, 57)

@dataclass
# ---------- Описание фигуры для рисования ----------
class DrawingDefinition:
    Name: str
    Builder: Callable[[], List[Point2D]]
    Closed: bool = True

@dataclass
# ---------- Описание уровня сложности ----------
class DifficultyDefinition:
    Name: str
    Speed: float
    Tolerance: float
    MissLimitSeconds: float
    Drawings: List[DrawingDefinition]

# ---------- Система частиц ----------
class ParticleSystem:
    # ---------- Инициализация ----------
    def __init__(self):
        self.particles = []  

    # ---------- Создание частиц ----------
    def emit(self, x, y, color, count=1):
        for _ in range(count):
            angle = np.random.uniform(0, 2 * math.pi)
            speed = np.random.uniform(10, 40)
            self.particles.append([
                x, y,
                math.cos(angle) * speed,
                math.sin(angle) * speed,
                205.0,
                color,
                np.random.uniform(1.5, 3.5)
            ])

    # ---------- Обновление частиц ----------
    def update(self, dt):
        alive_particles = []
        for p in self.particles:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[4] -= 400 * dt  
            if p[4] > 0:
                alive_particles.append(p)
        self.particles = alive_particles

    # ---------- Отрисовка частиц ----------
    def draw(self, surface):
        for p in self.particles:
            x, y, _, _, alpha, color, radius = p
            p_surf = pygame.Surface((int(radius*2)+2, int(radius*2)+2), pygame.SRCALPHA)
            pygame.draw.circle(p_surf, (*color, int(alpha)), (int(radius)+1, int(radius)+1), int(radius))
            surface.blit(p_surf, (int(x - radius), int(y - radius)))

# ---------- Создание поверхности свечения ----------
def create_glow_surface(radius: int, color: Tuple[int, int, int], max_alpha=80) -> pygame.Surface:
    side = radius * 2
    glow = pygame.Surface((side, side), pygame.SRCALPHA)
    y, x = np.ogrid[-radius:radius, -radius:radius]
    dist = np.sqrt(x**2 + y**2)
    alpha = max_alpha * (1.0 - (dist / radius))
    alpha = np.clip(alpha, 0, max_alpha).astype(np.uint8)
    
    rgba = np.zeros((side, side, 4), dtype=np.uint8)
    rgba[..., 0] = color[0]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[2]
    rgba[..., 3] = alpha
    
    pygame.surfarray.blit_array(glow, rgba[..., :3])
    alpha_surf = pygame.pixelcopy.make_surface(rgba[..., 3])
    glow.blit(alpha_surf, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
    return glow

# ---------- Вывод доступных HID-устройств в консоль ----------
def ShowDevices():
    if pygame.joystick.get_count() <= 0:
        return
    for Index in range(pygame.joystick.get_count()):
        Joy = pygame.joystick.Joystick(Index)
        Joy.init()
        print(f"  [{Index}] {Joy.get_name()}  |  axes={Joy.get_numaxes()}  buttons={Joy.get_numbuttons()}")

# ---------- Получение и сортировка HID-устройств ----------
def GetDevices():
    return list(range(pygame.joystick.get_count()))

# ---------- Открытие HID-устройства ----------
def OpenJoystick(DeviceIndex):
    Device = pygame.joystick.Joystick(DeviceIndex)
    Device.init()
    time.sleep(0.2)
    return Device

class JoystickInputReader:
    # ---------- Инициализация ----------
    def __init__(self, Device):
        self.Device = Device

    # ---------- Чтение актуального состояния ----------
    def read_latest(self):
        if self.Device is None:
            return None, None, False
        try:
            pygame.event.pump()
            AxesCount = self.Device.get_numaxes()
            # Axis 4/5/6 — это gyro-каналы Pico.
            GyroX = self.Device.get_axis(4) if AxesCount > 4 else 0.0
            GyroY = self.Device.get_axis(5) if AxesCount > 5 else 0.0
            GyroZ = self.Device.get_axis(6) if AxesCount > 6 else 0.0
            # Для текущей логики игры используем синтетический вектор ориентации:
            # X/Y берём из gyro-каналов, Z фиксируем как опорную ось.
            Accel = (float(GyroX), float(GyroY), 1.0)
            Gyro = (float(GyroX), float(GyroY), float(GyroZ))
            return Accel, Gyro, True
        except pygame.error:
            return None, None, False

# ---------- Ожидание готовности устройства ----------
def WaitForReady(Device, TimeoutSeconds=2.0):
    if Device is None:
        return False
    StartTime = time.time()
    while time.time() - StartTime < TimeoutSeconds:
        Accel, Gyro, Ok = JoystickInputReader(Device).read_latest()
        if not Ok:
            return False
        if Accel is not None:
            return True
        time.sleep(0.02)
    return False

# ---------- Расчет расстояния между точками ----------
def Distance2D(A, B): return math.hypot(A[0] - B[0], A[1] - B[1])
# ---------- Линейная интерполяция точки ----------
def LerpPoint(A, B, T): return (A[0] + (B[0] - A[0]) * T, A[1] + (B[1] - A[1]) * T)

# ---------- Расчет расстояния между точками ----------
def Distance2D(A, B): return math.hypot(A[0] - B[0], A[1] - B[1])
# ---------- Линейная интерполяция точки ----------
def LerpPoint(A, B, T): return (A[0] + (B[0] - A[0]) * T, A[1] + (B[1] - A[1]) * T)

# ---------- Уплотнение пути ----------
def DensifyPath(BasePoints, Closed=True, PointsPerSegment=24):
    if len(BasePoints) < 2: return list(BasePoints)
    DensePoints = []
    SegmentCount = len(BasePoints) if Closed else len(BasePoints) - 1
    for Index in range(SegmentCount):
        StartPoint = BasePoints[Index]
        EndPoint = BasePoints[(Index + 1) % len(BasePoints)] if Closed else BasePoints[Index + 1]
        for Step in range(PointsPerSegment):
            DensePoints.append(LerpPoint(StartPoint, EndPoint, Step / float(PointsPerSegment)))
    DensePoints.append(BasePoints[0] if Closed else BasePoints[-1])
    return DensePoints

# ---------- Масштабирование точек под область ----------
def FitPointsToRect(Points, Rect, Padding=40):
    MinX, MaxX = min(P[0] for P in Points), max(P[0] for P in Points)
    MinY, MaxY = min(P[1] for P in Points), max(P[1] for P in Points)
    Width, Height = max(1e-6, MaxX - MinX), max(1e-6, MaxY - MinY)
    Scale = min(max(1, Rect.width - 2 * Padding) / Width, max(1, Rect.height - 2 * Padding) / Height)
    ShiftX, ShiftY = Rect.x + Rect.width / 2.0, Rect.y + Rect.height / 2.0
    CenterX, CenterY = (MinX + MaxX) / 2.0, (MinY + MaxY) / 2.0
    return [((X - CenterX) * Scale + ShiftX, (Y - CenterY) * Scale + ShiftY) for X, Y in Points]

# ---------- Ограничение значения ----------
def Clamp(Value, Minimum, Maximum): return max(Minimum, min(Maximum, Value))

# ---------- Генерация круга ----------
def GenerateCircle(Sides=52, Radius=1.0): return [(math.cos((math.pi * 2.0 * Index) / Sides) * Radius, math.sin((math.pi * 2.0 * Index) / Sides) * Radius) for Index in range(Sides)]
# ---------- Генерация квадрата ----------
def GenerateSquare(): return [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
# ---------- Генерация треугольника ----------
def GenerateTriangle(): return [(0.0, -1.15), (1.05, 0.9), (-1.05, 0.9)]
# ---------- Генерация ромба ----------
def GenerateDiamond(): return [(0.0, -1.15), (1.0, 0.0), (0.0, 1.15), (-1.0, 0.0)]
# ---------- Генерация домика ----------
def GenerateHouse(): return [(-1.0, 0.2), (-1.0, 1.0), (1.0, 1.0), (1.0, 0.2), (0.0, -1.1)]
# ---------- Генерация звезды ----------
def GenerateStar():
    Points = []
    for Index in range(10):
        Angle = -math.pi / 2 + Index * math.pi / 5.0
        Points.append((math.cos(Angle) * (1.1 if Index % 2 == 0 else 0.5), math.sin(Angle) * (1.1 if Index % 2 == 0 else 0.5)))
    return Points
# ---------- Генерация сердца ----------
def GenerateHeart():
    Points = []
    for Index in range(120):
        T = (math.pi * 2.0) * Index / 120
        Points.append((16.0 * math.sin(T) ** 3 / 18.0, -(13.0 * math.cos(T) - 5.0 * math.cos(2 * T) - 2.0 * math.cos(3 * T) - math.cos(4 * T)) / 18.0))
    return Points
# ---------- Генерация рыбки ----------
def GenerateFish():
    Points = []
    for Index in range(60):
        T = (math.pi * 2.0) * Index / 60
        Points.append((math.cos(T) * (1.0 + 0.15 * math.cos(2 * T)), math.sin(T) * 0.65))
    return Points[:15] + [(-1.1, 0.0), (-1.55, 0.45), (-1.55, -0.45)] + Points[15:]
# ---------- Генерация ракеты ----------
def GenerateRocket(): return [(0.0, -1.25), (0.55, -0.75), (0.55, 0.45), (0.85, 0.95), (0.2, 0.85), (0.0, 1.2), (-0.2, 0.85), (-0.85, 0.95), (-0.55, 0.45), (-0.55, -0.75)]
# ---------- Генерация листа ----------
def GenerateLeaf():
    Points = []
    for Index in range(120):
        T = (math.pi * 2.0) * Index / 120
        Points.append((math.cos(T) * (1.0 + 0.35 * math.sin(T)), math.sin(T) * (0.85 + 0.15 * math.cos(2 * T))))
    return Points
# ---------- Генерация клевера ----------
def GenerateClover():
    Points = []
    for Index in range(160):
        T = (math.pi * 2.0) * Index / 160
        Points.append((math.cos(T) * (0.52 + 0.28 * math.sin(4 * T)), math.sin(T) * (0.52 + 0.28 * math.sin(4 * T))))
    return Points
# ---------- Генерация молнии ----------
def GenerateLightning(): return [(-0.7, -1.1), (0.0, -0.25), (-0.35, -0.2), (0.55, 1.15), (-0.05, 0.35), (0.35, 0.35)]
# ---------- Генерация луны ----------
def GenerateMoon():
    Points = []
    for Index in range(100):
        T = (math.pi * 2.0) * Index / 100
        W = 0.5 + 0.5 * math.sin(T)
        Points.append(((math.cos(T) * 1.0) * W + (math.cos(T) * 0.45 + 0.32) * (1.0 - W), (math.sin(T) * 1.2) * W + (math.sin(T) * 0.85) * (1.0 - W)))
    return Points
# ---------- Генерация спирали ----------
def GenerateSpiral():
    Points = []
    for Index in range(140):
        T = Index / 139.0
        Points.append((math.cos(T * 5.2 * math.pi) * (0.15 + 0.95 * T), math.sin(T * 5.2 * math.pi) * (0.15 + 0.95 * T)))
    return Points
# ---------- Генерация волны ----------
def GenerateWave(): return [(-1.25, 0.2), (-0.95, -0.5), (-0.6, 0.4), (-0.25, -0.55), (0.1, 0.45), (0.45, -0.45), (0.8, 0.55), (1.15, -0.15)]

# ---------- Сборка списка уровней сложности ----------
def BuildDifficultyData():
    return [
        DifficultyDefinition("Легкий", MaxSpeedEasy, 34.0, 1.40, [DrawingDefinition("Квадрат", GenerateSquare), DrawingDefinition("Круг", GenerateCircle), DrawingDefinition("Треугольник", GenerateTriangle), DrawingDefinition("Домик", GenerateHouse), DrawingDefinition("Ромб", GenerateDiamond)]),
        DifficultyDefinition("Средний", MaxSpeedMedium, 28.0, 1.00, [DrawingDefinition("Звезда", GenerateStar), DrawingDefinition("Сердце", GenerateHeart), DrawingDefinition("Рыбка", GenerateFish), DrawingDefinition("Ракета", GenerateRocket), DrawingDefinition("Лист", GenerateLeaf)]),
        DifficultyDefinition("Сложный", MaxSpeedHard, 22.0, 0.75, [DrawingDefinition("Клевер", GenerateClover), DrawingDefinition("Молния", GenerateLightning), DrawingDefinition("Луна", GenerateMoon), DrawingDefinition("Волна", GenerateWave, False), DrawingDefinition("Спираль", GenerateSpiral, False)]),
    ]

# ---------- Построение пути фигуры ----------
def BuildPathForDrawing(Drawing, TraceRect):
    BasePoints = Drawing.Builder()
    RawPoints = DensifyPath(BasePoints, Closed=Drawing.Closed, PointsPerSegment=12 if Drawing.Name in ("Спираль", "Волна") else 24)
    return FitPointsToRect(RawPoints, TraceRect, Padding=35)

# ---------- Отрисовка текста ----------
def DrawText(Screen, Font, Text, X, Y, Color):
    Screen.blit(Font.render(Text, True, Color), (X, Y))

# ---------- Отрисовка панели ----------
def DrawPanel(Screen, Rect, Radius=16):
    pygame.draw.rect(Screen, COLOR_PANEL, Rect, border_radius=Radius)
    pygame.draw.rect(Screen, COLOR_BORDER, Rect, width=1, border_radius=Radius)

# ---------- Отрисовка кнопки ----------
def DrawModernButton(Screen, Font, Rect, Text, Selected, PulseAlpha=0):
    Background = (24, 32, 48) if Selected else (14, 15, 22)
    BorderColor = COLOR_PRIMARY if Selected else COLOR_BORDER
    pygame.draw.rect(Screen, Background, Rect, border_radius=12)
    pygame.draw.rect(Screen, BorderColor, Rect, width=2 if Selected else 1, border_radius=12)
    if Selected and PulseAlpha > 0:
        glow = pygame.Surface((Rect.width+10, Rect.height+10), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*COLOR_PRIMARY, int(PulseAlpha)), (5, 5, Rect.width, Rect.height), width=3, border_radius=12)
        Screen.blit(glow, (Rect.x-5, Rect.y-5))
    TextSurf = Font.render(Text, True, COLOR_TEXT_MAIN if Selected else COLOR_TEXT_MUTED)
    Screen.blit(TextSurf, TextSurf.get_rect(center=Rect.center))

# ---------- Отрисовка линии пути ----------
def DrawPath(Screen, PathPoints, Color, Width):
    if len(PathPoints) < 2: return
    pygame.draw.lines(Screen, Color, False, PathPoints, Width)

# ---------- Отрисовка контрольных точек ----------
def DrawCheckpointDots(Screen, PathPoints, Step=25):
    for Index in range(0, len(PathPoints), Step):
        pygame.draw.circle(Screen, (50, 58, 82), (int(PathPoints[Index][0]), int(PathPoints[Index][1])), 2)

# ---------- Отрисовка игрока ----------
def DrawPlayer(Screen, PointX, PointY, GlowSurf=None):
    if GlowSurf: Screen.blit(GlowSurf, (int(PointX - 15), int(PointY - 15)))
    pygame.draw.circle(Screen, COLOR_TEXT_MAIN, (int(PointX), int(PointY)), PointRadius)
    pygame.draw.circle(Screen, COLOR_PRIMARY, (int(PointX), int(PointY)), PointRadius, 1)

# ---------- Отрисовка следа ----------
def DrawTrail(Screen, TrailPoints, Color=COLOR_PRIMARY, Width=2):
    if len(TrailPoints) < 2: return
    pygame.draw.lines(Screen, Color, False, TrailPoints, Width)

# ---------- Индикатор изменения управляющего сигнала (не является измерением тремора) ----------
def DrawSignalVariationWidget(Screen, Font, X, Y, SignalVariation):
    Width, Height = 180, 14
    DrawText(Screen, Font, f"ИЗМЕНЕНИЕ СИГНАЛА: {SignalVariation * 100:.1f}/100", X, Y - 22, COLOR_TEXT_MAIN)
    pygame.draw.rect(Screen, (24, 26, 36), (X, Y, Width, Height), border_radius=6)
    FillWidth = int(Width * min(1.0, max(0.0, SignalVariation)))
    if FillWidth > 0:
        pygame.draw.rect(Screen, COLOR_PRIMARY, (X, Y, FillWidth, Height), border_radius=6)
    pygame.draw.rect(Screen, COLOR_BORDER, (X, Y, Width, Height), 1, border_radius=6)

# ---------- Экран выбора HID-устройства ----------
def RunDeviceMenu(Screen, Clock, Font, BigFont):
    Devices = GetDevices()
    SelectedDeviceIndex = 0
    while True:
        for Event in pygame.event.get():
            if Event.type == pygame.QUIT:
                return None
            if Event.type == pygame.KEYDOWN:
                if Event.key == pygame.K_DOWN and Devices:
                    SelectedDeviceIndex = (SelectedDeviceIndex + 1) % len(Devices)
                elif Event.key == pygame.K_UP and Devices:
                    SelectedDeviceIndex = (SelectedDeviceIndex - 1) % len(Devices)
                elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and Devices:
                    return Devices[SelectedDeviceIndex]
                elif Event.key == pygame.K_ESCAPE:
                    return None
        Screen.fill(COLOR_BG)
        PanelRect = pygame.Rect(WindowWidth//2 - 250, WindowHeight//2 - 200, 500, 400)
        DrawPanel(Screen, PanelRect, Radius=24)
        DrawText(Screen, BigFont, "ПОДКЛЮЧЕНИЕ УСТРОЙСТВА", PanelRect.x + 45, PanelRect.y + 40, COLOR_TEXT_MAIN)
        DrawText(Screen, Font, "Используйте стрелки UP / DOWN и ENTER", PanelRect.x + 65, PanelRect.y + 90, COLOR_TEXT_MUTED)
        if not Devices:
            DrawText(Screen, Font, "УСТРОЙСТВА НЕ ОБНАРУЖЕНЫ", PanelRect.x + 110, PanelRect.y + 200, COLOR_ACCENT)
        else:
            for Index in Devices:
                Joy = pygame.joystick.Joystick(Index)
                Joy.init()
                DeviceLabel = f"[{Index}] {Joy.get_name()}"
                DrawModernButton(
                    Screen,
                    Font,
                    pygame.Rect(PanelRect.x + 50, PanelRect.y + 140 + Index * 55, 400, 45),
                    DeviceLabel,
                    Index == SelectedDeviceIndex,
                )
        pygame.display.flip()
        Clock.tick(60)

# ---------- Экран выбора сложности ----------
def RunDifficultyMenu(Screen, Clock, Font, BigFont, Difficulties):
    SelectedIndex = 0
    PulseTime = 0.0
    while True:
        PulseTime += Clock.tick(60) / 1000.0
        PulseAlpha = 40 + math.sin(PulseTime * 5) * 30
        for Event in pygame.event.get():
            if Event.type == pygame.QUIT: return None
            if Event.type == pygame.KEYDOWN:
                if Event.key in (pygame.K_RIGHT, pygame.K_DOWN): SelectedIndex = (SelectedIndex + 1) % len(Difficulties)
                elif Event.key in (pygame.K_LEFT, pygame.K_UP): SelectedIndex = (SelectedIndex - 1) % len(Difficulties)
                elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER): return SelectedIndex
                elif Event.key == pygame.K_ESCAPE: return None
        Screen.fill(COLOR_BG)

        DrawText(Screen, BigFont, "ВЫБЕРИТЕ УРОВЕНЬ СЛОЖНОСТИ", WindowWidth // 2 - 240, 60, COLOR_TEXT_MAIN)
        DrawText(Screen, Font, "Управление стрелками. ENTER — подтвердить выбор. ESC — выход.", WindowWidth // 2 - 290, 110, COLOR_TEXT_MUTED)
        
        CardWidth = 260
        TotalCardsWidth = CardWidth * 3 + 40 * 2
        StartX = (WindowWidth - TotalCardsWidth) // 2
        
        for Index, Difficulty in enumerate(Difficulties):
            Rect = pygame.Rect(StartX + Index * (CardWidth + 40), 220, CardWidth, 300)
            IsSelected = (Index == SelectedIndex)
            DrawPanel(Screen, Rect, Radius=20)
            if IsSelected:
                pygame.draw.rect(Screen, COLOR_PRIMARY, Rect, width=2, border_radius=20)
                glow = pygame.Surface((Rect.width+20, Rect.height+20), pygame.SRCALPHA)
                pygame.draw.rect(glow, (*COLOR_PRIMARY, int(PulseAlpha // 2)), (10, 10, Rect.width, Rect.height), width=4, border_radius=20)
                Screen.blit(glow, (Rect.x-10, Rect.y-10))
            DrawText(Screen, BigFont, Difficulty.Name, Rect.x + 24, Rect.y + 24, COLOR_TEXT_MAIN if IsSelected else COLOR_TEXT_MUTED)
            DrawText(Screen, Font, f"Скорость: {Difficulty.Speed:.0f}", Rect.x + 24, Rect.y + 105, COLOR_TEXT_MAIN)
            DrawText(Screen, Font, f"Допуск:   {Difficulty.Tolerance:.0f}", Rect.x + 24, Rect.y + 142, COLOR_TEXT_MAIN)
            DrawText(Screen, Font, f"Ошибка:   {Difficulty.MissLimitSeconds:.2f}s", Rect.x + 24, Rect.y + 179, COLOR_TEXT_MAIN)
            DrawText(Screen, Font, f"Фигур:    {len(Difficulty.Drawings)}", Rect.x + 24, Rect.y + 225, COLOR_TEXT_MUTED)
        pygame.display.flip()

# ---------- Экран выбора фигуры ----------
def RunDrawingMenu(Screen, Clock, Font, BigFont, Difficulty):
    SelectedIndex = 0
    PulseTime = 0.0
    while True:
        PulseTime += Clock.tick(60) / 1000.0
        PulseAlpha = 40 + math.sin(PulseTime * 5) * 30
        for Event in pygame.event.get():
            if Event.type == pygame.QUIT: return None
            if Event.type == pygame.KEYDOWN:
                if Event.key in (pygame.K_DOWN, pygame.K_RIGHT): SelectedIndex = (SelectedIndex + 1) % len(Difficulty.Drawings)
                elif Event.key in (pygame.K_UP, pygame.K_LEFT): SelectedIndex = (SelectedIndex - 1) % len(Difficulty.Drawings)
                elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER): return SelectedIndex
                elif Event.key == pygame.K_ESCAPE: return -1
        Screen.fill(COLOR_BG)

        PaddingX = int(WindowWidth * 0.06)
        PaddingY = 150
        DrawText(Screen, BigFont, f"УРОВЕНЬ: {Difficulty.Name.upper()}", PaddingX, 55, COLOR_PRIMARY)
        
        LeftPanelWidth = 360
        LeftPanel = pygame.Rect(PaddingX, PaddingY, LeftPanelWidth, WindowHeight - PaddingY - 60)
        DrawPanel(Screen, LeftPanel, Radius=20)
        DrawText(Screen, Font, "СПИСОК ФИГУР", LeftPanel.x + 24, LeftPanel.y + 20, COLOR_TEXT_MAIN)
        for Index, Drawing in enumerate(Difficulty.Drawings):
            DrawModernButton(Screen, Font, pygame.Rect(LeftPanel.x + 24, LeftPanel.y + 65 + Index * 56, LeftPanel.width - 48, 44), Drawing.Name, Index == SelectedIndex, PulseAlpha)
        
        PreviewRect = pygame.Rect(LeftPanel.right + 30, PaddingY, WindowWidth - LeftPanelWidth - PaddingX - 30, WindowHeight - PaddingY - 60)
        DrawPanel(Screen, PreviewRect, Radius=20)
        CurrentDrawing = Difficulty.Drawings[SelectedIndex]
        DrawText(Screen, BigFont, CurrentDrawing.Name, PreviewRect.x + 30, PreviewRect.y + 24, COLOR_TEXT_MAIN)
        
        TracePreviewRect = pygame.Rect(PreviewRect.x + 30, PreviewRect.y + 80, PreviewRect.width - 60, PreviewRect.height - 110)
        pygame.draw.rect(Screen, (12, 13, 18), TracePreviewRect, border_radius=14)
        PreviewPath = BuildPathForDrawing(CurrentDrawing, TracePreviewRect)
        DrawPath(Screen, PreviewPath, (42, 53, 74), 4)
        DrawCheckpointDots(Screen, PreviewPath, Step=max(12, len(PreviewPath) // 25))
        pygame.display.flip()


def CalculatePathLength(Points) -> float:
    if len(Points) < 2:
        return 0.0
    return sum(Distance2D(Points[Index - 1], Points[Index]) for Index in range(1, len(Points)))


def BuildDrawingRoundData(Success, Drawing, Difficulty, TrailPoints, PathPoints, TotalTime, SignalVariation):
    trailLength = CalculatePathLength(TrailPoints)
    idealPathLength = CalculatePathLength(PathPoints)
    deviation = CalculateDeviation(TrailPoints, PathPoints)
    averageSpeed = trailLength / TotalTime if TotalTime > 0 else 0.0
    efficiency = min(100.0, idealPathLength / trailLength * 100.0) if Success and trailLength > 0 else 0.0
    return {
        "round_id": str(uuid.uuid4()),
        "drawing": Drawing.Name,
        "difficulty": Difficulty.Name,
        "outcome": "completed" if Success else "interrupted",
        "duration_seconds": round(max(0.0, TotalTime), 3),
        "trail_length_px": round(trailLength, 3),
        "ideal_path_length_px": round(idealPathLength, 3),
        "mean_deviation_from_path_px": round(deviation, 3),
        "average_cursor_speed_px_per_second": round(averageSpeed, 3),
        "trajectory_efficiency_pct": round(efficiency, 3),
        "control_signal_variability_index_0_100": round(max(0.0, min(1.0, SignalVariation)) * 100.0, 3),
        "control_signal_variability_method": "mean smoothed change in normalized tilt-control signal after rejecting single-frame deltas above 0.18; not a physiological tremor measurement",
        "trail_points_count": len(TrailPoints),
        "target_path_points_count": len(PathPoints),
    }


def SaveDrawingStatistics(sessionStatus, activeRound=None):
    rounds = list(drawingRoundHistory)
    roundsForMetrics = rounds + ([activeRound] if activeRound else [])
    completedRounds = [item for item in roundsForMetrics if item.get("outcome") == "completed"]
    interruptedRounds = [item for item in roundsForMetrics if item.get("outcome") == "interrupted"]
    totalDuration = sum(float(item.get("duration_seconds", 0.0)) for item in roundsForMetrics)
    totalTrailLength = sum(float(item.get("trail_length_px", 0.0)) for item in roundsForMetrics)
    weightedDeviationNumerator = sum(float(item.get("mean_deviation_from_path_px", 0.0)) * max(1, int(item.get("trail_points_count", 0))) for item in roundsForMetrics)
    totalTrailPoints = sum(max(1, int(item.get("trail_points_count", 0))) for item in roundsForMetrics)
    variations = [float(item.get("control_signal_variability_index_0_100", 0.0)) for item in roundsForMetrics]
    completionTimes = [float(item.get("duration_seconds", 0.0)) for item in completedRounds]
    efficiencyValues = [float(item.get("trajectory_efficiency_pct", 0.0)) for item in completedRounds]
    metrics = {
        "drawingAttempts": metric("Попытки рисования", len(roundsForMetrics), "шт."),
        "drawingsCompleted": metric("Успешно выполненные задания", len(completedRounds), "шт."),
        "drawingAttemptsInterrupted": metric("Прерванные попытки", len(interruptedRounds), "шт."),
        "activeGameplaySeconds": metric("Суммарное время заданий", totalDuration, "сек."),
        "trailLengthPx": metric("Суммарная длина траектории", totalTrailLength, "px"),
        "averageCursorSpeedPxPerSecond": metric("Средняя скорость курсора за время заданий", totalTrailLength / totalDuration if totalDuration else 0.0, "px/сек"),
        "averageDeviationFromPathPx": metric("Среднее отклонение от контура", weightedDeviationNumerator / totalTrailPoints if totalTrailPoints else 0.0, "px"),
        "averageControlSignalVariabilityIndex": metric("Средний индекс изменения сигнала управления (условная шкала 0–100)", sum(variations) / len(variations) if variations else 0.0, "баллов из 100"),
        "averageTrajectoryEfficiencyPct": metric("Средняя эффективность траектории", sum(efficiencyValues) / len(efficiencyValues) if efficiencyValues else 0.0, "%"),
    }
    if roundsForMetrics:
        metrics["drawingCompletionRatePct"] = metric("Доля выполненных заданий", len(completedRounds) / len(roundsForMetrics) * 100.0, "%")
    if completionTimes:
        metrics["averageCompletionTimeSeconds"] = metric("Среднее время выполненного задания", sum(completionTimes) / len(completionTimes), "сек.")
        metrics["fastestCompletionTimeSeconds"] = metric("Лучшее время выполнения", min(completionTimes), "сек.")
    settings = {
        "logical_resolution_px": {"width": WindowWidth, "height": WindowHeight},
        "input_filter_strength": InputFilterStrength,
        "input_sampling_rate_hz_target": 120,
        "path_deviation_method": "mean distance from sampled trace points to nearest sampled target-path point",
        "signal_variability_is_diagnostic": False,
        "signal_variability_scale": "0-100 normalized index; not a percentage of accuracy and not a physiological tremor measurement",
        "single_frame_delta_rejection_threshold": 0.18,
    }
    details = {"rounds": drawingRoundHistory}
    if activeRound:
        details["active_round"] = {key: value for key, value in activeRound.items() if key != "started_monotonic"}
    moveUpStatistics.save(sessionStatus, metrics, settings, details)


def FinalizeDrawingStatistics():
    global drawingSessionFinalized
    if drawingSessionFinalized:
        return
    try:
        status = "interrupted" if activeDrawingSnapshot else ("completed" if any(item.get("outcome") == "completed" for item in drawingRoundHistory) else "interrupted")
        if activeDrawingSnapshot:
            interruptedSnapshot = dict(activeDrawingSnapshot)
            interruptedSnapshot["outcome"] = "interrupted"
            if "started_monotonic" in interruptedSnapshot:
                interruptedSnapshot.pop("started_monotonic", None)
            drawingRoundHistory.append(interruptedSnapshot)
            activeRoundForSave = None
        else:
            activeRoundForSave = None
        SaveDrawingStatistics(status, activeRoundForSave)
    except Exception as error:
        print(f"Не удалось сохранить статистику Drawing при выходе: {error}")
    drawingSessionFinalized = True


atexit.register(FinalizeDrawingStatistics)


# ---------- Игровой процесс ----------
def RunGame(Screen, Clock, Font, BigFont, Device, BaselineAccel, Difficulty, Drawing):
    global CurrentSensitivityIdx, PreviousSensitivityButton
    PaddingX = int(WindowWidth * 0.06)
    TraceArea = pygame.Rect(PaddingX, 140, WindowWidth - (PaddingX * 2), WindowHeight - 240)
    PathPoints = BuildPathForDrawing(Drawing, TraceArea)

    PointX, PointY = float(PathPoints[0][0]), float(PathPoints[0][1])
    Reader = JoystickInputReader(Device)
    P_System = ParticleSystem()
    PlayerGlow = create_glow_surface(15, COLOR_PRIMARY, max_alpha=120) 

    BaselinePitch, BaselineRoll = 0.0, 0.0
    if BaselineAccel is not None:
        Ax, Ay, Az = BaselineAccel
        SafeAz = Az if abs(Az) > 1e-6 else 1e-6
        BaselinePitch = math.atan2(-Ax, math.sqrt(Ay * Ay + Az * Az))
        BaselineRoll = math.atan2(Ay, SafeAz)

    ControlX, ControlY = 0.0, 0.0
    ControlGain = 2.7
    TrailPoints = [(PointX, PointY)]
    TrailStepDistance = 1.0
    CurrentIndex = 1

    LastRawX, LastRawY = 0.0, 0.0
    TremorHistory = []
    AllSessionTremor = []  
    DisplayTremor = 0.0
    
    StartTime = time.time()
    StartMonotonic = time.monotonic()
    global activeDrawingSnapshot, drawingLastStatsSaveTime
    activeRoundId = str(uuid.uuid4())
    activeDrawingSnapshot = {
        "round_id": activeRoundId,
        "drawing": Drawing.Name,
        "difficulty": Difficulty.Name,
        "outcome": "in_progress",
        "duration_seconds": 0.0,
        "trail_length_px": 0.0,
        "ideal_path_length_px": CalculatePathLength(PathPoints),
        "mean_deviation_from_path_px": 0.0,
        "average_cursor_speed_px_per_second": 0.0,
        "trajectory_efficiency_pct": 0.0,
        "control_signal_variability_index_0_100": 0.0,
        "trail_points_count": len(TrailPoints),
        "target_path_points_count": len(PathPoints),
        "control_signal_variability_method": "mean smoothed change in normalized tilt-control signal after rejecting single-frame deltas above 0.18; not a physiological tremor measurement",
    }
    drawingLastStatsSaveTime = time.monotonic()

    while True:
        DeltaTime = Clock.tick(120) / 1000.0
        if DeltaTime <= 0.0: DeltaTime = 1.0 / 120.0

        for Event in pygame.event.get():
            if Event.type == pygame.QUIT: return False, TrailPoints, PathPoints, max(0.0, time.time() - StartTime), (sum(AllSessionTremor) / len(AllSessionTremor) if AllSessionTremor else 0.0)
            if Event.type == pygame.KEYDOWN and Event.key == pygame.K_ESCAPE: return False, TrailPoints, PathPoints, max(0.0, time.time() - StartTime), (sum(AllSessionTremor) / len(AllSessionTremor) if AllSessionTremor else 0.0)

        Accel, Gyro, ConnectionOk = Reader.read_latest()
        if not ConnectionOk: return False, TrailPoints, PathPoints, max(0.0, time.time() - StartTime), (sum(AllSessionTremor) / len(AllSessionTremor) if AllSessionTremor else 0.0)

        SensitivityButton = Device.get_button(3) if Device.get_numbuttons() > 3 else 0
        if SensitivityButton and not PreviousSensitivityButton:
            CurrentSensitivityIdx = (CurrentSensitivityIdx + 1) % len(SENSITIVITY_NAMES)
            print(f"Чувствительность: {SENSITIVITY_NAMES[CurrentSensitivityIdx]}")
        PreviousSensitivityButton = SensitivityButton

        if Accel is not None:
            Ax, Ay, Az = Accel
            Length = math.sqrt(Ax * Ax + Ay * Ay + Az * Az)
            if Length > 1e-6:
                Nx, Ny, Nz = Ax / Length, Ay / Length, Az / Length
                Pitch = math.atan2(-Nx, math.sqrt(Ny * Ny + Nz * Nz))
                Roll = math.atan2(Ny, Nz if abs(Nz) > 1e-6 else 1e-6)

                DiffX = Roll - BaselineRoll
                DiffY = Pitch - BaselinePitch

                if SwapAxes: DiffX, DiffY = DiffY, DiffX

                TargetX = Clamp(DiffX * ControlGain * RightSign, -1.0, 1.0)
                TargetY = Clamp(DiffY * ControlGain * ForwardSign, -1.0, 1.0)

                if abs(TargetX) < DeadZone: TargetX = 0.0
                if abs(TargetY) < DeadZone: TargetY = 0.0

                RawDelta = math.hypot(TargetX - LastRawX, TargetY - LastRawY)
                LastRawX, LastRawY = TargetX, TargetY
                
                if RawDelta > 0.18: RawDelta = 0.0
                
                TremorHistory.append(RawDelta)
                if len(TremorHistory) > 25: TremorHistory.pop(0)
                
                CalculatedTremor = (sum(TremorHistory) / len(TremorHistory)) * 7.5
                DisplayTremor = DisplayTremor + (CalculatedTremor - DisplayTremor) * 0.1
                DisplayTremor = Clamp(DisplayTremor, 0.0, 1.0)
                AllSessionTremor.append(DisplayTremor)

                ControlX = ControlX + (TargetX - ControlX) * InputFilterStrength
                ControlY = ControlY + (TargetY - ControlY) * InputFilterStrength

        SensitivityMultiplier = SENSITIVITY_PRESETS[SENSITIVITY_NAMES[CurrentSensitivityIdx]]
        MoveX = ControlX * Difficulty.Speed * DeltaTime * SensitivityMultiplier
        MoveY = ControlY * Difficulty.Speed * DeltaTime * SensitivityMultiplier

        if abs(MoveX) > 0.1 or abs(MoveY) > 0.1:
            P_System.emit(PointX, PointY, COLOR_PRIMARY, count=1)

        PointX = Clamp(PointX + MoveX, PointRadius + TraceArea.x, TraceArea.right - PointRadius)
        PointY = Clamp(PointY + MoveY, PointRadius + TraceArea.y, TraceArea.bottom - PointRadius)

        NewPoint = (PointX, PointY)
        if Distance2D(NewPoint, TrailPoints[-1]) >= TrailStepDistance:
            TrailPoints.append(NewPoint)

        while CurrentIndex < len(PathPoints) and Distance2D(NewPoint, PathPoints[CurrentIndex]) <= Difficulty.Tolerance:
            P_System.emit(PathPoints[CurrentIndex][0], PathPoints[CurrentIndex][1], COLOR_SUCCESS, count=6)
            CurrentIndex += 1

        P_System.update(DeltaTime)
        Screen.fill(COLOR_BG)

        DrawText(Screen, BigFont, Drawing.Name.upper(), PaddingX, 25, COLOR_TEXT_MAIN)
        DrawText(Screen, Font, f"Сложность: {Difficulty.Name}", PaddingX, 68, COLOR_TEXT_MUTED)
        DrawText(Screen, Font,
                 f"Чувствительность: {SENSITIVITY_NAMES[CurrentSensitivityIdx]} ",
                 PaddingX, 105, COLOR_PRIMARY)
        
        ProgressPercent = 100.0 * CurrentIndex / max(1, len(PathPoints) - 1)
        DrawText(Screen, Font, f"Прогресс: {ProgressPercent:5.1f}%", WindowWidth - 450, 68, COLOR_PRIMARY)
        DrawSignalVariationWidget(Screen, Font, WindowWidth - 240, 65, DisplayTremor)

        pygame.draw.rect(Screen, (12, 13, 18), TraceArea, border_radius=20)
        pygame.draw.rect(Screen, COLOR_BORDER, TraceArea, width=1, border_radius=20)

        DrawPath(Screen, PathPoints, (35, 42, 58), 6)                   
        DrawPath(Screen, PathPoints[:CurrentIndex], COLOR_SUCCESS, 5)   
        DrawTrail(Screen, TrailPoints, COLOR_PRIMARY, 2)                
        DrawCheckpointDots(Screen, PathPoints, Step=max(14, len(PathPoints) // 20))

        if CurrentIndex < len(PathPoints):
            pygame.draw.circle(Screen, COLOR_ACCENT, (int(PathPoints[CurrentIndex][0]), int(PathPoints[CurrentIndex][1])), 6)

        P_System.draw(Screen)
        DrawPlayer(Screen, PointX, PointY, PlayerGlow)

        ProgressBarRect = pygame.Rect(PaddingX, WindowHeight - 65, WindowWidth - (PaddingX * 2), 12)
        pygame.draw.rect(Screen, (24, 26, 36), ProgressBarRect, border_radius=6)
        FillWidth = int(ProgressBarRect.width * min(1.0, CurrentIndex / max(1, len(PathPoints) - 1)))
        if FillWidth > 0: pygame.draw.rect(Screen, COLOR_SUCCESS, (ProgressBarRect.x, ProgressBarRect.y, FillWidth, ProgressBarRect.height), border_radius=6)
        pygame.draw.rect(Screen, COLOR_BORDER, ProgressBarRect, 1, border_radius=6)

        pygame.display.flip()
        TotalTime = max(0.0, time.time() - StartTime)
        AvgTremor = sum(AllSessionTremor) / len(AllSessionTremor) if AllSessionTremor else 0.0
        statsNow = time.monotonic()
        IsComplete = CurrentIndex >= len(PathPoints)
        if IsComplete or statsNow - drawingLastStatsSaveTime >= 2.0:
            activeDrawingSnapshot = BuildDrawingRoundData(IsComplete, Drawing, Difficulty, TrailPoints, PathPoints, TotalTime, AvgTremor)
            activeDrawingSnapshot["round_id"] = activeRoundId
            activeDrawingSnapshot["outcome"] = "completed" if IsComplete else "in_progress"
            activeDrawingSnapshot["started_monotonic"] = StartMonotonic
            if not IsComplete:
                SaveDrawingStatistics("interrupted", activeDrawingSnapshot)
                drawingLastStatsSaveTime = statsNow
        if IsComplete:
            return True, TrailPoints, PathPoints, TotalTime, AvgTremor

# ---------- Экран исходного рисунка ----------
def ShowPureDrawingScreen(Screen, Clock, Font, BigFont, TrailPoints, PathPoints, DrawingName):
    while True:
        for Event in pygame.event.get():
            if Event.type == pygame.QUIT: return False
            if Event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN): return True

        Screen.fill(COLOR_BG)
        DrawText(Screen, BigFont, f"ВАШ РИСУНОК: {DrawingName.upper()}", 50, 30, COLOR_PRIMARY)
        DrawText(Screen, Font, "Нажмите любую клавишу для возврата к статистике", 50, 75, COLOR_TEXT_MUTED)
        
        DrawPath(Screen, PathPoints, (35, 42, 58), 4) 
        DrawTrail(Screen, TrailPoints, COLOR_PRIMARY, 3) 
        
        pygame.display.flip()
        Clock.tick(60)

# ---------- Расчет отклонения ----------
def CalculateDeviation(TrailPoints, PathPoints) -> float:
    if not TrailPoints or not PathPoints: return 0.0
    TotalDist = 0.0
    for Tp in TrailPoints:
        MinD = min(math.hypot(Tp[0] - Pp[0], Tp[1] - Pp[1]) for Pp in PathPoints)
        TotalDist += MinD
    return TotalDist / len(TrailPoints)

# ---------- Экран результатов ----------
def RunResultScreen(Screen, Clock, Font, BigFont, Success, Drawing, Difficulty, TrailPoints, PathPoints, TotalTime, AvgTremor):
    SelectedIndex = 0
    ButtonsText = ["Выбрать фигуру", "Посмотреть рисунок", "Вернуться в меню"]
    
    Deviation = CalculateDeviation(TrailPoints, PathPoints)
    PulseTime = 0.0

    while True:
        PulseTime += Clock.tick(60) / 1000.0
        PulseAlpha = 40 + math.sin(PulseTime * 5) * 30
        
        for Event in pygame.event.get():
            if Event.type == pygame.QUIT: return "exit"
            if Event.type == pygame.KEYDOWN:
                if Event.key in (pygame.K_DOWN, pygame.K_RIGHT): SelectedIndex = (SelectedIndex + 1) % len(ButtonsText)
                elif Event.key in (pygame.K_UP, pygame.K_LEFT): SelectedIndex = (SelectedIndex - 1) % len(ButtonsText)
                elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if SelectedIndex == 0: return "select_drawing"
                    elif SelectedIndex == 1: 
                        if not ShowPureDrawingScreen(Screen, Clock, Font, BigFont, TrailPoints, PathPoints, Drawing.Name):
                            return "exit"
                    elif SelectedIndex == 2: return "exit"
                elif Event.key == pygame.K_ESCAPE: return "select_drawing"

        Screen.fill(COLOR_BG)

        BoxWidth = 600
        BoxHeight = 590
        BoxRect = pygame.Rect((WindowWidth - BoxWidth) // 2, (WindowHeight - BoxHeight) // 2, BoxWidth, BoxHeight)
        DrawPanel(Screen, BoxRect, Radius=24)
        
        TitleColor = COLOR_SUCCESS if Success else COLOR_PRIMARY
        DrawText(Screen, BigFont, "ЗАДАНИЕ ВЫПОЛНЕНО" if Success else "ПОПЫТКА ПРЕРВАНА", BoxRect.x + 50, BoxRect.y + 45, TitleColor)
        
        TrailLength = CalculatePathLength(TrailPoints)
        IdealPathLength = CalculatePathLength(PathPoints)
        AverageCursorSpeed = TrailLength / TotalTime if TotalTime > 0 else 0.0
        TrajectoryEfficiency = min(100.0, IdealPathLength / TrailLength * 100.0) if Success and TrailLength > 0 else None
        StatY = BoxRect.y + 112
        StatLines = [
            (f"Фигура: {Drawing.Name}", COLOR_TEXT_MAIN),
            (f"Сложность: {Difficulty.Name}", COLOR_TEXT_MUTED),
            (f"Время задания: {TotalTime:.2f} сек.", COLOR_TEXT_MAIN),
            (f"Индекс изменения сигнала (0–100): {AvgTremor * 100:.1f}", COLOR_TEXT_MAIN),
            (f"Среднее отклонение от контура: {Deviation:.1f} px", COLOR_PRIMARY),
            (f"Длина траектории курсора: {TrailLength:.0f} px", COLOR_TEXT_MAIN),
            (f"Средняя скорость курсора: {AverageCursorSpeed:.1f} px/сек", COLOR_TEXT_MAIN),
            (f"Эффективность траектории: {TrajectoryEfficiency:.1f}%" if TrajectoryEfficiency is not None else "Эффективность траектории: —", COLOR_PRIMARY),
        ]
        for Index, (LineText, LineColor) in enumerate(StatLines):
            DrawText(Screen, Font, LineText, BoxRect.x + 42, StatY + Index * 31, LineColor)

        BtnWidth = BoxRect.width - 100
        for Index, Text in enumerate(ButtonsText):
            BtnRect = pygame.Rect(BoxRect.x + 50, BoxRect.y + 420 + Index * 48, BtnWidth, 40)
            DrawModernButton(Screen, Font, BtnRect, Text, Index == SelectedIndex, PulseAlpha if Index == SelectedIndex else 0)

        pygame.display.flip()

# ---------- Точка входа ----------
def main():
    global WindowWidth, WindowHeight, activeDrawingSnapshot
    pygame.init()
    pygame.joystick.init()

    ShowDevices()
    Devices = GetDevices()
    if not Devices:
        return

    Screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    WindowWidth, WindowHeight = Screen.get_size()

    pygame.display.set_caption("Cyber Tilt Trace")
    Clock = pygame.time.Clock()
    Font = pygame.font.SysFont("Segoe UI", 20, bold=True)
    BigFont = pygame.font.SysFont("Segoe UI", 30, bold=True)

    DeviceIndex = RunDeviceMenu(Screen, Clock, Font, BigFont)
    if DeviceIndex is None:
        return

    try:
        Device = OpenJoystick(DeviceIndex)
    except Exception:
        return

    try:
        if not WaitForReady(Device):
            return

        Reader = JoystickInputReader(Device)
        AccelSamples = []
        CalibrationStart = time.time()
        while len(AccelSamples) < 60 and time.time() - CalibrationStart < 5.0:
            Accel, _, Ok = Reader.read_latest()
            if not Ok:
                return
            if Accel is not None:
                AccelSamples.append(Accel)
            time.sleep(0.02)
        if not AccelSamples:
            return

        BaselineAccel = (
            sum(S[0] for S in AccelSamples) / len(AccelSamples),
            sum(S[1] for S in AccelSamples) / len(AccelSamples),
            sum(S[2] for S in AccelSamples) / len(AccelSamples),
        )

        Difficulties = BuildDifficultyData()

        while True:
            DifficultyIndex = RunDifficultyMenu(Screen, Clock, Font, BigFont, Difficulties)
            if DifficultyIndex is None:
                break
            Difficulty = Difficulties[DifficultyIndex]

            SelectNewDifficulty = False
            while not SelectNewDifficulty:
                DrawingIndex = RunDrawingMenu(Screen, Clock, Font, BigFont, Difficulty)
                if DrawingIndex is None or DrawingIndex == -1: 
                    SelectNewDifficulty = True 
                    break

                Success, TrailPoints, PathPoints, TotalTime, AvgTremor = RunGame(
                    Screen, Clock, Font, BigFont, Device, BaselineAccel, Difficulty, Difficulty.Drawings[DrawingIndex]
                )

                RoundData = BuildDrawingRoundData(
                    Success, Difficulty.Drawings[DrawingIndex], Difficulty,
                    TrailPoints, PathPoints, TotalTime, AvgTremor
                )
                RoundData["round_id"] = (activeDrawingSnapshot or {}).get("round_id", str(uuid.uuid4()))
                RoundData.pop("started_monotonic", None)
                if not Success:
                    # The attempt was ended by Escape, window close, or controller disconnect.
                    RoundData["outcome"] = "interrupted"
                drawingRoundHistory.append(RoundData)
                activeDrawingSnapshot = None
                SaveDrawingStatistics("interrupted")

                ActionResult = RunResultScreen(
                    Screen, Clock, Font, BigFont, Success, Difficulty.Drawings[DrawingIndex], Difficulty, TrailPoints, PathPoints, TotalTime, AvgTremor
                )

                if ActionResult == "exit":
                    return 
                elif ActionResult == "select_drawing":
                    SelectNewDifficulty = True 
                    break
    finally:
        try:
            Device.quit()
        except Exception:
            pass
        pygame.quit()

if __name__ == "__main__":
    try:
        main()
    finally:
        FinalizeDrawingStatistics()