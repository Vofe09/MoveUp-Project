import os
import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GLU import *
import math
import random
import time

# Serial support is optional: the game now reads the controller over USB HID.
# These imports only support the legacy COM-port helper functions retained below.
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

# =========================
# Настройки и конфигурация
# =========================

def load_config():
    return {
        "window": {"width": 1024, "height": 768, "title": "Интерактивная реабилитация: Цифровая Квартира"},
        "rehabilitation_settings": {"max_allowed_tremor": 4.0, "pour_angle_trigger": 15.0},
        "kitchen_tasks": [
            {"id":"tea","name":"Налить и перенести чай","progress":0,"completed":False, "description": "Поднимите чайник наклоном Pitch, налейте наклоном Roll и перенесите чашку на стол."},
            {"id":"bread","name":"Нарезать свежий хлеб","progress":0,"completed":False, "description": "Взмахивайте датчиком вверх-вниз для имитации резки ножом по вертикали."},
            {"id":"soup","name":"Помешать суп в кастрюле","progress":0,"completed":False, "description": "Совершайте плавные круговые движения кистью."},
            {"id":"wipe","name":"Протереть обеденный стол","progress":0,"completed":False, "description": "Двигайте рукой влево-вправо и вперед-назад, чтобы стереть грязь со стола."},
            {"id":"pancake","name":"Перевернуть блинчик","progress":0,"completed":False, "description": "Дождитесь, пока блин поджарится, и резко взмахните датчиком вверх (Pitch)."}
        ],
        "hallway_tasks": [
            {"id":"door_key","name":"Закрыть входную дверь","progress":0,"completed":False, "description": "Поворачивайте кисть влево и вправо (Roll), чтобы провернуть ключ в замочной скважине."},
            {"id":"clean_mirror","name":"Промыть зеркало","progress":0,"completed":False, "description": "Двигайте рукой влево-вправо (Roll) и вверх-вниз (Pitch), чтобы очистить вертикальное зеркало губкой."},
            {"id":"fix_mat","name":"Расправить коврик у двери","progress":0,"completed":False, "description": "Двигайте рукой вперед-назад (Pitch), чтобы аккуратно разгладить и выровнять придверный коврик."},
            {"id":"hang_jacket","name":"Повесить куртку на крючок","progress":0,"completed":False, "description": "Плавно поднимите руку вверх (Pitch), чтобы зацепить петельку куртки за крючок вешалки."},
            {"id":"lamp_switch","name":"Включить настенное бра","progress":0,"completed":False, "description": "Сделайте уверенное движение рукой вниз (Pitch), чтобы потянуть за веревочный выключатель бра."}
        ],
        "bedroom_tasks": [
            {"id":"make_bed","name":"Заправить кровать","progress":0,"completed":False, "description": "Выполняйте широкие махи рукой влево-вправо (Roll), чтобы разгладить покрывало."},
            {"id":"fluff_pillow","name":"Взбить подушку","progress":0,"completed":False, "description": "Делайте частые и резкие движения рукой вверх-вниз (Pitch), возвращая её на место."},
            {"id":"open_box","name":"Открыть шкатулку","progress":0,"completed":False, "description": "Медленно наклоняйте кисть назад (Pitch), удерживая минимальный тремор кисти."},
            {"id":"set_alarm","name":"Поставить будильник","progress":0,"completed":False, "description": "Наклонами Pitch/Roll переместите будильник из руки на прикроватную тумбочку."},
            {"id":"stack_book","name":"Сложить книгу в стопку","progress":0,"completed":False, "description": "Держите руку идеально горизонтально (Roll около нуля), чтобы ровно перенести книгу."}
        ]
    }

config = load_config()

# USB HID axes used by the updated MoveUp firmware.
# These indices and directions match the working Drawing.py mapping:
# Ry increases when tilting away and decreases when tilting toward the user.
# Rz increases when tilting left and decreases when tilting right.
ControllerRyAxis = 4
ControllerRzAxis = 5
ControllerTiltRangeDegrees = 45.0  # Firmware maps a +/-45-degree tilt to the HID axis range.
ControllerPitchSign = -1.0
ControllerRollSign = 1.0

# Task-specific travel sensitivity. Movement along world Z is visually less
# obvious in the table camera and the mirror's usable Z span is narrower.
TableWipeDepthSensitivity = 0.05
MirrorWipeHorizontalSensitivity = 0.05

ser = None  # Retained only for the legacy serial helper functions below.
calibrated_offset_pitch = 0.0
calibrated_offset_roll = 0.0


# =========================
# Работа с портами
# =========================

def GetPorts():
    Ports = list(list_ports.comports())

    Ports.sort(
        key=lambda Port: (
            not (
                "USB" in Port.hwid
                or "ACM" in Port.device
                or "USB" in Port.device
            ),
            Port.device,
        )
    )

    return [Port.device for Port in Ports]


# =========================
# Калибровка датчика
# =========================

def CalibrateSensor():
    global calibrated_offset_pitch
    global calibrated_offset_roll

    print("Калибровка MPU-6500...")

    time.sleep(0.5)

    SamplesPitch = []
    SamplesRoll = []

    Start = time.time()

    while time.time() - Start < 1.5:

        if ser.in_waiting:

            try:
                Line = ser.readline().decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                Tokens = Line.split(",")

                if len(Tokens) >= 9:

                    ax = float(Tokens[7])
                    ay = float(Tokens[8])

                    Pitch = math.atan2(
                        ay,
                        1.0
                    ) * 180 / math.pi

                    Roll = math.atan2(
                        -ax,
                        1.0
                    ) * 180 / math.pi

                    SamplesPitch.append(Pitch)
                    SamplesRoll.append(Roll)

            except:
                pass

        time.sleep(0.01)

    if SamplesPitch:

        calibrated_offset_pitch = sum(SamplesPitch) / len(SamplesPitch)
        calibrated_offset_roll = sum(SamplesRoll) / len(SamplesRoll)

    print("Калибровка завершена.")


# =========================
# Подключение к Serial
# =========================

def OpenSerial(PortName):
    global ser

    ser = serial.Serial(
        PortName,
        115200,
        timeout=0.01
    )

    CalibrateSensor()

# =========================
# Экран выбора порта
# =========================

def RunPortMenu():

    pygame.init()

    Width = 900
    Height = 600

    Screen = pygame.display.set_mode((Width, Height))
    pygame.display.set_caption("Выбор COM-порта")

    BigFont = pygame.font.SysFont("Arial", 42, bold=True)
    Font = pygame.font.SysFont("Arial", 28)

    Selected = 0
    Clock = pygame.time.Clock()

    while True:

        Ports = GetPorts()

        if Selected >= len(Ports):
            Selected = 0

        for Event in pygame.event.get():

            if Event.type == pygame.QUIT:
                return None

            if Event.type == pygame.KEYDOWN:

                if Event.key == pygame.K_ESCAPE:
                    return None

                elif Event.key == pygame.K_UP:

                    if Ports:
                        Selected = (Selected - 1) % len(Ports)

                elif Event.key == pygame.K_DOWN:

                    if Ports:
                        Selected = (Selected + 1) % len(Ports)

                elif Event.key == pygame.K_F5:
                    pass

                elif Event.key == pygame.K_RETURN:

                    if Ports:
                        return Ports[Selected]

        Screen.fill((18, 25, 35))

        Title = BigFont.render(
            "Выберите порт Arduino",
            True,
            (255, 255, 255)
        )

        Screen.blit(Title, (60, 40))

        Hint = Font.render(
            "↑ ↓ - выбрать    Enter - подтвердить    F5 - обновить",
            True,
            (180, 180, 180)
        )

        Screen.blit(Hint, (60, 100))

        if not Ports:

            Txt = Font.render(
                "Порты не найдены",
                True,
                (255, 120, 120)
            )

            Screen.blit(Txt, (80, 180))

        else:

            for i, Port in enumerate(Ports):

                Rect = pygame.Rect(
                    60,
                    170 + i * 55,
                    500,
                    45
                )

                if i == Selected:

                    pygame.draw.rect(
                        Screen,
                        (0, 170, 255),
                        Rect,
                        border_radius=8
                    )

                    Color = (255, 255, 255)

                else:

                    pygame.draw.rect(
                        Screen,
                        (45, 55, 70),
                        Rect,
                        border_radius=8
                    )

                    Color = (200, 200, 200)

                Txt = Font.render(
                    Port,
                    True,
                    Color
                )

                Screen.blit(
                    Txt,
                    (Rect.x + 15, Rect.y + 9)
                )

        pygame.display.flip()

        Clock.tick(60)

# =========================
# Частицы жидкости и эффекты
# =========================

class LiquidParticle:
# =========================
# Создание частицы
# =========================

    def __init__(self, x, y, z, color, vx=None, vy=None, vz=None, gravity=0.004, fade=0.025, size=0.02):
        self.x, self.y, self.z = x, y, z
        self.vx = vx if vx is not None else random.uniform(-0.01, 0.01)
        self.vy = vy if vy is not None else random.uniform(-0.005, 0.0)
        self.vz = vz if vz is not None else random.uniform(-0.01, 0.01)
        self.gravity = gravity
        self.life, self.fade, self.color, self.size = 1.0, fade, color, size

# =========================
# Обновление частицы
# =========================

    def update(self):
        self.vy -= self.gravity
        self.x += self.vx; self.y += self.vy; self.z += self.vz
        self.life -= self.fade

# =========================
# Отрисовка частицы
# =========================

    def draw(self):
        glPushMatrix()
        glTranslatef(self.x, self.y, self.z)
        glColor4f(self.color[0], self.color[1], self.color[2], self.life)
        s = self.size
        glBegin(GL_QUADS)
        glVertex3f(-s,-s,s); glVertex3f(s,-s,s); glVertex3f(s,s,s); glVertex3f(-s,s,s)
        glVertex3f(-s,-s,-s); glVertex3f(-s,s,-s); glVertex3f(s,s,-s); glVertex3f(s,-s,-s)
        glVertex3f(-s,s,-s); glVertex3f(-s,s,s); glVertex3f(s,s,s); glVertex3f(s,s,-s)
        glVertex3f(-s,-s,-s); glVertex3f(s,-s,-s); glVertex3f(s,-s,s); glVertex3f(-s,-s,s)
        glEnd()
        glPopMatrix()

# =========================
# 3D-геометрия: куб
# =========================

def draw_solid_cube(x, y, z, dx, dy, dz, color, tex_id=None, shine=False):
    glPushMatrix()
    glTranslatef(x, y, z)
    if shine:
        glMaterialfv(GL_FRONT, GL_SPECULAR, (1.0, 1.0, 1.0, 1.0))
        glMaterialf(GL_FRONT, GL_SHININESS, 50.0)
    else:
        glMaterialfv(GL_FRONT, GL_SPECULAR, (0.0, 0.0, 0.0, 1.0))

    if tex_id:
        glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, tex_id)
        glColor3f(1.0, 1.0, 1.0)
    else:
        if len(color) == 4: glColor4f(*color)
        else: glColor3f(*color)

    hx, hy, hz = dx/2, dy/2, dz/2
    glBegin(GL_QUADS)
    glNormal3f(0,0,1); glTexCoord2f(0,0); glVertex3f(-hx,-hy,hz); glTexCoord2f(1,0); glVertex3f(hx,-hy,hz); glTexCoord2f(1,1); glVertex3f(hx,hy,hz); glTexCoord2f(0,1); glVertex3f(-hx,hy,hz)
    glNormal3f(0,0,-1); glTexCoord2f(0,0); glVertex3f(-hx,-hy,-hz); glTexCoord2f(0,1); glVertex3f(-hx,hy,-hz); glTexCoord2f(1,1); glVertex3f(hx,hy,-hz); glTexCoord2f(1,0); glVertex3f(hx,-hy,-hz)
    glNormal3f(0,1,0); glTexCoord2f(0,0); glVertex3f(-hx,hy,-hz); glTexCoord2f(0,1); glVertex3f(-hx,hy,hz); glTexCoord2f(1,1); glVertex3f(hx,hy,hz); glTexCoord2f(1,0); glVertex3f(-hx,hy,-hz)
    glNormal3f(0,-1,0); glTexCoord2f(0,0); glVertex3f(-hx,-hy,-hz); glTexCoord2f(1,0); glVertex3f(hx,-hy,-hz); glTexCoord2f(1,1); glVertex3f(hx,hy,hz); glTexCoord2f(0,1); glVertex3f(-hx,-hy,hz)
    glNormal3f(1,0,0); glTexCoord2f(0,0); glVertex3f(hx,-hy,-hz); glTexCoord2f(1,0); glVertex3f(hx,hy,-hz); glTexCoord2f(1,1); glVertex3f(hx,hy,hz); glTexCoord2f(0,1); glVertex3f(hx,-hy,-hz)
    glNormal3f(-1,0,0); glTexCoord2f(0,0); glVertex3f(-hx,-hy,-hz); glTexCoord2f(1,0); glVertex3f(-hx,-hy,hz); glTexCoord2f(1,1); glVertex3f(-hx,hy,hz); glTexCoord2f(0,1); glVertex3f(-hx,-hy,-hz)
    glEnd()
    if tex_id: glDisable(GL_TEXTURE_2D)
    glPopMatrix()

# =========================
# 3D-геометрия: цилиндр
# =========================

def draw_solid_cylinder(x, y, z, r, h, color, tex_id=None, rx=0, ry=0, rz=0, shine=False):
    glPushMatrix()
    glTranslatef(x, y, z)
    if rx or ry or rz:
        glRotatef(rx, 1, 0, 0); glRotatef(ry, 0, 1, 0); glRotatef(rz, 0, 0, 1)
    glRotatef(-90, 1, 0, 0)
    if shine:
        glMaterialfv(GL_FRONT, GL_SPECULAR, (1.0, 1.0, 1.0, 1.0))
        glMaterialf(GL_FRONT, GL_SHININESS, 50.0)
    else:
        glMaterialfv(GL_FRONT, GL_SPECULAR, (0.0, 0.0, 0.0, 1.0))

    if tex_id:
        glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, tex_id)
        glColor3f(1.0, 1.0, 1.0)
    else: glColor3f(*color)

    slices = 20
    glBegin(GL_QUAD_STRIP)
    for i in range(slices + 1):
        u = i / slices
        angle = i * 2.0 * math.pi / slices
        nx, nz = math.cos(angle), math.sin(angle)
        glNormal3f(nx, nz, 0)
        glTexCoord2f(u, 0); glVertex3f(r * nx, r * nz, -h/2)
        glTexCoord2f(u, 1); glVertex3f(r * nx, r * nz, h/2)
    glEnd()
    if tex_id: glDisable(GL_TEXTURE_2D)
    
    glColor3f(*color)
    for side in [-h/2, h/2]:
        glBegin(GL_TRIANGLE_FAN)
        glNormal3f(0, 0, 1 if side > 0 else -1)
        glVertex3f(0, 0, side)
        for i in range(slices + 1):
            angle = (i if side > 0 else -i) * 2.0 * math.pi / slices
            glVertex3f(r * math.cos(angle), r * math.sin(angle), side)
        glEnd()
    glPopMatrix()

# =========================
# 3D-объект: чайник
# =========================

def draw_detailed_kettle(x, y, z, f_roll, tex):
    glPushMatrix()
    glTranslatef(x, y, z); glRotatef(f_roll, 0, 0, 1); glTranslatef(0, 0.225, 0)
    draw_solid_cylinder(0, 0, 0, 0.22, 0.45, (0.8, 0.82, 0.85), tex, shine=True)
    draw_solid_cylinder(0, 0.24, 0, 0.18, 0.04, (0.15, 0.15, 0.15))
    draw_solid_cylinder(0, 0.27, 0, 0.04, 0.04, (0.85, 0.15, 0.15))
    draw_solid_cube(0.26, 0.0, 0, 0.06, 0.35, 0.05, (0.15, 0.15, 0.15))
    draw_solid_cylinder(-0.24, 0.1, 0, 0.04, 0.22, (0.8, 0.82, 0.85), None, 0, 0, -45, shine=True)
    glPopMatrix()

# =========================
# 3D-объект: кружка
# =========================

def draw_detailed_mug(x, y, z, liquid_level, tex):
    glPushMatrix()
    glTranslatef(x, y, z); glTranslatef(0, 0.14, 0)
    draw_solid_cylinder(0, 0, 0, 0.13, 0.28, (0.85, 0.15, 0.15), tex)
    draw_solid_cube(0.16, 0.0, 0, 0.04, 0.16, 0.04, (0.85, 0.15, 0.15))
    if liquid_level > 0:
        fill_height = -0.13 + (0.24 * min(liquid_level / 40.0, 1.0))
        draw_solid_cylinder(0, fill_height, 0, 0.11, 0.01, (0.45, 0.22, 0.05))
    glPopMatrix()

# =========================
# 3D-объект: нож
# =========================

def draw_detailed_knife(x, y, z, tex_metal):
    glPushMatrix()
    glTranslatef(x, y, z)
    draw_solid_cube(0.22, 0, 0, 0.16, 0.04, 0.05, (0.35, 0.2, 0.1))
    draw_solid_cube(0.0, -0.01, 0, 0.28, 0.06, 0.01, (0.85, 0.87, 0.9), tex_metal, shine=True)
    glPopMatrix()

# =========================
# 3D-объект: хлеб
# =========================

def draw_detailed_bread(x, y, z, tex_bread):
    glPushMatrix()
    glTranslatef(x, y, z)
    draw_solid_cube(0, 0, 0, 0.6, 0.25, 0.4, (0.55, 0.35, 0.15), tex_bread)
    draw_solid_cube(0, -0.14, 0, 0.8, 0.03, 0.55, (0.8, 0.65, 0.45))
    glPopMatrix()

# =========================
# 3D-объект: стул
# =========================

def draw_chair(x, y, z, rot_y, tex_wood):
    glPushMatrix()
    glTranslatef(x, y, z); glRotatef(rot_y, 0, 1, 0)
    draw_solid_cube(0, 0.5, 0, 0.5, 0.04, 0.5, (0.4, 0.25, 0.15), tex_wood)
    draw_solid_cube(0, 0.95, -0.23, 0.46, 0.9, 0.04, (0.4, 0.25, 0.15), tex_wood)
    for lx, lz in [(-0.22, -0.22), (0.22, -0.22), (-0.22, 0.22), (0.22, 0.22)]:
        draw_solid_cube(lx, 0.25, lz, 0.04, 0.5, 0.04, (0.25, 0.15, 0.1), tex_wood)
    glPopMatrix()


# =========================
# Выбор USB HID-контроллера MoveUp
# =========================

def RunControllerMenu():
    pygame.init()
    pygame.joystick.init()

    Width = 900
    Height = 600
    Screen = pygame.display.set_mode((Width, Height))
    pygame.display.set_caption("MoveUp — выбор контроллера")

    BigFont = pygame.font.SysFont("Arial", 38, bold=True)
    Font = pygame.font.SysFont("Arial", 24)
    SmallFont = pygame.font.SysFont("Arial", 18)
    Clock = pygame.time.Clock()
    Selected = 0
    StatusMessage = "Выберите MoveUp Gamepad и нажмите Enter."
    StatusColor = (180, 190, 205)
    OpenDevices = []

    def CloseOpenDevices():
        """Закрывает только созданные объекты джойстиков, не перезапуская подсистему."""
        for DeviceIndex, Device in OpenDevices:
            try:
                if Device.get_init():
                    Device.quit()
            except pygame.error:
                pass

    def RefreshOpenDevices():
        """Обновляет список устройств, не вызывая joystick.quit()/init()."""
        nonlocal OpenDevices, Selected
        CloseOpenDevices()
        OpenDevices = []

        for DeviceIndex in range(pygame.joystick.get_count()):
            try:
                Device = pygame.joystick.Joystick(DeviceIndex)
                Device.init()
                OpenDevices.append((DeviceIndex, Device))
            except pygame.error as Error:
                print(f"Не удалось открыть HID-устройство {DeviceIndex}: {Error}")

        if OpenDevices:
            Selected %= len(OpenDevices)
        else:
            Selected = 0

    RefreshOpenDevices()

    while True:
        DeviceCount = len(OpenDevices)
        if DeviceCount:
            Selected %= DeviceCount
        else:
            Selected = 0

        for Event in pygame.event.get():
            if Event.type == pygame.QUIT:
                CloseOpenDevices()
                return None

            # Важно: не перезапускаем pygame.joystick при горячем подключении.
            # Такой перезапуск во время обработки событий может повредить внутреннее
            # сопоставление устройств Pygame и вызвать KeyError внутри event.get().
            if Event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                RefreshOpenDevices()
                DeviceCount = len(OpenDevices)
                continue

            if Event.type == pygame.KEYDOWN:
                if Event.key == pygame.K_ESCAPE:
                    CloseOpenDevices()
                    return None
                elif Event.key == pygame.K_UP and DeviceCount:
                    Selected = (Selected - 1) % DeviceCount
                elif Event.key == pygame.K_DOWN and DeviceCount:
                    Selected = (Selected + 1) % DeviceCount
                elif Event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and DeviceCount:
                    DeviceIndex, Candidate = OpenDevices[Selected]
                    AxisCount = Candidate.get_numaxes()
                    if AxisCount < 6:
                        StatusMessage = (
                            f"У устройства только {AxisCount} осей. Нужны как минимум 6 осей "
                            "(Ry — ось 4, Rz — ось 5)."
                        )
                        StatusColor = (255, 150, 120)
                    else:
                        CloseOpenDevices()
                        return DeviceIndex

        DeviceCount = len(OpenDevices)
        Screen.fill((18, 25, 35))
        Screen.blit(BigFont.render("ПОДКЛЮЧЕНИЕ MOVEUP", True, (245, 248, 255)), (50, 42))
        Screen.blit(
            Font.render("Стрелки ↑/↓ — выбор    Enter — подтвердить    Esc — выход", True, (180, 190, 205)),
            (52, 100),
        )

        if DeviceCount == 0:
            Screen.blit(Font.render("HID-контроллеры не найдены", True, (255, 130, 130)), (60, 190))
            Screen.blit(
                SmallFont.render(
                    "Подключите Pico по USB и проверьте, что прошивка загружена.",
                    True,
                    (180, 190, 205),
                ),
                (60, 235),
            )
        else:
            for MenuIndex, (DeviceIndex, Candidate) in enumerate(OpenDevices):
                DeviceName = Candidate.get_name()
                AxisCount = Candidate.get_numaxes()
                DeviceRect = pygame.Rect(55, 165 + MenuIndex * 62, 790, 50)
                IsSelected = MenuIndex == Selected
                BackgroundColor = (0, 105, 165) if IsSelected else (42, 53, 70)
                pygame.draw.rect(Screen, BackgroundColor, DeviceRect, border_radius=8)
                LabelColor = (255, 255, 255) if IsSelected else (205, 213, 225)
                Label = f"[{DeviceIndex}] {DeviceName}    —    осей: {AxisCount}"
                Screen.blit(Font.render(Label, True, LabelColor), (DeviceRect.x + 16, DeviceRect.y + 11))

        Screen.blit(SmallFont.render(StatusMessage, True, StatusColor), (55, Height - 55))
        pygame.display.flip()
        Clock.tick(60)


# =========================
# Основной игровой класс
# =========================

class AdvancedRehabGame:
# =========================
# Создание окна и игрового состояния
# =========================

    def __init__(self, Controller):
        pygame.init()
        self.controller = Controller
        self.w, self.h = config["window"]["width"], config["window"]["height"]
        self.screen = pygame.display.set_mode((self.w, self.h), DOUBLEBUF | OPENGL)
        pygame.display.set_caption(config["window"]["title"])
        
        self.font_sys = pygame.font.SysFont('Arial', 15)
        self.font_btn = pygame.font.SysFont('Arial', 16, bold=True)
        self.font_hdr = pygame.font.SysFont('Arial', 20, bold=True)
        self.font_arrow = pygame.font.SysFont('Arial', 28, bold=True)
        
        self.rooms = ['KITCHEN', 'HALLWAY', 'BEDROOM']
        self.room_idx = 0
        self.room = self.rooms[self.room_idx]
        
        self.state = 'FREE_LOOK'
        self.menu_open = False
        
        self.btn_x = self.w - 250
        self.btn_y = 20
        
        self.kitchen_tasks = config["kitchen_tasks"]
        self.hallway_tasks = config["hallway_tasks"]
        self.bedroom_tasks = config["bedroom_tasks"]
        self.current_task_idx = 0
        
        self.cam_x, self.cam_y, self.cam_z = 0.0, 4.2, 6.8
        self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 4.2, 6.8
        self.look_x, self.look_y, self.look_z = 0.0, 1.0, 0.0
        self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 1.0, 0.0
        
        self.pitch, self.roll = 0.0, 0.0
        self.prev_pitch, self.prev_roll = 0.0, 0.0
        self.f_pitch, self.f_roll = 0.0, 0.0
        self.tremor = 0.0
        self.tremor_history = [0] * 280
        
        self.table_top_y = 1.645
        self.dinner_table_y = 1.03
        
        self.tea_stage = 1; self.kettle_y = self.table_top_y; self.tea_poured = 0.0
        self.mug_x, self.mug_y, self.mug_z = -0.6, self.table_top_y, -4.3
        self.soup_angle = 0.0
        self.particles = []
        self.sponge_x, self.sponge_z = 0.0, 1.6
        self.dirt_spots = [
            {"x": -0.5, "z": 1.2, "cleaned": 0.0}, {"x": 0.5, "z": 2.0, "cleaned": 0.0},
            {"x": -0.3, "z": 2.2, "cleaned": 0.0}, {"x": 0.4, "z": 1.1, "cleaned": 0.0}
        ]
        self.pancake_y = self.table_top_y + 0.18
        self.pancake_state = 'FRYING'; self.pancake_timer = 0.0; self.pancake_v_y = 0.0; self.pancake_rot_x = 0.0
        
        self.key_turns = 0.0         
        self.key_last_roll = 0.0     
        self.mirror_sponge_x, self.mirror_sponge_y = -5.75, 2.2
        self.mirror_spots = [
            {"y": 2.6, "z": -1.3, "cleaned": 0.0}, {"y": 2.5, "z": -0.7, "cleaned": 0.0},
            {"y": 1.9, "z": -1.2, "cleaned": 0.0}, {"y": 1.8, "z": -0.8, "cleaned": 0.0}
        ]
        self.mat_shift_z = 0.3         
        self.jacket_y = 1.1            
        self.lamp_on = False           
        self.lamp_cord_y = 0.0         

        self.bed_wrinkle_scale = 1.0  
        self.pillow_fluff_clicks = 0   
        self.box_lid_angle = 0.0      
        self.alarm_pos = [0.0, 1.8, -3.5] 
        self.book_stable_time = 0.0    

        self.clock = pygame.time.Clock()
        self.init_opengl_context()
        
        self.hud_tex = glGenTextures(1)
        self.textures = self.generate_procedural_textures()

# =========================
# Инициализация OpenGL
# =========================

    def init_opengl_context(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_LIGHT1)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glLightfv(GL_LIGHT0, GL_POSITION, (-4.0, 7.0, 4.0, 1.0))
        glLightfv(GL_LIGHT0, GL_AMBIENT, (0.5, 0.5, 0.5, 1.0))
        glLightfv(GL_LIGHT0, GL_DIFFUSE, (0.75, 0.75, 0.7, 1.0))
        glLightfv(GL_LIGHT1, GL_POSITION, (0.0, 5.0, -3.0, 1.0))
        glLightfv(GL_LIGHT1, GL_DIFFUSE, (0.4, 0.4, 0.45, 1.0))

# =========================
# Генерация процедурных текстур
# =========================

    def generate_procedural_textures(self):
        def surf_to_tex(surf):
            data = pygame.image.tobytes(surf, "RGBA", True)
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, surf.get_width(), surf.get_height(), 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
            return tex

        wall_surf = pygame.Surface((256, 256), pygame.SRCALPHA)
        wall_surf.fill((235, 233, 226))
        for y in range(0, 256, 16): pygame.draw.line(wall_surf, (220, 215, 205), (0, y), (256, y), 1)

        hall_wall_surf = pygame.Surface((256, 256), pygame.SRCALPHA)
        hall_wall_surf.fill((210, 200, 185))
        for x in range(0, 256, 32): pygame.draw.rect(hall_wall_surf, (195, 185, 170), (x, 0, 16, 256))

        bedroom_wall_surf = pygame.Surface((256, 256), pygame.SRCALPHA)
        bedroom_wall_surf.fill((190, 210, 205)) 

        tile_surf = pygame.Surface((128, 128), pygame.SRCALPHA)
        tile_surf.fill((220, 225, 230))
        for i in range(0, 128, 32):
            pygame.draw.line(tile_surf, (170, 180, 190), (0, i), (128, i), 2)
            pygame.draw.line(tile_surf, (170, 180, 190), (i, i), (i, 128), 2)

        metal_surf = pygame.Surface((128, 128), pygame.SRCALPHA)
        for x in range(128):
            for y in range(128):
                c = random.randint(190, 215)
                metal_surf.set_at((x, y), (c, c, c + 5, 255))

        mug_surf = pygame.Surface((64, 64), pygame.SRCALPHA)
        mug_surf.fill((210, 40, 40))

        bread_surf = pygame.Surface((128, 128), pygame.SRCALPHA)
        bread_surf.fill((135, 80, 35))

        wood_surf = pygame.Surface((256, 256), pygame.SRCALPHA)
        wood_surf.fill((140, 90, 55))
        for _ in range(40):
            y_pos = random.randint(0, 255)
            pygame.draw.line(wood_surf, (105, 65, 35), (0, y_pos), (256, y_pos + random.randint(-10, 10)), random.randint(1, 3))

        mat_surf = pygame.Surface((128, 64), pygame.SRCALPHA)
        mat_surf.fill((40, 50, 65))
        pygame.draw.rect(mat_surf, (200, 160, 60), (4, 4, 120, 56), 2)

        cloth_surf = pygame.Surface((64, 64), pygame.SRCALPHA)
        cloth_surf.fill((30, 90, 160))

        blanket_surf = pygame.Surface((128, 128), pygame.SRCALPHA)
        blanket_surf.fill((220, 180, 120)) 

        return {
            "wall": surf_to_tex(wall_surf),
            "hall_wall": surf_to_tex(hall_wall_surf),
            "bedroom_wall": surf_to_tex(bedroom_wall_surf),
            "tile": surf_to_tex(tile_surf),
            "metal": surf_to_tex(metal_surf),
            "ceramic": surf_to_tex(mug_surf),
            "bread": surf_to_tex(bread_surf),
            "wood": surf_to_tex(wood_surf),
            "mat": surf_to_tex(mat_surf),
            "cloth": surf_to_tex(cloth_surf),
            "blanket": surf_to_tex(blanket_surf)
        }

# =========================
# Настройка 3D-камеры
# =========================

    def apply_3d_camera(self):
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(45, (self.w / self.h), 0.1, 50.0)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        
        self.cam_x += (self.target_cam_x - self.cam_x) * 0.05
        self.cam_y += (self.target_cam_y - self.cam_y) * 0.05
        self.cam_z += (self.target_cam_z - self.cam_z) * 0.05
        
        self.look_x += (self.target_look_x - self.look_x) * 0.08
        self.look_y += (self.target_look_y - self.look_y) * 0.08
        self.look_z += (self.target_look_z - self.look_z) * 0.08
        
        gluLookAt(self.cam_x, self.cam_y, self.cam_z, self.look_x, self.look_y, self.look_z, 0, 1, 0)

# =========================
# Отрисовка кухни
# =========================

    def draw_kitchen_environment(self):
        draw_solid_cube(0, -0.05, 0, 16, 0.1, 16, (0.45, 0.47, 0.5))
        draw_solid_cube(0, 3, -5.8, 16, 6, 0.2, (1, 1, 1), self.textures["wall"])
        draw_solid_cube(-7.9, 3, 0, 0.2, 6, 16, (0.85, 0.85, 0.82))
        draw_solid_cube(0, 2.1, -5.68, 16, 1.0, 0.02, (1, 1, 1), self.textures["tile"])
        draw_solid_cube(-2.0, 0.8, -4.6, 4.0, 1.6, 1.4, (0.22, 0.25, 0.28)) 
        draw_solid_cube(1.8, 0.8, -4.6, 2.0, 1.6, 1.4, (0.15, 0.17, 0.2))    
        draw_solid_cube(-0.1, 1.62, -4.6, 8.0, 0.05, 1.45, (0.95, 0.95, 0.93)) 
        draw_solid_cube(1.8, 1.63, -4.6, 1.4, 0.01, 1.1, (0.1, 0.1, 0.1)) 
        for cx, cz in [(1.5, -4.9), (2.1, -4.9), (1.5, -4.3), (2.1, -4.3)]:
            draw_solid_cylinder(cx, 1.64, cz, 0.2, 0.01, (0.2, 0.2, 0.2)) 
        draw_solid_cube(1.8, 3.2, -4.6, 1.3, 0.3, 1.0, (0.7, 0.72, 0.75), self.textures["metal"], shine=True) 
        draw_solid_cube(1.8, 3.8, -5.2, 0.4, 1.0, 0.4, (0.6, 0.62, 0.65), self.textures["metal"]) 
        draw_solid_cube(-3.0, 1.625, -4.6, 1.3, 0.01, 1.0, (0.7, 0.7, 0.7), self.textures["metal"], shine=True) 
        draw_solid_cube(0, 1.0, 1.6, 3.2, 0.06, 2.2, (1, 1, 1), self.textures["wood"])
        for lx, lz in [(-1.4, 0.7), (1.4, 0.7), (-1.4, 2.5), (1.4, 2.5)]:
            draw_solid_cube(lx, 0.5, lz, 0.12, 1.0, 0.12, (0.15, 0.15, 0.15))
        draw_chair(-1.8, 0, 1.6, 90, self.textures["wood"])
        draw_chair(1.8, 0, 1.6, -90, self.textures["wood"])

# =========================
# Отрисовка холла
# =========================

    def draw_hallway_environment(self):
        if self.lamp_on: glLightfv(GL_LIGHT0, GL_AMBIENT, (0.8, 0.78, 0.7, 1.0))
        else: glLightfv(GL_LIGHT0, GL_AMBIENT, (0.5, 0.5, 0.5, 1.0))
        draw_solid_cube(0, -0.05, 0, 12, 0.1, 12, (1, 1, 1), self.textures["wood"])
        draw_solid_cube(0, 3, -5.9, 12, 6, 0.2, (1, 1, 1), self.textures["hall_wall"]) 
        draw_solid_cube(-5.9, 3, 0, 0.2, 6, 12, (1, 1, 1), self.textures["hall_wall"]) 
        draw_solid_cube(5.9, 3, 0, 0.2, 6, 12, (1, 1, 1), self.textures["hall_wall"])  
        draw_solid_cube(0.0, 1.9, -5.8, 1.8, 3.6, 0.15, (0.25, 0.2, 0.18)) 
        draw_solid_cube(0.0, 1.9, -5.74, 2.0, 3.7, 0.04, (0.12, 0.1, 0.09)) 
        draw_solid_cylinder(0.7, 1.8, -5.7, 0.02, 0.05, (0.7, 0.6, 0.3), rx=90)
        draw_solid_cube(0.75, 1.8, -5.66, 0.16, 0.02, 0.03, (0.7, 0.6, 0.3))
        draw_solid_cylinder(0.7, 1.4, -5.65, 0.03, 0.01, (0.1, 0.1, 0.1), rx=90)
        draw_solid_cube(0.0, 0.01, -4.5 + self.mat_shift_z, 1.8, 0.02, 1.0, (1, 1, 1), self.textures["mat"])
        draw_solid_cube(2.2, 1.8, -5.1, 1.4, 3.6, 1.0, (0.32, 0.22, 0.15), self.textures["wood"])
        draw_solid_cube(-2.0, 0.5, -5.2, 1.2, 1.0, 0.9, (0.32, 0.22, 0.15), self.textures["wood"])
        lamp_color = (1.0, 0.95, 0.6) if self.lamp_on else (0.4, 0.4, 0.35)
        draw_solid_cube(3.8, 2.8, -5.7, 0.2, 0.2, 0.1, (0.2, 0.2, 0.2))
        draw_solid_cylinder(3.8, 2.9, -5.5, 0.15, 0.2, lamp_color)
        draw_solid_cylinder(3.85, 2.5 - (self.lamp_cord_y * 0.5), -5.5, 0.005, 0.4, (0.8, 0.8, 0.8))
        draw_solid_cylinder(3.85, 2.3 - self.lamp_cord_y, -5.5, 0.015, 0.04, (0.9, 0.1, 0.1))
        draw_solid_cube(-4.5, 2.5, -5.7, 1.4, 0.1, 0.1, (0.3, 0.2, 0.1))
        draw_solid_cylinder(-4.5, 2.45, -5.6, 0.01, 0.1, (0.7, 0.7, 0.7), rx=45)
        draw_solid_cube(-4.5, self.jacket_y, -5.5, 0.4, 0.7, 0.15, (1, 1, 1), self.textures["cloth"])
        draw_solid_cube(-5.78, 2.2, -1.0, 0.04, 2.4, 1.4, (0.4, 0.3, 0.2)) 
        draw_solid_cube(-5.75, 2.2, -1.0, 0.02, 2.2, 1.2, (0.8, 0.9, 0.95), shine=True) 
        draw_solid_cube(-5.0, 0.5, -1.0, 1.2, 0.9, 1.6, (1, 1, 1), self.textures["wood"]) 

# =========================
# Отрисовка спальни
# =========================

    def draw_bedroom_environment(self):

        draw_solid_cube(0, -0.05, 0, 12, 0.1, 12, (1, 1, 1), self.textures["wood"])
        draw_solid_cube(0, 3, -5.9, 12, 6, 0.2, (1, 1, 1), self.textures["bedroom_wall"]) 
        draw_solid_cube(-5.9, 3, 0, 0.2, 6, 12, (1, 1, 1), self.textures["bedroom_wall"]) 

        draw_solid_cube(5.9, 3, 0, 0.2, 6, 12, (1, 1, 1), self.textures["bedroom_wall"])
        draw_solid_cube(5.82, 3.0, 0.0, 0.02, 2.4, 3.0, (0.8, 0.95, 1.0), shine=True) 
        draw_solid_cube(5.8, 3.0, 0.0, 0.05, 2.5, 0.1, (0.95, 0.95, 0.95))

        draw_solid_cube(-1.5, 0.4, -2.0, 3.0, 0.8, 4.0, (0.25, 0.18, 0.12), self.textures["wood"])
     
        blanket_thick = 0.5 + (0.12 * self.bed_wrinkle_scale)
        draw_solid_cube(-1.5, 0.8 + (blanket_thick/2), -2.0, 2.8, blanket_thick, 3.8, (1, 1, 1), self.textures["blanket"])

        pillow_height = 0.2 + (0.15 * (self.pillow_fluff_clicks / 15.0))
        draw_solid_cube(-1.5, 1.35, -3.4, 1.8, pillow_height, 0.7, (0.95, 0.95, 0.95))

        draw_solid_cube(2.2, 0.5, -4.5, 1.0, 1.0, 1.0, (0.35, 0.25, 0.18), self.textures["wood"])
        draw_solid_cube(2.2, 0.7, -3.98, 0.8, 0.25, 0.02, (0.2, 0.15, 0.1)) 

        draw_solid_cube(1.9, 1.05, -4.5, 0.35, 0.12, 0.25, (0.5, 0.1, 0.1))
        glPushMatrix()
        glTranslatef(1.9, 1.11, -4.625) 
        glRotatef(-self.box_lid_angle, 1, 0, 0)
        draw_solid_cube(0, 0.03, 0.125, 0.36, 0.06, 0.26, (0.6, 0.15, 0.15))
        glPopMatrix()

        if self.state != 'SET_ALARM_TASK':
            draw_solid_cylinder(2.4, 1.12, -4.4, 0.08, 0.15, (0.1, 0.4, 0.8), rx=90) 
            draw_solid_cylinder(2.4, 1.12, -4.32, 0.06, 0.01, (0.95, 0.95, 0.95), rx=90) 

        draw_solid_cube(2.2, 0.06, -3.2, 0.5, 0.12, 0.4, (0.7, 0.2, 0.2))
        draw_solid_cube(2.2, 0.16, -3.15, 0.48, 0.08, 0.38, (0.2, 0.5, 0.3))
        draw_solid_cube(2.18, 0.23, -3.22, 0.52, 0.06, 0.42, (0.2, 0.3, 0.6))

# =========================
# Отрисовка 2D-интерфейса
# =========================

    def draw_2d_interface(self):
        glPushAttrib(GL_ALL_ATTRIB_BITS); glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); gluOrtho2D(0, self.w, self.h, 0) 
        glMatrixMode(GL_MODELVIEW); glPushMatrix(); glLoadIdentity()
        
        ui_surface = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        
        arrow_box_w, arrow_box_h = 70, 90
        arrow_x, arrow_y = self.w - arrow_box_w - 10, (self.h // 2) - (arrow_box_h // 2)
        
        pygame.draw.rect(ui_surface, (30, 40, 55, 220), (arrow_x, arrow_y, arrow_box_w, arrow_box_h), border_radius=10)
        pygame.draw.rect(ui_surface, (0, 190, 255, 140), (arrow_x, arrow_y, arrow_box_w, arrow_box_h), 2, border_radius=10)
        
        ui_surface.blit(self.font_arrow.render("-->", True, (255, 215, 0)), (arrow_x + 14, arrow_y + 15))
        room_names = {"KITCHEN": "Кухня", "HALLWAY": "Холл", "BEDROOM": "Спальня"}
        next_room_idx = (self.room_idx + 1) % len(self.rooms)
        ui_surface.blit(self.font_sys.render(room_names[self.rooms[next_room_idx]], True, (255, 255, 255)), (arrow_x + 8, arrow_y + 55))

        if self.room == 'KITCHEN': current_tasks = self.kitchen_tasks
        elif self.room == 'HALLWAY': current_tasks = self.hallway_tasks
        else: current_tasks = self.bedroom_tasks
        
        pygame.draw.rect(ui_surface, (35, 45, 65, 245), (self.btn_x, self.btn_y, 230, 44), border_radius=8)
        pygame.draw.rect(ui_surface, (0, 180, 255, 100), (self.btn_x, self.btn_y, 230, 44), 1, border_radius=8)
        ui_surface.blit(self.font_btn.render("[M] МЕНЮ ТРЕНИРОВОК", True, (255, 255, 255)), (self.btn_x + 25, self.btn_y + 12))
        
        if self.menu_open:
            drop_x, drop_y = self.w - 370, 75
            menu_height = 55 + (len(current_tasks) * 62) + 15
            pygame.draw.rect(ui_surface, (250, 252, 255, 245), (drop_x, drop_y, 350, menu_height), border_radius=10)
            pygame.draw.rect(ui_surface, (170, 185, 200), (drop_x, drop_y, 350, menu_height), 1, border_radius=10)
            ui_surface.blit(self.font_hdr.render("ВЫБЕРИТЕ ТРЕНИРОВКУ:", True, (25, 35, 50)), (drop_x + 20, drop_y + 15))
            for i, task_item in enumerate(current_tasks):
                y_pos = drop_y + 55 + (i * 62)
                color_theme = (46, 175, 90) if task_item["completed"] else (40, 120, 215)
                pygame.draw.rect(ui_surface, color_theme, (drop_x + 15, y_pos, 320, 48), border_radius=6)
                ui_surface.blit(self.font_btn.render(f"{i+1}. {task_item['name']}", True, (255, 255, 255)), (drop_x + 25, y_pos + 6))

        if self.state != 'FREE_LOOK':
            active_task = current_tasks[self.current_task_idx]
            bx, by, bw, bh = self.w - 360, self.h - 420, 340, 400
            pygame.draw.rect(ui_surface, (255, 255, 255, 240), (bx, by, bw, bh), border_radius=12)
            ui_surface.blit(self.font_hdr.render("АКТИВНАЯ ТРЕНИРОВКА", True, (35, 45, 65)), (bx + 20, by + 15))
            
            desc_lines = [active_task['description'][i:i+35] for i in range(0, len(active_task['description']), 35)]
            for li, line in enumerate(desc_lines):
                ui_surface.blit(self.font_sys.render(line, True, (100, 110, 125)), (bx + 20, by + 50 + li*18))

            ui_surface.blit(self.font_sys.render(f"Выполнение: {int(active_task['progress'])}%", True, (40, 50, 70)), (bx + 20, by + 120))
            pygame.draw.rect(ui_surface, (230, 235, 245), (bx + 20, by + 140, 300, 14), border_radius=4)
            pygame.draw.rect(ui_surface, (46, 175, 90), (bx + 20, by + 140, int(active_task["progress"] * 3.0), 14), border_radius=4)
            
            ui_surface.blit(self.font_btn.render("Стабильность кисти", True, (70, 85, 105)), (bx + 20, by + 175))
            gx, gy, gw, gh = bx + 20, by + 200, 300, 130
            pygame.draw.rect(ui_surface, (15, 22, 35), (gx, gy, gw, gh), border_radius=8)
            self.tremor_history.append(self.tremor)
            if len(self.tremor_history) > gw: self.tremor_history.pop(0)
            graph_points = [(gx + idx, gy + gh - int(min(max(val * 9, 5), gh - 5))) for idx, val in enumerate(self.tremor_history)]
            if len(graph_points) > 1:
                col = (240, 70, 70) if self.tremor > config["rehabilitation_settings"]["max_allowed_tremor"] else (46, 175, 90)
                pygame.draw.lines(ui_surface, col, False, graph_points, 2)

        bytes_data = pygame.image.tobytes(ui_surface, 'RGBA', False)
        glBindTexture(GL_TEXTURE_2D, self.hud_tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.w, self.h, 0, GL_RGBA, GL_UNSIGNED_BYTE, bytes_data)
        
        glEnable(GL_TEXTURE_2D); glColor4f(1.0, 1.0, 1.0, 1.0)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(0, 0); glTexCoord2f(1, 0); glVertex2f(self.w, 0)
        glTexCoord2f(1, 1); glVertex2f(self.w, self.h); glTexCoord2f(0, 1); glVertex2f(0, self.h)
        glEnd(); glDisable(GL_TEXTURE_2D)
        
        glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW); glPopMatrix(); glPopAttrib()

# =========================
# Чтение данных датчика
# =========================

    def update_sensor_data(self):
        """Read the updated Pico USB HID axes and convert them to game angles."""
        if self.controller is None:
            return

        try:
            pygame.event.pump()
            if not self.controller.get_init():
                self.controller.init()

            if self.controller.get_numaxes() <= max(ControllerRyAxis, ControllerRzAxis):
                return

            # Preserve the previous frame so gesture-based tasks can detect movement.
            self.prev_pitch = self.pitch
            self.prev_roll = self.roll

            RyValue = max(-1.0, min(1.0, float(self.controller.get_axis(ControllerRyAxis))))
            RzValue = max(-1.0, min(1.0, float(self.controller.get_axis(ControllerRzAxis))))

            # HID values are normalized to [-1, 1]; the firmware maps +/-45 degrees
            # to that range. Keep the same signs and axis assignment as Drawing.py.
            self.pitch = ControllerPitchSign * RyValue * ControllerTiltRangeDegrees
            self.roll = ControllerRollSign * RzValue * ControllerTiltRangeDegrees

            # Per-frame control-signal change used by the existing stability checks.
            # This is a control variability value, not a medical tremor measurement.
            self.tremor = math.hypot(
                self.pitch - self.prev_pitch,
                self.roll - self.prev_roll,
            )

            alpha = 0.20
            self.f_pitch += (self.pitch - self.f_pitch) * alpha
            self.f_roll += (self.roll - self.f_roll) * alpha

        except pygame.error as Error:
            print(f"Ошибка чтения HID-контроллера: {Error}")
            self.controller = None


# =========================
# Сброс в свободный обзор
# =========================

    def reset_to_freelook(self):
        self.state = 'FREE_LOOK'
        if self.room == 'KITCHEN':
            self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 4.2, 6.8
            self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 1.0, 0.0
        elif self.room == 'HALLWAY':
            self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 3.4, 5.5
            self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 1.5, -5.8
        elif self.room == 'BEDROOM':
            self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 3.8, 6.0
            self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 1.2, -2.0

# =========================
# Главный цикл игры
# =========================

    def game_loop(self):
        while True:
            self.clock.tick(60)
            self.update_sensor_data()
            
            for event in pygame.event.get():
                if event.type == QUIT: pygame.quit(); return
                if event.type == KEYDOWN:
                    if event.key == K_m: self.menu_open = not self.menu_open
                    if event.key == K_ESCAPE: self.reset_to_freelook()

                if event.type == MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    
                    arrow_box_w, arrow_box_h = 70, 90
                    arrow_x, arrow_y = self.w - arrow_box_w - 10, (self.h // 2) - (arrow_box_h // 2)
                    
                    if arrow_x <= mx <= arrow_x + arrow_box_w and arrow_y <= my <= arrow_y + arrow_box_h:
                        self.room_idx = (self.room_idx + 1) % len(self.rooms)
                        self.room = self.rooms[self.room_idx]
                        self.menu_open = False
                        self.reset_to_freelook()
                    
                    if self.btn_x <= mx <= self.btn_x + 230 and self.btn_y <= my <= self.btn_y + 44:
                        self.menu_open = not self.menu_open
                    
                    if self.menu_open:
                        drop_x, drop_y = self.w - 370, 75
                        current_tasks = self.kitchen_tasks if self.room == 'KITCHEN' else (self.hallway_tasks if self.room == 'HALLWAY' else self.bedroom_tasks)
                        for i in range(len(current_tasks)):
                            if drop_x + 15 <= mx <= drop_x + 335 and drop_y + 55 + (i * 62) <= my <= drop_y + 103 + (i * 62):
                                self.current_task_idx = i; self.menu_open = False; current_tasks[i]["progress"] = 0
                                
                                if self.room == 'KITCHEN':
                                    if i == 0:
                                        self.state = 'TEA_TASK'; self.tea_stage = 1; self.kettle_y = self.table_top_y; self.tea_poured = 0.0
                                        self.mug_x, self.mug_y, self.mug_z = -0.6, self.table_top_y, -4.3
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -0.3, 2.4, -2.4
                                        self.target_look_x, self.target_look_y, self.target_look_z = -0.5, self.table_top_y, -4.3
                                    elif i == 1:
                                        self.state = 'BREAD_TASK'
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -2.0, 2.3, -2.5
                                        self.target_look_x, self.target_look_y, self.target_look_z = -2.0, self.table_top_y, -4.3
                                    elif i == 2:
                                        self.state = 'SOUP_TASK'
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 1.8, 2.4, -2.5
                                        self.target_look_x, self.target_look_y, self.target_look_z = 1.8, self.table_top_y, -4.3
                                    elif i == 3:
                                        self.state = 'WIPE_TASK'; self.sponge_x, self.sponge_z = 0.0, 1.6
                                        for spot in self.dirt_spots: spot["cleaned"] = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 2.5, 3.8
                                        self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 1.0, 1.6
                                    elif i == 4:
                                        self.state = 'PANCAKE_TASK'; self.pancake_state = 'FRYING'; self.pancake_timer = 0.0
                                        self.pancake_y = self.table_top_y + 0.18; self.pancake_rot_x = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 1.8, 2.4, -2.5
                                        self.target_look_x, self.target_look_y, self.target_look_z = 1.8, self.table_top_y, -4.3
                                
                                elif self.room == 'HALLWAY':
                                    if i == 0: 
                                        self.state = 'KEY_TASK'; self.key_turns = 0.0; self.key_last_roll = self.f_roll
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.7, 1.4, -4.5
                                        self.target_look_x, self.target_look_y, self.target_look_z = 0.7, 1.4, -5.8
                                    elif i == 1:
                                        self.state = 'MIRROR_TASK'
                                        for spot in self.mirror_spots: spot["cleaned"] = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -3.2, 2.2, -1.0
                                        self.target_look_x, self.target_look_y, self.target_look_z = -5.75, 2.2, -1.0
                                    elif i == 2:
                                        self.state = 'FIX_MAT_TASK'; self.mat_shift_z = 0.5
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 0.0, 1.8, -3.2
                                        self.target_look_x, self.target_look_y, self.target_look_z = 0.0, 0.0, -4.5
                                    elif i == 3:
                                        self.state = 'HANG_JACKET_TASK'; self.jacket_y = 1.1
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -4.5, 2.0, -4.2
                                        self.target_look_x, self.target_look_y, self.target_look_z = -4.5, 2.2, -5.7
                                    elif i == 4:
                                        self.state = 'LAMP_SWITCH_TASK'; self.lamp_cord_y = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 3.8, 2.2, -4.2
                                        self.target_look_x, self.target_look_y, self.target_look_z = 3.8, 2.4, -5.7

                                elif self.room == 'BEDROOM':
                                    if i == 0:
                                        self.state = 'MAKE_BED_TASK'; self.bed_wrinkle_scale = 1.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -1.5, 2.5, 1.0
                                        self.target_look_x, self.target_look_y, self.target_look_z = -1.5, 0.8, -2.0
                                    elif i == 1:
                                        self.state = 'FLUFF_PILLOW_TASK'; self.pillow_fluff_clicks = 0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = -1.5, 2.2, -1.5
                                        self.target_look_x, self.target_look_y, self.target_look_z = -1.5, 1.2, -3.4
                                    elif i == 2:
                                        self.state = 'OPEN_BOX_TASK'; self.box_lid_angle = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 1.9, 1.5, -3.2
                                        self.target_look_x, self.target_look_y, self.target_look_z = 1.9, 1.05, -4.5
                                    elif i == 3:
                                        self.state = 'SET_ALARM_TASK'; self.alarm_pos = [0.2, 1.6, -2.8]
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 1.5, 1.7, -2.6
                                        self.target_look_x, self.target_look_y, self.target_look_z = 2.2, 1.1, -4.4
                                    elif i == 4:
                                        self.state = 'STACK_BOOK_TASK'; self.book_stable_time = 0.0
                                        self.target_cam_x, self.target_cam_y, self.target_cam_z = 2.2, 1.0, -2.0
                                        self.target_look_x, self.target_look_y, self.target_look_z = 2.2, 0.3, -3.2

            glClearColor(0.55, 0.65, 0.75, 1.0); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glDisable(GL_TEXTURE_2D); glDisable(GL_BLEND); glEnable(GL_LIGHTING); glEnable(GL_DEPTH_TEST)
            
            self.apply_3d_camera()
            
            if self.room == 'KITCHEN': self.draw_kitchen_environment()
            elif self.room == 'HALLWAY': self.draw_hallway_environment()
            else: self.draw_bedroom_environment()
            
            if self.room == 'KITCHEN':
                if self.state == 'TEA_TASK':
                    task = self.kitchen_tasks[0]
                    if self.tea_stage == 1:
                        if self.f_pitch > 6.0 and self.kettle_y < (self.table_top_y + 0.4): self.kettle_y += 0.005
                        if self.kettle_y >= (self.table_top_y + 0.35): self.tea_stage = 2
                        task["progress"] = int(((self.kettle_y - self.table_top_y) / 0.35) * 20)
                    elif self.tea_stage == 2:
                        if self.f_roll > config["rehabilitation_settings"]["pour_angle_trigger"]:
                            self.tea_poured += 0.15; task["progress"] = 20 + min(int((self.tea_poured / 40.0) * 40), 40)
                            if random.random() > 0.1: self.particles.append(LiquidParticle(-0.4, self.kettle_y + 0.05, -4.3, (0.45, 0.22, 0.05)))
                            if self.tea_poured >= 40: self.tea_stage = 3 
                    elif self.tea_stage == 3:
                        if self.mug_z < 1.6:
                            if abs(self.f_roll) < 15.0 and abs(self.f_pitch) < 15.0:
                                self.mug_z += 0.012; self.mug_x += (0.0 - self.mug_x) * 0.012; self.mug_y += (self.dinner_table_y - self.mug_y) * 0.012
                                task["progress"] = 60 + int(((self.mug_z + 4.3) / 5.9) * 40)
                        if self.tremor > config["rehabilitation_settings"]["max_allowed_tremor"]:
                            task["progress"] = max(60, task["progress"] - 0.1)
                        if self.mug_z >= 1.55:
                            task["completed"] = True; task["progress"] = 100; self.reset_to_freelook()
                    draw_detailed_kettle(-0.05, self.kettle_y, -4.3, self.f_roll, self.textures["metal"])
                    draw_detailed_mug(self.mug_x, self.mug_y, self.mug_z, self.tea_poured, self.textures["ceramic"])

                elif self.state == 'BREAD_TASK':
                    task = self.kitchen_tasks[1]
                    draw_detailed_bread(-2.0, self.table_top_y + 0.03, -4.6, self.textures["bread"])
                    knife_y = self.table_top_y + 0.2 + (self.f_pitch * 0.01)
                    draw_detailed_knife(-2.0, knife_y, -4.6, self.textures["metal"])
                    if abs(self.pitch - self.prev_pitch) > 1.8 and self.tremor < 8.0:
                        task["progress"] = min(100, task["progress"] + 0.15)
                        if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'SOUP_TASK':
                    task = self.kitchen_tasks[2]
                    draw_solid_cylinder(1.8, self.table_top_y + 0.175, -4.6, 0.4, 0.35, (0.75, 0.78, 0.8), self.textures["metal"], shine=True)
                    delta_angle = (self.f_roll * 0.002); self.soup_angle += delta_angle
                    if abs(delta_angle) > 0.0005 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                        task["progress"] = min(100, task["progress"] + 0.08)
                        if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()
                    glPushMatrix()
                    glTranslatef(1.8 + math.cos(self.soup_angle) * 0.18, self.table_top_y + 0.35, -4.6 + math.sin(self.soup_angle) * 0.18)
                    glRotatef(15, 0, 0, 1); draw_solid_cube(0, 0, 0, 0.03, 0.45, 0.03, (0.5, 0.3, 0.15), self.textures["wood"]); glPopMatrix()

                elif self.state == 'WIPE_TASK':
                    task = self.kitchen_tasks[3]
                    # Roll moves the sponge left/right across the table (world X).
                    self.sponge_x = min(max(-self.f_roll * 0.032, -1.3), 1.3)
                    # Pitch moves it toward/away across the table (world Z).
                    # Use stronger travel so this depth movement is clearly visible
                    # from the angled camera used for the tabletop.
                    self.sponge_z = min(
                        max(1.6 + (self.f_pitch * TableWipeDepthSensitivity), 0.6),
                        2.6,
                    )
                    for spot in self.dirt_spots:
                        if spot["cleaned"] < 1.0:
                            glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                            draw_solid_cube(spot["x"], self.dinner_table_y + 0.035, spot["z"], 0.22, 0.005, 0.22, (0.4, 0.3, 0.2, 1.0 - spot["cleaned"]))
                            glDisable(GL_BLEND)
                            dist = math.sqrt((self.sponge_x - spot["x"])**2 + (self.sponge_z - spot["z"])**2)
                            if dist < 0.25 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                                spot["cleaned"] = min(1.0, spot["cleaned"] + 0.005)
                    total_cleaned = sum([s["cleaned"] for s in self.dirt_spots])
                    task["progress"] = int((total_cleaned / len(self.dirt_spots)) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()
                    draw_solid_cube(self.sponge_x, self.dinner_table_y + 0.06, self.sponge_z, 0.2, 0.04, 0.12, (0.1, 0.75, 0.7))

                elif self.state == 'PANCAKE_TASK':
                    task = self.kitchen_tasks[4]; draw_solid_cylinder(1.8, self.table_top_y + 0.165, -4.6, 0.45, 0.03, (0.15, 0.15, 0.15))
                    self.pancake_timer += 0.016
                    if self.pancake_state == 'FRYING':
                        draw_solid_cylinder(1.8, self.pancake_y, -4.6, 0.35, 0.015, (0.9, 0.7, 0.4))
                        task["progress"] = min(90, int((self.pancake_timer / 6.0) * 90))
                        if (self.pitch - self.prev_pitch) > 3.5 and self.pancake_timer > 3.0: self.pancake_state = 'FLYING'; self.pancake_v_y = 0.08
                    elif self.pancake_state == 'FLYING':
                        self.pancake_y += self.pancake_v_y; self.pancake_v_y -= 0.005; self.pancake_rot_x += 12.0
                        glPushMatrix(); glTranslatef(1.8, self.pancake_y, -4.6); glRotatef(self.pancake_rot_x, 1, 0, 0)
                        draw_solid_cylinder(0, 0, 0, 0.35, 0.015, (0.7, 0.45, 0.2)); glPopMatrix()
                        if self.pancake_v_y <= 0 and self.pancake_y <= (self.table_top_y + 0.18):
                            task["progress"] = 100; task["completed"] = True; self.reset_to_freelook()

            elif self.room == 'HALLWAY':
                if self.state == 'KEY_TASK':
                    task = self.hallway_tasks[0]; roll_diff = self.f_roll - self.key_last_roll; self.key_last_roll = self.f_roll
                    if self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]: self.key_turns += abs(roll_diff) / 180.0
                    task["progress"] = min(100, int((self.key_turns / 3.0) * 100))
                    glPushMatrix(); glTranslatef(0.7, 1.4, -5.62); glRotatef(self.f_roll * 3.0, 0, 0, 1) 
                    draw_solid_cylinder(0, 0, 0, 0.012, 0.12, (0.75, 0.75, 0.75), shine=True); glPopMatrix()
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'MIRROR_TASK':
                    task = self.hallway_tasks[1]
                    # The mirror lies in the Y/Z plane: roll moves horizontally
                    # along Z, while pitch moves vertically along Y. The usable
                    # horizontal span is narrow, so increase roll travel to make
                    # left/right movement as noticeable as vertical movement.
                    target_sponge_z = min(
                        max(-1.0 + (self.f_roll * MirrorWipeHorizontalSensitivity), -1.5),
                        -0.5,
                    )
                    target_sponge_y = min(max(2.2 + (self.f_pitch * 0.025), 1.2), 3.2)
                    for spot in self.mirror_spots:
                        if spot["cleaned"] < 1.0:
                            glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                            draw_solid_cube(-5.74, spot["y"], spot["z"], 0.005, 0.18, 0.18, (0.4, 0.35, 0.25, 1.0 - spot["cleaned"]))
                            glDisable(GL_BLEND)
                            dist = math.sqrt((target_sponge_y - spot["y"])**2 + (target_sponge_z - spot["z"])**2)
                            if dist < 0.22 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]: spot["cleaned"] = min(1.0, spot["cleaned"] + 0.006)
                    task["progress"] = int((sum([s["cleaned"] for s in self.mirror_spots]) / len(self.mirror_spots)) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()
                    draw_solid_cube(self.mirror_sponge_x, target_sponge_y, target_sponge_z, 0.04, 0.12, 0.18, (0.1, 0.75, 0.7))

                elif self.state == 'FIX_MAT_TASK':
                    task = self.hallway_tasks[2]
                    if self.f_pitch > 5.0 and self.mat_shift_z > 0.0 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                        self.mat_shift_z = max(0.0, self.mat_shift_z - 0.004)
                    task["progress"] = int((1.0 - (self.mat_shift_z / 0.5)) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'HANG_JACKET_TASK':
                    task = self.hallway_tasks[3]
                    if self.f_pitch < -6.0 and self.jacket_y < 2.2 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                        self.jacket_y = min(2.2, self.jacket_y + 0.01)
                    task["progress"] = int(((self.jacket_y - 1.1) / 1.1) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'LAMP_SWITCH_TASK':
                    task = self.hallway_tasks[4]
                    if self.pitch > 15.0 and not self.lamp_on: self.lamp_cord_y = 0.15; self.lamp_on = True; task["progress"] = 100
                    if self.lamp_cord_y > 0.0: self.lamp_cord_y -= 0.01
                    if task["progress"] >= 100 and self.lamp_cord_y <= 0.0: task["completed"] = True; self.reset_to_freelook()

            elif self.room == 'BEDROOM':
            
                if self.state == 'MAKE_BED_TASK':
                    task = self.bedroom_tasks[0]
                    if abs(self.pitch - self.prev_pitch) > 1.5 and self.tremor < 9.0:
                        self.bed_wrinkle_scale = max(0.0, self.bed_wrinkle_scale - 0.005)
                    task["progress"] = int((1.0 - self.bed_wrinkle_scale) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'FLUFF_PILLOW_TASK':
                    task = self.bedroom_tasks[1]
                    if abs(self.pitch - self.prev_pitch) > 4.0:
                        self.pillow_fluff_clicks = min(15, self.pillow_fluff_clicks + 1)
                        pygame.time.wait(30) 
                    task["progress"] = int((self.pillow_fluff_clicks / 15.0) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'OPEN_BOX_TASK':
                    task = self.bedroom_tasks[2]
                    if self.f_pitch < -3.0 and self.box_lid_angle < 90.0:
                        if self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                            self.box_lid_angle = min(90.0, self.box_lid_angle + 0.8)
                        else:  
                            self.box_lid_angle = max(0.0, self.box_lid_angle - 1.5)
                    task["progress"] = int((self.box_lid_angle / 90.0) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

                elif self.state == 'SET_ALARM_TASK':
                    task = self.bedroom_tasks[3]
                
                    self.alarm_pos[0] = min(max(0.2 - (self.f_roll * 0.03), 0.2), 2.4)
                    self.alarm_pos[2] = min(max(-2.8 + (self.f_pitch * 0.025), -4.4), -2.8)
                    dist_to_target = math.sqrt((self.alarm_pos[0] - 2.4)**2 + (self.alarm_pos[2] - -4.4)**2)
                    if dist_to_target < 0.2: self.alarm_pos[1] = 1.12
                    else: self.alarm_pos[1] = 1.5

                
                    draw_solid_cylinder(self.alarm_pos[0], self.alarm_pos[1], self.alarm_pos[2], 0.08, 0.15, (0.1, 0.4, 0.8), rx=90)
                    draw_solid_cylinder(self.alarm_pos[0], self.alarm_pos[1], self.alarm_pos[2] + 0.08, 0.06, 0.01, (0.95, 0.95, 0.95), rx=90)

                    task["progress"] = int((1.0 - (dist_to_target / 2.5)) * 100)
                    task["progress"] = min(max(task["progress"], 0), 100)
                    if dist_to_target < 0.05: task["completed"] = True; task["progress"] = 100; self.reset_to_freelook()

            
                elif self.state == 'STACK_BOOK_TASK':
                    task = self.bedroom_tasks[4]
                
                    if abs(self.f_roll) < 5.0 and self.tremor < config["rehabilitation_settings"]["max_allowed_tremor"]:
                        self.book_stable_time = min(4.0, self.book_stable_time + 0.016)
                    else:
                        self.book_stable_time = max(0.0, self.book_stable_time - 0.02) 

                    
                    book_y = 0.28 + (0.4 * (1.0 - (self.book_stable_time / 4.0)))
                    draw_solid_cube(2.18, book_y, -3.22, 0.5, 0.05, 0.4, (0.8, 0.4, 0.1))

                    task["progress"] = int((self.book_stable_time / 4.0) * 100)
                    if task["progress"] >= 100: task["completed"] = True; self.reset_to_freelook()

            glDisable(GL_LIGHTING); glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            for p in self.particles[:]:
                p.update(); p.draw()
                if p.life <= 0: self.particles.remove(p)
            glEnable(GL_LIGHTING)

            self.draw_2d_interface()
            pygame.display.flip()

if __name__ == "__main__":
    ControllerIndex = RunControllerMenu()

    if ControllerIndex is None:
        pygame.quit()
        raise SystemExit

    Controller = None
    try:
        pygame.joystick.init()
        Controller = pygame.joystick.Joystick(ControllerIndex)
        Controller.init()

        if Controller.get_numaxes() < 6:
            raise RuntimeError(
                "У выбранного контроллера недостаточно HID-осей: "
                "требуются Ry (индекс 4) и Rz (индекс 5)."
            )

        print(f"Подключён HID-контроллер: {Controller.get_name()}")
        AdvancedRehabGame(Controller).game_loop()

    finally:
        if Controller is not None:
            try:
                Controller.quit()
            except pygame.error:
                pass
        pygame.quit()
