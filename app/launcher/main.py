from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import uuid

from PySide6.QtCore import Qt, QTimer, QSize, QRect, QRectF, QPointF, QStandardPaths
from PySide6.QtGui import QColor, QPalette, QFont, QLinearGradient, QPainter, QPen, QBrush, QPainterPath, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QStackedWidget,
    QScrollArea,
    QFileDialog,
    QMessageBox,
    QLineEdit,
    QComboBox,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QSizePolicy,
    QSpacerItem,
    QMenu,
)

APP_NAME = "MoveUp Launcher"
APP_VERSION = "0.5.0"

COLORS = {
    "bg": "#17232D",
    "sidebar": "#1E2D39",
    "panel": "#263747",
    "panel_alt": "#304555",
    "border": "#3B5262",
    "blue": "#A9C8D8",
    "blue_bright": "#C2DCE8",
    "milk": "#F3F5F4",
    "muted": "#AABAC4",
    "yellow": "#E8C66A",
    "red": "#E77B72",
    "green": "#8CCDAA",
}


def app_dir() -> Path:
    """Directory beside the executable in packaged mode, or beside main.py in development."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_installer_defaults() -> dict[str, Any]:
    """Read optional installer-written settings stored beside the launcher executable."""
    for file_name in ("launcher_defaults.json", "installer_defaults.json"):
        defaults_path = app_dir() / file_name
        if defaults_path.is_file():
            try:
                with defaults_path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def app_icon_path() -> Path | None:
    """Prefer the requested icon.png, while retaining backward compatibility with icon.jpg."""
    for file_name in ("icon.png", "icon.jpg", "icon.ico"):
        candidate = app_dir() / file_name
        if candidate.is_file():
            return candidate
    return None


def games_statistics_path() -> Path:
    """Locate the games' shared statistics file at ../games/statistics.json.

    MOVEUP_GAMES_STATISTICS_FILE can override this path for development or custom installs.
    The file is read-only for the launcher; game processes remain its owners.
    """
    configured_path = os.environ.get("MOVEUP_GAMES_STATISTICS_FILE", "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve(strict=False)

    candidates = [
        app_dir().parent / "games" / "statistics.json",  # requested ../games/statistics.json
        app_dir() / "games" / "statistics.json",         # portable layout fallback
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(strict=False)
    return candidates[0].resolve(strict=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def format_duration(seconds: int | float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds_left} сек"


def format_metric_value(value: Any, unit: str = "") -> str:
    if isinstance(value, bool):
        display_value = "Да" if value else "Нет"
    elif isinstance(value, float):
        display_value = f"{value:.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, (int, str)):
        display_value = str(value)
    else:
        display_value = "…"
    return f"{display_value} {unit}".strip()


def metric_items(metrics: Any, limit: int = 3) -> list[tuple[str, str]]:
    """Normalize flexible per-game metric objects for display without enforcing game-specific keys."""
    if not isinstance(metrics, dict):
        return []
    result: list[tuple[str, str]] = []
    for key, item in metrics.items():
        if isinstance(item, dict):
            readable_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ")
            label = str(item.get("label") or readable_key.capitalize())
            if "value" not in item:
                continue
            value = format_metric_value(item.get("value"), str(item.get("unit") or ""))
        else:
            readable_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ")
            label = readable_key.capitalize()
            value = format_metric_value(item)
        result.append((label, value))
        if len(result) >= limit:
            break
    return result


def app_data_dir() -> Path:
    """Return a per-user config directory that works in both script and packaged modes."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    if not base:
        base = str(Path.home() / ".config" / "moveup-launcher")
    target = Path(base) / "MoveUp"
    target.mkdir(parents=True, exist_ok=True)
    return target


def find_default_python() -> str:
    current = Path(sys.executable) if sys.executable else None
    if current and current.exists() and "python" in current.name.lower():
        return str(current)
    return shutil.which("python") or shutil.which("python3") or "python"


@dataclass
class GameEntry:
    id: str
    title: str
    description: str
    category: str = "Координация"
    path: str = ""
    args: str = ""
    accent: str = "#A9C8D8"
    art: str = "maze"
    source_path: str = ""  # relative filename under AppConfig.default_source

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameEntry":
        allowed = {"id", "title", "description", "category", "path", "args", "accent", "art", "source_path"}
        cleaned = {key: value for key, value in data.items() if key in allowed}
        return cls(**cleaned)


DEFAULT_GAMES = [
    GameEntry(
        id="maze",
        title="Лабиринт",
        description="Точность движений, планирование и координация верхних конечностей.",
        category="Координация",
        accent="#A9C8D8",
        art="maze",
        source_path="labyrinth.py",
    ),
    GameEntry(
        id="shooter",
        title="Точный выстрел",
        description="Управление направлением, устойчивость и точность наведения.",
        category="Точность",
        accent="#E8C66A",
        art="target",
        source_path="shooter.py",
    ),
    GameEntry(
        id="minefield",
        title="Поле внимания",
        description="Наблюдательность, реакция и последовательное выполнение задач.",
        category="Внимание",
        accent="#93C8AC",
        art="dots",
        source_path="minefield.py",
    ),
]


IGNORED_GAME_DIRECTORY_NAMES = {"assets", ".git", ".venv", "venv", "env", "node_modules"}
GAME_ENTRYPOINT_NAMES = ("main.py", "game.py", "run.py", "launcher.py", "app.py")
GAME_ID_ALIASES = {
    "labyrinth": "maze",
    "cyber_maze": "maze",
    "maze": "maze",
    "cyber_shooter": "shooter",
    "shooter": "shooter",
    "cyber_minefield": "minefield",
    "minefield": "minefield",
    "race": "neon_highway",
    "racing": "neon_highway",
    "neon_highway": "neon_highway",
}


def _game_slug(value: str) -> str:
    """Make a stable ID from a directory or script name, retaining Unicode letters."""
    slug = re.sub(r"[^\w]+", "_", value.casefold(), flags=re.UNICODE).strip("_")
    if re.fullmatch(r"race(?:_[0-9_]+)?", slug):
        return "neon_highway"
    return GAME_ID_ALIASES.get(slug, slug or "game")


def _is_ignored_game_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    for part in relative_parts[:-1] if path.is_file() else relative_parts:
        lowered = part.casefold()
        if "pycache" in lowered or lowered in IGNORED_GAME_DIRECTORY_NAMES:
            return True
    return path.name.casefold() == "__init__.py" if path.is_file() else False


def _python_files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    try:
        candidates = root.rglob("*.py")
        return sorted(
            (item.resolve(strict=False) for item in candidates
             if item.is_file() and not _is_ignored_game_path(item, root)),
            key=lambda item: str(item).casefold(),
        )
    except OSError:
        return []


def discover_game_files(root: Path) -> list[dict[str, Any]]:
    """Find games represented by standalone Python files or named folders.

    Root-level scripts are treated as separate games. A nested folder is treated
    as one game when it contains an obvious entrypoint (directly or under a
    single source subfolder), or when it contains exactly one Python script.
    Helper files are not split into separate cards when an entrypoint exists.
    """
    root = root.expanduser().resolve(strict=False)
    python_files = _python_files_under(root)
    if not python_files:
        return []

    candidates: list[dict[str, Any]] = []
    default_by_id = {game.id: game for game in DEFAULT_GAMES}
    claimed_ids: set[str] = set()
    claimed_paths: set[str] = set()
    covered_directories: list[Path] = []

    def add_candidate(script_path: Path, *, game_folder: Path | None = None) -> None:
        resolved_script = script_path.resolve(strict=False)
        script_key = str(resolved_script)
        if script_key in claimed_paths:
            return
        game_id = _game_slug(game_folder.name if game_folder is not None else script_path.stem)
        if game_id in claimed_ids:
            # A game folder is more informative than a duplicate root-level file.
            existing = next((item for item in candidates if item["id"] == game_id), None)
            if existing and game_folder is not None and not existing.get("is_folder"):
                candidates.remove(existing)
                claimed_paths.discard(existing["script_path"])
                claimed_ids.discard(game_id)
            else:
                return
        default_game = default_by_id.get(game_id)
        raw_title = game_folder.name if game_folder is not None else script_path.stem
        title = default_game.title if default_game else re.sub(r"[_-]+", " ", raw_title).strip().title()
        candidates.append({
            "id": game_id,
            "title": title,
            "script_path": script_key,
            "root": str(root),
            "is_folder": game_folder is not None,
        })
        claimed_ids.add(game_id)
        claimed_paths.add(script_key)

    for script_path in python_files:
        if script_path.parent == root:
            add_candidate(script_path)

    # Include ancestor directories so a game such as games/platformer/src/main.py
    # is named "platformer", not "src". If a folder contains several independent
    # game entrypoints, discovery proceeds into those child folders instead.
    directory_set: set[Path] = set()
    for script_path in python_files:
        parent = script_path.parent
        while parent != root and root in parent.parents:
            directory_set.add(parent)
            parent = parent.parent
    directories = sorted(
        directory_set,
        key=lambda directory: (len(directory.relative_to(root).parts), str(directory).casefold()),
    )

    for directory in directories:
        if any(directory == covered or covered in directory.parents for covered in covered_directories):
            continue

        direct_scripts = sorted(
            (path for path in python_files if path.parent == directory),
            key=lambda path: path.name.casefold(),
        )
        by_name = {path.name.casefold(): path for path in direct_scripts}
        preferred_script = next((by_name[name] for name in GAME_ENTRYPOINT_NAMES if name in by_name), None)
        same_name_script = by_name.get(f"{directory.name.casefold()}.py")
        selected_script = preferred_script or same_name_script
        if selected_script is None and len(direct_scripts) == 1:
            selected_script = direct_scripts[0]

        if selected_script is None and not direct_scripts:
            descendants = [path for path in python_files if directory in path.parents]
            preferred_descendants = [
                path for path in descendants
                if path.name.casefold() in GAME_ENTRYPOINT_NAMES
                or path.stem.casefold() == directory.name.casefold()
            ]
            if preferred_descendants:
                # Group multiple likely entrypoints only when they live in the
                # same direct subfolder; separate sibling games stay separate.
                branch_names = {
                    path.relative_to(directory).parts[0]
                    for path in preferred_descendants
                    if path.relative_to(directory).parts
                }
                if len(branch_names) == 1:
                    selected_script = next(
                        (path for name in GAME_ENTRYPOINT_NAMES for path in preferred_descendants
                         if path.name.casefold() == name),
                        preferred_descendants[0],
                    )
            elif len(descendants) == 1:
                selected_script = descendants[0]

        if selected_script is not None:
            add_candidate(selected_script, game_folder=directory)
            covered_directories.append(directory)
        elif direct_scripts:
            # No obvious entrypoint: keep the Python files discoverable separately.
            for script_path in direct_scripts:
                add_candidate(script_path)

    return candidates


class AppConfig:
    def __init__(self) -> None:
        self.path = app_data_dir() / "config.json"
        self.python_executable = find_default_python()
        self.preferred_port = ""
        installer_defaults = read_installer_defaults()
        initial_source = str(installer_defaults.get("default_source") or "games")
        self.default_source = self._resolve_source(initial_source)
        self.games: list[GameEntry] = [GameEntry(**asdict(item)) for item in DEFAULT_GAMES]

        installer_game_paths = installer_defaults.get("game_paths", {})
        self.installer_game_paths = installer_game_paths if isinstance(installer_game_paths, dict) else {}
        for game in self.games:
            configured_relative_path = self.installer_game_paths.get(game.id)
            if isinstance(configured_relative_path, str) and configured_relative_path.strip():
                game.source_path = configured_relative_path.strip()
        self.load()

    @staticmethod
    def _resolve_source(source: str) -> str:
        candidate = Path(source).expanduser()
        if not candidate.is_absolute():
            # In the common MoveUp layout the launcher lives in app/launcher and
            # the games live next to launcher in app/games (the requested ../games).
            if candidate.as_posix().strip("./") == "games":
                preferred_roots = [app_dir().parent / "games", app_dir() / "games"]
                candidate = next((root for root in preferred_roots if root.is_dir()), preferred_roots[0])
            else:
                candidate = app_dir() / candidate
        return str(candidate.resolve(strict=False))

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        try:
            with self.path.open("r", encoding="utf-8") as file:
                raw = json.load(file)
            if not isinstance(raw, dict):
                raise ValueError("config.json должен содержать JSON-объект")
            self.python_executable = str(raw.get("python_executable") or self.python_executable)
            self.preferred_port = str(raw.get("preferred_port") or "")
            configured_source = raw.get("default_source")
            if isinstance(configured_source, str) and configured_source.strip():
                self.default_source = self._resolve_source(configured_source)
            game_data = raw.get("games")
            if isinstance(game_data, list):
                self.games = [GameEntry.from_dict(item) for item in game_data if isinstance(item, dict)]
                # Migrate configurations created before default-source paths existed.
                defaults_by_id = {item.id: item for item in DEFAULT_GAMES}
                for game in self.games:
                    if not game.source_path and not game.path and game.id in defaults_by_id:
                        game.source_path = str(
                            self.installer_game_paths.get(game.id)
                            or defaults_by_id[game.id].source_path
                        )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Keep usable defaults if a config file is damaged.
            self.games = [GameEntry(**asdict(item)) for item in DEFAULT_GAMES]

    def save(self) -> None:
        payload = {
            "python_executable": self.python_executable,
            "preferred_port": self.preferred_port,
            "default_source": self.default_source,
            "games": [asdict(game) for game in self.games],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            temp_path.replace(self.path)
        except OSError as error:
            raise RuntimeError(f"Не удалось сохранить настройки: {error}") from error


class StatisticsStore:
    """Keeps a persistent ledger and imports game-authored sessions from ../games/statistics.json.

    The shared games file is always read-only. Imported sessions are copied into the user's
    application data so history remains available if the games folder is temporarily offline.
    """

    SCHEMA_VERSION = 1
    EXTERNAL_MATCH_WINDOW_SECONDS = 180

    def __init__(self) -> None:
        self.path = app_data_dir() / "statistics.json"
        self.results_dir = app_data_dir() / "game_results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.external_path = games_statistics_path()
        self.external_available = False
        self.external_last_error = ""
        self.external_session_count = 0
        self._external_fingerprint: tuple[int, int] | None = None
        self.data: dict[str, Any] = {"schema_version": self.SCHEMA_VERSION, "sessions": []}
        self.load()

    def load(self) -> None:
        if self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict) and isinstance(loaded.get("sessions", []), list):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                # Preserve a usable launcher if the local cache is damaged.
                self.data = {"schema_version": self.SCHEMA_VERSION, "sessions": []}
        self.data["schema_version"] = self.SCHEMA_VERSION
        self.data.setdefault("created_at", iso_timestamp())
        raw_sessions = self.data.setdefault("sessions", [])
        sessions = [item for item in raw_sessions if isinstance(item, dict)] if isinstance(raw_sessions, list) else []
        self.data["sessions"] = sessions
        now = utc_now()
        changed = not isinstance(raw_sessions, list) or len(sessions) != len(raw_sessions)
        # A previous running marker means the launcher ended without finalizing that session.
        for session in sessions:
            if not isinstance(session, dict) or session.get("status") != "running":
                continue
            started = parse_timestamp(session.get("started_at"))
            last_seen = parse_timestamp(session.get("last_seen_at")) or started
            if started and last_seen:
                measured = max(0, int((last_seen - started).total_seconds()))
                session["duration_seconds"] = max(int(session.get("duration_seconds") or 0), measured)
                session["ended_at"] = iso_timestamp(last_seen)
            else:
                session["ended_at"] = iso_timestamp(now)
            session["status"] = "interrupted"
            session["interruption_reason"] = "launcher_restarted_without_session_finalization"
            session.pop("last_seen_at", None)
            changed = True
        changed = self._merge_pending_results() or changed
        changed = self.sync_external_statistics(force=True, save_changes=False) or changed
        if changed or not self.path.exists():
            self.save()

    def sync_external_statistics(self, *, force: bool = False, save_changes: bool = True) -> bool:
        """Import newly written/updated game sessions, without changing the shared source file."""
        try:
            stat = self.external_path.stat()
            if not self.external_path.is_file():
                self.external_available = False
                return False
            fingerprint = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            self.external_available = False
            self.external_last_error = "Файл статистики игр пока не найден или недоступен."
            return False

        self.external_available = True
        if not force and fingerprint == self._external_fingerprint:
            return False

        try:
            with self.external_path.open("r", encoding="utf-8") as file:
                external_data = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            self.external_last_error = f"Не удалось прочитать файл статистики: {error}"
            return False

        if not isinstance(external_data, dict) or not isinstance(external_data.get("sessions"), list):
            self.external_last_error = "Файл статистики имеет неверный формат: ожидался объект с массивом sessions."
            return False

        external_sessions = [
            item for item in external_data["sessions"]
            if isinstance(item, dict) and item.get("session_id") and item.get("game_id")
        ]
        local_sessions = self.data.setdefault("sessions", [])
        changed = False
        imported_count = 0

        # Deduplicate against prior imports and reconcile matching launcher-tracked runs.
        for external_session in sorted(
            external_sessions,
            key=lambda item: str(item.get("started_at") or "")
        ):
            external_id = str(external_session["session_id"])
            linked = next((
                item for item in local_sessions
                if isinstance(item, dict) and (
                    str(item.get("external_session_id") or "") == external_id
                    or (str(item.get("session_id") or "") == external_id
                        and item.get("record_source") == "games_statistics")
                )
            ), None)

            if linked is not None:
                self._replace_with_external(linked, external_session, external_id)
                changed = True
                imported_count += 1
                continue

            matching_launcher_session = self._find_matching_launcher_session(external_session, local_sessions)
            if matching_launcher_session is not None:
                launcher_session_id = str(matching_launcher_session.get("session_id") or "")
                matching_launcher_session.clear()
                matching_launcher_session.update(external_session)
                # Keep the launcher's id for any live process callbacks, and remember source id for dedup.
                matching_launcher_session["session_id"] = launcher_session_id or external_id
                matching_launcher_session["launcher_session_id"] = launcher_session_id or external_id
                matching_launcher_session["external_session_id"] = external_id
                matching_launcher_session["record_source"] = "games_statistics"
                changed = True
                imported_count += 1
                continue

            imported = dict(external_session)
            imported["session_id"] = external_id
            imported["external_session_id"] = external_id
            imported["record_source"] = "games_statistics"
            local_sessions.append(imported)
            changed = True
            imported_count += 1

        self._external_fingerprint = fingerprint
        self.external_session_count = len(external_sessions)
        self.external_last_error = ""
        self.data["external_statistics_path"] = str(self.external_path)
        self.data["external_statistics_last_sync"] = iso_timestamp()
        if changed and save_changes:
            self.save()
        return changed

    def _replace_with_external(
        self, target: dict[str, Any], external_session: dict[str, Any], external_id: str
    ) -> None:
        launcher_session_id = str(target.get("launcher_session_id") or "")
        if not launcher_session_id and target.get("record_source") != "games_statistics":
            launcher_session_id = str(target.get("session_id") or "")
        target.clear()
        target.update(external_session)
        target["session_id"] = launcher_session_id or external_id
        target["external_session_id"] = external_id
        target["record_source"] = "games_statistics"
        if launcher_session_id:
            target["launcher_session_id"] = launcher_session_id

    def _find_matching_launcher_session(
        self, external_session: dict[str, Any], sessions: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        external_game_id = str(external_session.get("game_id") or "")
        external_start = parse_timestamp(external_session.get("started_at"))
        if not external_game_id or not external_start:
            return None

        candidates: list[tuple[float, dict[str, Any]]] = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if session.get("record_source") == "games_statistics" or session.get("external_session_id"):
                continue
            if session.get("game_id") != external_game_id:
                continue
            local_start = parse_timestamp(session.get("started_at"))
            if not local_start:
                continue
            time_delta = abs((local_start - external_start).total_seconds())
            if time_delta <= self.EXTERNAL_MATCH_WINDOW_SECONDS:
                candidates.append((time_delta, session))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def _merge_pending_results(self) -> bool:
        """Recover metrics written by the currently running integration helper."""
        changed = False
        for result_path in self.results_dir.glob("*.json"):
            try:
                with result_path.open("r", encoding="utf-8") as result_file:
                    result_data = json.load(result_file)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(result_data, dict):
                continue
            session_id = str(result_data.get("session_id") or result_path.stem)
            session = self._find_session(session_id)
            if not session:
                continue
            matches_game = not result_data.get("game_id") or result_data.get("game_id") == session.get("game_id")
            metrics = result_data.get("metrics", {})
            if matches_game and isinstance(metrics, dict):
                session["metrics"] = metrics
                changed = True
                try:
                    result_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return changed

    def save(self) -> None:
        self.data["updated_at"] = iso_timestamp()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
        temporary_path.replace(self.path)

    def start_session(self, game: GameEntry) -> tuple[str, Path]:
        session_id = str(uuid.uuid4())
        result_path = self.results_dir / f"{session_id}.json"
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        started_at = iso_timestamp()
        self.data["sessions"].append({
            "session_id": session_id,
            "game_id": game.id,
            "game_title": game.title,
            "started_at": started_at,
            "ended_at": None,
            "duration_seconds": 0,
            "status": "running",
            "exit_code": None,
            "metrics": {},
            "last_seen_at": started_at,
            "record_source": "launcher",
        })
        self.save()
        return session_id, result_path

    def _find_session(self, session_id: str) -> dict[str, Any] | None:
        return next((
            item for item in self.data.get("sessions", [])
            if isinstance(item, dict) and session_id in {
                str(item.get("session_id") or ""),
                str(item.get("launcher_session_id") or ""),
                str(item.get("external_session_id") or ""),
            }
        ), None)

    def heartbeat_running_sessions(self, session_ids: set[str] | None = None) -> None:
        now = utc_now()
        changed = False
        for session in self.data.get("sessions", []):
            if not isinstance(session, dict) or session.get("status") != "running":
                continue
            if session_ids is not None and session.get("session_id") not in session_ids:
                continue
            started = parse_timestamp(session.get("started_at"))
            if started:
                session["duration_seconds"] = max(0, int((now - started).total_seconds()))
            session["last_seen_at"] = iso_timestamp(now)
            changed = True
        if changed:
            self.save()

    def finish_session(self, session_id: str, exit_code: int, result_path: Path | None = None) -> None:
        session = self._find_session(session_id)
        if not session:
            return
        if result_path and result_path.is_file():
            try:
                with result_path.open("r", encoding="utf-8") as file:
                    result_data = json.load(file)
                if isinstance(result_data, dict):
                    matches_session = not result_data.get("session_id") or result_data.get("session_id") == session_id
                    matches_game = not result_data.get("game_id") or result_data.get("game_id") == session.get("game_id")
                    metrics = result_data.get("metrics", {})
                    if (matches_session and matches_game and isinstance(metrics, dict)
                            and (session.get("record_source") != "games_statistics" or not session.get("metrics"))):
                        session["metrics"] = metrics
            except (OSError, json.JSONDecodeError):
                session["metrics_error"] = "invalid_or_unreadable_result_file"
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
        now = utc_now()
        started = parse_timestamp(session.get("started_at"))
        if started:
            measured = max(0, int((now - started).total_seconds()))
            session["duration_seconds"] = max(int(session.get("duration_seconds") or 0), measured)
        session["ended_at"] = iso_timestamp(now)
        session["exit_code"] = int(exit_code)
        # If the game authored a matching detailed session, keep its status as the authoritative outcome.
        if session.get("record_source") != "games_statistics":
            session["status"] = "failed_to_start" if exit_code == -1 else ("completed" if exit_code == 0 else "failed")
        session.pop("last_seen_at", None)
        self.save()

    def close_tracking(self, session_id: str) -> None:
        """Finalize tracked time if the launcher closes while the child game may remain open."""
        session = self._find_session(session_id)
        if not session or session.get("status") != "running":
            return
        now = utc_now()
        started = parse_timestamp(session.get("started_at"))
        if started:
            session["duration_seconds"] = max(0, int((now - started).total_seconds()))
        session["ended_at"] = iso_timestamp(now)
        session["status"] = "launcher_closed"
        session["interruption_reason"] = "launcher_closed_while_game_may_still_be_running"
        session.pop("last_seen_at", None)
        self.save()

    @staticmethod
    def _duration(session: dict[str, Any]) -> int:
        started = parse_timestamp(session.get("started_at"))
        if session.get("status") == "running" and started:
            return max(0, int((utc_now() - started).total_seconds()))
        try:
            return max(0, int(float(session.get("duration_seconds") or 0)))
        except (TypeError, ValueError):
            return 0

    def session_count(self) -> int:
        return sum(1 for item in self.data.get("sessions", [])
                   if isinstance(item, dict) and item.get("status") != "failed_to_start")

    def total_duration(self) -> int:
        return sum(self._duration(item) for item in self.data.get("sessions", [])
                   if isinstance(item, dict) and item.get("status") != "failed_to_start")

    def game_summary(self, game_id: str) -> dict[str, Any]:
        sessions = [item for item in self.data.get("sessions", [])
                    if isinstance(item, dict) and item.get("game_id") == game_id and item.get("status") != "failed_to_start"]
        sessions.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        return {
            "launch_count": len(sessions),
            "total_duration_seconds": sum(self._duration(item) for item in sessions),
            "latest_metrics": next((item.get("metrics", {}) for item in sessions if item.get("metrics")), {}),
            "last_session": sessions[0] if sessions else None,
        }

    def recent_sessions(self, limit: int = 8) -> list[dict[str, Any]]:
        sessions = [dict(item) for item in self.data.get("sessions", []) if isinstance(item, dict)]
        sessions.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        for session in sessions:
            session["duration_seconds"] = self._duration(session)
        return sessions[:limit]

    def activity_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        today = utc_now().astimezone().date()
        day_dates = [today - timedelta(days=days - index - 1) for index in range(days)]
        totals = {day.isoformat(): 0 for day in day_dates}
        for session in self.data.get("sessions", []):
            if not isinstance(session, dict) or session.get("status") == "failed_to_start":
                continue
            started = parse_timestamp(session.get("started_at"))
            if started:
                day_key = started.astimezone().date().isoformat()
                if day_key in totals:
                    totals[day_key] += self._duration(session)
        return [(day.strftime("%d.%m"), totals[day.isoformat()] // 60) for day in day_dates]


class SoftPanel(QFrame):
    def __init__(self, *, object_name: str = "panel", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.NoFrame)


class HeroBanner(QFrame):
    """Decorative hero panel with a custom-drawn abstract movement path."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setMaximumHeight(290)
        self.setObjectName("heroBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QFrame#heroBanner { border-radius: 22px; }")

        content = QHBoxLayout(self)
        content.setContentsMargins(30, 25, 25, 24)
        content.setSpacing(15)

        text_column = QVBoxLayout()
        text_column.setSpacing(11)
        eyebrow = QLabel("ДВИЖЕНИЕ • ВОССТАНОВЛЕНИЕ • ИГРА")
        eyebrow.setObjectName("heroEyebrow")
        title = QLabel("Реабилитация без скуки")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        text_column.addWidget(eyebrow)
        text_column.addWidget(title)
        text_column.addStretch(1)
        content.addLayout(text_column, 3)

        art_column = QVBoxLayout()
        art_column.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        art_column.setSpacing(5)
        art_label = QLabel("MOVE\nUP")
        art_label.setObjectName("heroMark")
        art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_label.setMinimumWidth(135)
        art_label.setMinimumHeight(132)
        art_label.setStyleSheet(
            "QLabel#heroMark { color: #F3F5F4; background: rgba(169,200,216,0.10); "
            "border: 1px solid rgba(194,220,232,0.26); border-radius: 28px; "
            "font-size: 25px; font-weight: 800; letter-spacing: 5px; }"
        )
        small_label = QLabel("MOTION TRAINING SYSTEM")
        small_label.setObjectName("heroSmall")
        small_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_column.addWidget(art_label, 0, Qt.AlignmentFlag.AlignRight)
        art_column.addWidget(small_label, 0, Qt.AlignmentFlag.AlignRight)
        content.addLayout(art_column, 1)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0.0, QColor("#304858"))
        gradient.setColorAt(0.55, QColor("#263B4A"))
        gradient.setColorAt(1.0, QColor("#1D2C38"))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(rect), 22, 22)

        # Soft ambient circles.
        painter.setBrush(QColor(169, 200, 216, 14))
        painter.drawEllipse(QPointF(rect.width() * 0.80, rect.height() * 0.15), 125, 125)
        painter.setBrush(QColor(232, 198, 106, 10))
        painter.drawEllipse(QPointF(rect.width() * 0.91, rect.height() * 0.90), 95, 95)

        # A route-like line inspired by rehabilitation motion tracking.
        path = QPainterPath()
        path.moveTo(rect.width() * 0.48, rect.height() * 0.88)
        path.cubicTo(rect.width() * 0.58, rect.height() * 0.72,
                     rect.width() * 0.58, rect.height() * 0.32,
                     rect.width() * 0.70, rect.height() * 0.44)
        path.cubicTo(rect.width() * 0.77, rect.height() * 0.51,
                     rect.width() * 0.77, rect.height() * 0.79,
                     rect.width() * 0.86, rect.height() * 0.68)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(169, 200, 216, 38), 2.0, Qt.PenStyle.DashLine))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, radius in [
            (0.70, 0.44, 5), (0.86, 0.68, 6), (0.48, 0.88, 4), (0.58, 0.32, 3)
        ]:
            painter.setBrush(QColor(194, 220, 232, 105))
            painter.drawEllipse(QPointF(rect.width() * x, rect.height() * y), radius, radius)
        painter.end()
        super().paintEvent(event)


class GameArtwork(QWidget):
    """Game cover: prefers launcher/assets/<game_id> image, then draws vector fallback."""
    def __init__(self, art: str, accent: str, game_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.art = art
        self.game_id = game_id
        self.accent = QColor(accent)
        self.cover_path = self._find_cover()
        self.setMinimumSize(160, 90)
        self.setMaximumHeight(250)
        artwork_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        artwork_policy.setHeightForWidth(True)
        self.setSizePolicy(artwork_policy)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return max(90, round(width * 9 / 16))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(320, 180)

    def _find_cover(self) -> Path | None:
        assets_dir = app_dir() / "assets"
        if not assets_dir.is_dir():
            return None

        # The game ID and image filename do not always use the same spelling:
        # path_finder -> path-finder.png, neon_highway/race -> race.png.
        identifiers = [self.game_id, self.art]
        aliases = {
            "neon_highway": ("race", "racing", "neon-highway"),
            "race": ("neon_highway", "neon-highway"),
            "path_finder": ("path-finder", "pathfinder"),
            "path-finder": ("path_finder", "pathfinder"),
        }
        expanded_identifiers: list[str] = []
        for identifier in identifiers:
            if not identifier:
                continue
            expanded_identifiers.append(identifier)
            expanded_identifiers.extend(aliases.get(identifier.casefold(), ()))

        extensions = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        for identifier in expanded_identifiers:
            for extension in extensions:
                candidate = assets_dir / f"{identifier}{extension}"
                if candidate.is_file():
                    return candidate

        # Final case-insensitive fallback: treat hyphens and underscores as equal.
        def normalize_name(value: str) -> str:
            return re.sub(r"[-_ ]+", "", value.casefold())

        wanted_names = {normalize_name(identifier) for identifier in expanded_identifiers}
        for candidate in assets_dir.iterdir():
            if candidate.is_file() and candidate.suffix.casefold() in extensions:
                if normalize_name(candidate.stem) in wanted_names:
                    return candidate
        return None

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        scale = max(0.65, min(rect.width(), rect.height()) / 120.0)
        if self.cover_path:
            pixmap = QPixmap(str(self.cover_path))
            if not pixmap.isNull():
                clip_path = QPainterPath()
                clip_path.addRoundedRect(QRectF(rect), 14, 14)
                painter.setClipPath(clip_path)
                scaled_pixmap = pixmap.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Crop first, then draw the cropped pixmap. This avoids PySide6
                # rejecting a mixed QRect/QRectF overload or an out-of-bounds
                # source rectangle caused by scaling/rounding differences.
                crop_width = min(rect.width(), scaled_pixmap.width())
                crop_height = min(rect.height(), scaled_pixmap.height())
                source_x = max(0, (scaled_pixmap.width() - crop_width) // 2)
                source_y = max(0, (scaled_pixmap.height() - crop_height) // 2)
                source_rect = QRect(source_x, source_y, crop_width, crop_height)
                cropped_pixmap = scaled_pixmap.copy(source_rect)
                painter.drawPixmap(rect, cropped_pixmap)
                painter.end()
                return
        background = QLinearGradient(0, 0, rect.width(), rect.height())
        background.setColorAt(0, QColor("#385565"))
        background.setColorAt(1, QColor("#1D2C37"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(QRectF(rect), 14, 14)
        painter.setBrush(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 24))
        painter.drawEllipse(QPointF(rect.width() * .77, rect.height() * .46), 55 * scale, 55 * scale)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 90), 1.5))

        if self.art == "maze":
            x0, y0 = rect.width() * .52, rect.height() * .20
            width, height = rect.width() * .35, rect.height() * .58
            lines = [
                (x0, y0, x0 + width, y0),
                (x0, y0, x0, y0 + height * .68),
                (x0 + width * .36, y0, x0 + width * .36, y0 + height * .50),
                (x0 + width * .36, y0 + height * .50, x0 + width, y0 + height * .50),
                (x0 + width, y0 + height * .50, x0 + width, y0 + height),
                (x0, y0 + height * .68, x0 + width * .65, y0 + height * .68),
                (x0 + width * .65, y0 + height * .68, x0 + width * .65, y0 + height),
                (x0 + width * .65, y0 + height, x0 + width, y0 + height),
            ]
            for x1, y1, x2, y2 in lines:
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            painter.setBrush(self.accent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(x0 + width * .17, y0 + height * .87), 4, 4)
        elif self.art == "target":
            cx, cy = rect.width() * .72, rect.height() * .51
            for radius in (36, 25, 14):
                painter.drawEllipse(QPointF(cx, cy), radius * scale, radius * scale)
            painter.setPen(QPen(self.accent, max(1.4, 2 * scale)))
            painter.drawLine(QPointF(cx - 42 * scale, cy + 32 * scale), QPointF(cx + 13 * scale, cy - 9 * scale))
            painter.setBrush(self.accent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx + 13 * scale, cy - 9 * scale), 4 * scale, 4 * scale)
        else:
            for row in range(3):
                for col in range(5):
                    alpha = 155 if (row + col) % 3 != 0 else 65
                    painter.setBrush(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), alpha))
                    painter.setPen(Qt.PenStyle.NoPen)
                    dot_size = 7 * scale
                    painter.drawRoundedRect(QRectF(rect.width() * .54 + col * 17 * scale,
                                                   rect.height() * .25 + row * 19 * scale, dot_size, dot_size), 3 * scale, 3 * scale)
        painter.end()


class GameCard(SoftPanel):
    def __init__(self, game: GameEntry, launch_callback, configure_callback, path_callback, menu_callback, game_stats=None, stats_callback=None, parent=None) -> None:
        super().__init__(object_name="gameCard", parent=parent)
        self.game = game
        self.launch_callback = launch_callback
        self.configure_callback = configure_callback
        self.path_callback = path_callback
        self.menu_callback = menu_callback
        self.stats_callback = stats_callback
        self.setMaximumWidth(460)
        self.setMaximumHeight(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(11)

        self.artwork = GameArtwork(game.art, game.accent, game.id)
        layout.addWidget(self.artwork)

        meta_row = QHBoxLayout()
        category = QLabel(game.category.upper())
        category.setObjectName("microLabel")
        category.setStyleSheet(f"color: {game.accent};")
        meta_row.addWidget(category)
        meta_row.addStretch(1)
        status_text = "ГОТОВА К ЗАПУСКУ" if self._valid_path() else "НУЖНО НАСТРОИТЬ"
        status = QLabel(status_text)
        status.setObjectName("microLabel")
        status.setStyleSheet("color: #8CCDAA;" if self._valid_path() else "color: #AABAC4;")
        meta_row.addWidget(status)
        layout.addLayout(meta_row)

        title = QLabel(game.title)
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        description = QLabel(game.description)
        description.setObjectName("bodyMuted")
        description.setWordWrap(True)
        description.setMinimumHeight(38)
        description.setMaximumHeight(54)
        layout.addWidget(title)
        layout.addWidget(description)
        stats = game_stats or {}
        statistics_line = QLabel(f"{stats.get('launch_count', 0)} запусков  ·  {format_duration(stats.get('total_duration_seconds', 0))}")
        statistics_line.setObjectName("gameStatsSmall")
        layout.addWidget(statistics_line)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.launch_button = QPushButton("▶  Запустить" if self._valid_path() else "Настроить запуск")
        self.launch_button.setObjectName("primaryButton")
        self.launch_button.clicked.connect(lambda: self.launch_callback(self.game.id))
        stats_button = QPushButton("↗")
        stats_button.setObjectName("iconButton")
        stats_button.setFixedSize(40, 38)
        stats_button.setToolTip("Подробная статистика этой игры")
        if self.stats_callback:
            stats_button.clicked.connect(lambda: self.stats_callback(self.game.id))
        else:
            stats_button.setEnabled(False)
        settings_button = QPushButton("•••")
        settings_button.setObjectName("iconButton")
        settings_button.setFixedSize(40, 38)
        settings_button.setToolTip("Путь к игре и другие действия")
        settings_button.clicked.connect(lambda: self.menu_callback(self.game.id, settings_button))
        buttons.addWidget(self.launch_button, 1)
        buttons.addWidget(stats_button)
        buttons.addWidget(settings_button)
        layout.addLayout(buttons)

    def _valid_path(self) -> bool:
        path = self.path_callback(self.game)
        return bool(path and path.is_file() and path.suffix.lower() in {".py", ".exe"})


class HomeGameTile(SoftPanel):
    def __init__(self, game: GameEntry, launch_callback, game_stats: dict[str, Any], stats_callback=None, parent=None) -> None:
        super().__init__(object_name="homeGameTile", parent=parent)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 13)
        layout.setSpacing(8)
        artwork = GameArtwork(game.art, game.accent, game.id)
        layout.addWidget(artwork)
        category = QLabel(game.category.upper())
        category.setObjectName("microLabel")
        category.setStyleSheet(f"color: {game.accent};")
        title = QLabel(game.title)
        title.setObjectName("tileTitle")
        title.setWordWrap(True)
        summary = QLabel(f"{game_stats.get('launch_count', 0)} запусков  ·  {format_duration(game_stats.get('total_duration_seconds', 0))}")
        summary.setObjectName("bodyMuted")
        summary.setWordWrap(True)
        layout.addWidget(category)
        layout.addWidget(title)
        layout.addWidget(summary)
        action_row = QHBoxLayout()
        action_row.setSpacing(7)
        launch_button = QPushButton("Играть  →")
        launch_button.setObjectName("secondaryButton")
        launch_button.clicked.connect(lambda: launch_callback(game.id))
        action_row.addWidget(launch_button, 1)
        stats_button = QPushButton("↗")
        stats_button.setObjectName("iconButton")
        stats_button.setFixedSize(38, 36)
        stats_button.setToolTip("Статистика этой игры")
        if stats_callback:
            stats_button.clicked.connect(lambda: stats_callback(game.id))
        action_row.addWidget(stats_button)
        layout.addLayout(action_row)


class AddGameDialog(QDialog):
    def __init__(self, parent=None, game: GameEntry | None = None, default_source_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройка игры" if game else "Добавить игру")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.game = game
        self.default_source_path = default_source_path

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(15)
        heading = QLabel("Настроить игру" if game else "Новая игра")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        self.title_edit = QLineEdit(game.title if game else "")
        self.title_edit.setPlaceholderText("Например, Лабиринт")
        self.description_edit = QLineEdit(game.description if game else "")
        self.description_edit.setPlaceholderText("Краткое описание игры")
        self.category_combo = QComboBox()
        self.category_combo.addItems(["Координация", "Точность", "Внимание", "Моторика", "Реакция", "Логика", "Другое"])
        if game and game.category:
            index = self.category_combo.findText(game.category)
            if index < 0:
                self.category_combo.addItem(game.category)
                index = self.category_combo.findText(game.category)
            self.category_combo.setCurrentIndex(index)
        self.path_edit = QLineEdit(game.path if game else "")
        self.path_edit.setPlaceholderText("Пусто — использовать файл из каталога по умолчанию")
        browse_button = QPushButton("Обзор…")
        browse_button.setObjectName("secondaryButton")
        browse_button.clicked.connect(self._browse_file)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_button)
        self.path_hint = QLabel(self._default_path_hint())
        self.path_hint.setObjectName("bodyMuted")
        self.path_hint.setWordWrap(True)
        self.args_edit = QLineEdit(game.args if game else "")
        self.args_edit.setPlaceholderText("Необязательно: аргументы запуска")
        form.addRow("Название", self.title_edit)
        form.addRow("Описание", self.description_edit)
        form.addRow("Категория", self.category_combo)
        form.addRow("Файл игры", path_row)
        form.addRow("", self.path_hint)
        form.addRow("Аргументы", self.args_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _default_path_hint(self) -> str:
        if self.game and not self.game.path and self.game.source_path and self.default_source_path:
            return f"По умолчанию: {self.default_source_path / self.game.source_path}\nОставь поле пустым, чтобы использовать этот файл."
        if self.game and not self.game.path:
            return "Путь не задан. Настрой общий каталог игр в настройках или укажи отдельный файл."
        if self.game:
            return "Можно указать отдельный файл. Пустое поле использует каталог игр по умолчанию, если для сценария задан относительный путь."
        return "Укажи файл игры. Для новых сценариев путь задаётся индивидуально."

    def _browse_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл игры",
            str(self.default_source_path or Path.home()),
            "Игры и скрипты (*.py *.exe);;Python (*.py);;Программы (*.exe);;Все файлы (*)",
        )
        if selected:
            self.path_edit.setText(selected)
            if not self.title_edit.text().strip():
                self.title_edit.setText(Path(selected).stem.replace("_", " ").title())

    def _validate_and_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "Не указано название", "Введите название игры.")
            self.title_edit.setFocus()
            return
        if not self.path_edit.text().strip() and not (self.game and self.game.source_path and self.default_source_path):
            QMessageBox.warning(self, "Не выбран файл", "Укажите файл .py или .exe либо задайте файл в каталоге игр по умолчанию.")
            self.path_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.text().strip() or "Описание пока не добавлено.",
            "category": self.category_combo.currentText(),
            "path": str(Path(self.path_edit.text().strip()).expanduser()) if self.path_edit.text().strip() else "",
            "args": self.args_edit.text().strip(),
        }


class ActivityChart(QWidget):
    """Lightweight custom chart; no external plotting library is required."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.values: list[tuple[str, int]] = []
        self.setObjectName("activityChart")
        self.setMinimumHeight(190)
        self.setMaximumHeight(235)

    def set_data(self, values: list[tuple[str, int]]) -> None:
        self.values = values
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.fillRect(rect, QColor("#1B2B36"))
        left, right, top, bottom = 42.0, rect.width() - 12.0, 14.0, rect.height() - 29.0
        chart_width = max(1.0, right - left)
        chart_height = max(1.0, bottom - top)
        max_value = max([value for _label, value in self.values] or [0])
        scale_max = max(30, ((max_value + 14) // 15) * 15)
        painter.setFont(QFont("Inter", 8))
        for grid_index in range(4):
            y = top + chart_height * grid_index / 3
            painter.setPen(QPen(QColor(169, 200, 216, 25), 1))
            painter.drawLine(QPointF(left, y), QPointF(right, y))
            tick = int(scale_max * (3 - grid_index) / 3)
            painter.setPen(QColor("#8097A4"))
            painter.drawText(0, int(y - 7), int(left - 8), 16, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(tick))

        if self.values:
            point_count = len(self.values)
            points: list[QPointF] = []
            for index, (_label, value) in enumerate(self.values):
                x = left + chart_width * index / max(1, point_count - 1)
                y = bottom - min(scale_max, max(0, value)) / scale_max * chart_height
                points.append(QPointF(x, y))
            if points:
                line_path = QPainterPath(points[0])
                for point in points[1:]:
                    line_path.lineTo(point)
                fill_path = QPainterPath(points[0])
                for point in points[1:]:
                    fill_path.lineTo(point)
                fill_path.lineTo(QPointF(points[-1].x(), bottom))
                fill_path.lineTo(QPointF(points[0].x(), bottom))
                fill_path.closeSubpath()
                gradient = QLinearGradient(0, top, 0, bottom)
                gradient.setColorAt(0, QColor(87, 174, 231, 85))
                gradient.setColorAt(1, QColor(87, 174, 231, 4))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(gradient))
                painter.drawPath(fill_path)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#69B8F3"), 2.5))
                painter.drawPath(line_path)
                painter.setPen(Qt.PenStyle.NoPen)
                for point in points:
                    painter.setBrush(QColor("#C2DCE8"))
                    painter.drawEllipse(point, 3.2, 3.2)
            for index, (label, _value) in enumerate(self.values):
                x = left + chart_width * index / max(1, point_count - 1)
                painter.setPen(QColor("#8097A4"))
                painter.drawText(int(x - 26), int(bottom + 7), 52, 16, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, label)
        else:
            painter.setPen(QColor("#8097A4"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Данные появятся после первых тренировок")
        painter.end()


class MoveUpLauncher(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig()
        self.statistics = StatisticsStore()
        self._catalog_sync_error = ""
        self._sync_discovered_games()
        self.game_processes: dict[str, subprocess.Popen] = {}
        self.active_session_ids: dict[str, str] = {}
        self.session_result_paths: dict[str, Path] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.selected_stats_game_id = ""
        self.stats_return_page = "catalog"
        self._heartbeat_ticks = 0
        # These counters must exist before page builders call _update_home_metrics().
        self._external_sync_ticks = 0
        self.setWindowTitle(APP_NAME)
        icon_path = app_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(960, 620)
        self.resize(1220, 740)

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        main_layout.addWidget(self.sidebar)

        content_wrap = QWidget()
        content_wrap.setObjectName("contentWrap")
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_topbar())

        self.pages = QStackedWidget()
        self.pages.setObjectName("pagesStack")
        self.catalog_columns = 0
        self.home_page = self._build_home_page()
        self.catalog_page = self._build_catalog_page()
        self.results_page = self._build_results_page()
        self.settings_page = self._build_settings_page()
        self.game_stats_page = self._build_game_stats_page()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.catalog_page)
        self.pages.addWidget(self.results_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.game_stats_page)
        content_layout.addWidget(self.pages, 1)
        content_layout.addWidget(self._build_statusbar())
        main_layout.addWidget(content_wrap, 1)

        self.apply_styles()
        self.refresh_controller_status()
        self.show_page("home")

        self.controller_timer = QTimer(self)
        self.controller_timer.timeout.connect(self.refresh_controller_status)
        self.controller_timer.start(5000)
        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self._update_home_metrics)
        self.process_timer.start(1000)

    def _sync_discovered_games(self) -> int:
        """Merge scripts discovered on disk into the persistent catalog."""
        self._catalog_sync_error = ""
        defaults_by_id = {game.id: game for game in DEFAULT_GAMES}
        previous_games = [asdict(game) for game in self.config.games]
        default_root = Path(self.config.default_source).expanduser().resolve(strict=False)
        candidate_roots = [
            default_root,
            (app_dir().parent / "games").resolve(strict=False),
            (app_dir() / "games").resolve(strict=False),
        ]
        scan_roots: list[Path] = []
        seen_roots: set[str] = set()
        for root in candidate_roots:
            key = str(root)
            if key not in seen_roots and root.is_dir():
                scan_roots.append(root)
                seen_roots.add(key)

        sessions = [item for item in self.statistics.data.get("sessions", []) if isinstance(item, dict)]
        sessions.sort(key=lambda item: str(item.get("started_at") or ""))
        stats_titles: dict[str, str] = {}
        for session in sessions:
            game_id = str(session.get("game_id") or "")
            title = str(session.get("game_title") or "").strip()
            if game_id and title:
                stats_titles[game_id] = title

        discovered: list[dict[str, Any]] = []
        discovered_ids: set[str] = set()
        discovered_paths: set[str] = set()
        for root in scan_roots:
            for item in discover_game_files(root):
                game_id = str(item["id"])
                script_path = Path(str(item["script_path"])).resolve(strict=False)
                if game_id in discovered_ids or str(script_path) in discovered_paths:
                    continue
                source_path = (
                    str(script_path.relative_to(default_root))
                    if root == default_root and script_path.is_relative_to(default_root)
                    else str(script_path)
                )
                item["source_path"] = source_path
                item["script_path"] = str(script_path)
                discovered.append(item)
                discovered_ids.add(game_id)
                discovered_paths.add(str(script_path))

        added_count = 0
        current_games = list(self.config.games)
        game_by_id = {game.id: game for game in current_games}
        for item in discovered:
            game_id = str(item["id"])
            script_path = Path(str(item["script_path"])).resolve(strict=False)
            matching_game = game_by_id.get(game_id)
            if matching_game is None:
                matching_game = next((
                    game for game in current_games
                    if self.resolve_game_path(game) is not None
                    and self.resolve_game_path(game).resolve(strict=False) == script_path
                ), None)

            if matching_game is not None:
                # Normalize known aliases (for example race.py -> neon_highway)
                # so current launches and imported historical metrics share an ID.
                if matching_game.id != game_id and _game_slug(matching_game.id) == game_id:
                    game_by_id.pop(matching_game.id, None)
                    matching_game.id = game_id
                    game_by_id[game_id] = matching_game
                # Do not overwrite an intentional per-game override. Refresh the
                # default source path only when the game uses catalog discovery.
                if not matching_game.path.strip():
                    matching_game.source_path = str(item["source_path"])
                continue

            default_game = defaults_by_id.get(game_id)
            fallback_title = str(item.get("title") or game_id.replace("_", " ").title())
            title = stats_titles.get(game_id, fallback_title)
            category = default_game.category if default_game else "Другое"
            accent = default_game.accent if default_game else COLORS["blue"]
            art = default_game.art if default_game else "dots"
            if game_id in {"maze", "labyrinth"}:
                category, art = "Координация", "maze"
            elif game_id in {"shooter", "target"}:
                category, art = "Точность", "target"
            elif game_id in {"minefield", "puzzle"}:
                category, art = "Внимание", "dots"
            elif game_id in {"race", "racing", "neon_highway"}:
                category, art = "Реакция", "target"
            elif game_id == "platformer":
                category, art = "Координация", "maze"

            game = GameEntry(
                id=game_id,
                title=title,
                description=(default_game.description if default_game else
                             "Игра найдена автоматически. При необходимости описание можно изменить в настройках."),
                category=category,
                accent=accent,
                art=art,
                source_path=str(item["source_path"]),
            )
            current_games.append(game)
            game_by_id[game.id] = game
            added_count += 1

        # Remove broken built-in placeholders when their scripts are not actually
        # installed. User-created entries remain visible so their paths can be fixed.
        retained_games: list[GameEntry] = []
        for game in current_games:
            if game.id not in discovered_ids and not game.path.strip() and game.source_path.strip():
                resolved_path = self.resolve_game_path(game)
                if not resolved_path or not resolved_path.is_file():
                    # Automatically discovered entries disappear when their
                    # script is removed. Explicitly configured custom paths remain.
                    continue
            retained_games.append(game)

        self.config.games = retained_games
        current_snapshot = [asdict(game) for game in self.config.games]
        if current_snapshot != previous_games:
            try:
                self.config.save()
            except RuntimeError as error:
                self._catalog_sync_error = str(error)
        return added_count

    def refresh_game_library(self) -> None:
        """Rescan the configured and conventional ../games folders."""
        added_count = self._sync_discovered_games()
        self._refresh_views()
        if self._catalog_sync_error:
            QMessageBox.warning(self, "Библиотека обновлена не полностью", self._catalog_sync_error)
        else:
            QMessageBox.information(
                self,
                "Библиотека обновлена",
                f"Проверены папки игр. Игр в каталоге: {len(self.config.games)}. Новых обнаружено: {added_count}.",
            )

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 22, 18, 20)
        layout.setSpacing(8)

        brand = QHBoxLayout()
        brand.setSpacing(11)
        icon_path = app_icon_path()
        logo = QLabel("M")
        logo.setObjectName("brandLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(44, 44)
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                logo.setText("")
                logo.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        name = QLabel("MOVEUP")
        name.setObjectName("brandName")
        descriptor = QLabel("REHAB SYSTEM")
        descriptor.setObjectName("brandDescriptor")
        brand_text.addWidget(name)
        brand_text.addWidget(descriptor)
        brand.addWidget(logo)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)

        divider = QFrame()
        divider.setObjectName("sidebarDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(15)

        menu_items = [
            ("home", "⌂", "Главная"),
            ("catalog", "▦", "Игры"),
            ("results", "◷", "Результаты"),
        ]
        for key, icon, label in menu_items:
            button = QPushButton(f"{icon}     {label}")
            button.setObjectName("navButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page_key=key: self.show_page(page_key))
            self.nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        controller_card = SoftPanel(object_name="sideStatusCard")
        controller_layout = QVBoxLayout(controller_card)
        controller_layout.setContentsMargins(13, 13, 13, 13)
        controller_layout.setSpacing(7)
        row = QHBoxLayout()
        controller_icon = QLabel("⌁")
        controller_icon.setObjectName("controllerGlyph")
        row.addWidget(controller_icon)
        controller_title = QLabel("Контроллер")
        controller_title.setObjectName("sideStatusTitle")
        row.addWidget(controller_title, 1)
        controller_layout.addLayout(row)
        self.sidebar_controller_state = QLabel("Поиск устройства…")
        self.sidebar_controller_state.setObjectName("sideStatusText")
        self.sidebar_controller_state.setWordWrap(True)
        controller_layout.addWidget(self.sidebar_controller_state)
        controller_button = QPushButton("Проверить устройство")
        controller_button.setObjectName("textButton")
        controller_button.clicked.connect(self.refresh_controller_status)
        controller_layout.addWidget(controller_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(controller_card)

        settings_button = QPushButton("⚙     Настройки")
        settings_button.setObjectName("bottomNavButton")
        settings_button.clicked.connect(lambda: self.show_page("settings"))
        layout.addWidget(settings_button)

        version = QLabel(f"MoveUp Launcher  •  {APP_VERSION}")
        version.setObjectName("versionLabel")
        layout.addWidget(version)
        return sidebar

    def _build_topbar(self) -> QWidget:
        topbar = QWidget()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(78)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(28, 12, 28, 12)
        layout.setSpacing(12)
        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        self.topbar_title = QLabel("Добро пожаловать в MoveUp")
        self.topbar_title.setObjectName("topbarTitle")
        self.topbar_subtitle = QLabel("Реабилитация без скуки")
        self.topbar_subtitle.setObjectName("topbarSubtitle")
        text_column.addWidget(self.topbar_title)
        text_column.addWidget(self.topbar_subtitle)
        layout.addLayout(text_column, 1)
        self.topbar_status = QLabel("●  Проверяем контроллер")
        self.topbar_status.setObjectName("topbarStatus")
        layout.addWidget(self.topbar_status)
        settings_button = QPushButton("⚙")
        settings_button.setObjectName("topIconButton")
        settings_button.setFixedSize(42, 42)
        settings_button.setToolTip("Настройки")
        settings_button.clicked.connect(lambda: self.show_page("settings"))
        layout.addWidget(settings_button)
        return topbar

    def _wrap_in_scroll(self, child: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("pageScroll")
        scroll.setWidget(child)
        return scroll

    def _build_home_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(17)

        intro = QVBoxLayout()
        intro.setSpacing(4)
        eyebrow = QLabel("MOVEUP  /  ИГРОВАЯ РЕАБИЛИТАЦИЯ")
        eyebrow.setObjectName("heroEyebrow")
        heading = QLabel("Двигайся. Играй. Развивайся.")
        heading.setObjectName("homeHeadline")
        subtitle = QLabel("Реабилитация без скуки — тренировки, которые хочется проходить.")
        subtitle.setObjectName("bodyMuted")
        intro.addWidget(eyebrow)
        intro.addWidget(heading)
        intro.addWidget(subtitle)
        layout.addLayout(intro)

        self.featured_frame = SoftPanel(object_name="immersiveFeatured")
        self.featured_layout = QHBoxLayout(self.featured_frame)
        self.featured_layout.setContentsMargins(18, 18, 18, 18)
        self.featured_layout.setSpacing(22)
        self._populate_featured()
        layout.addWidget(self.featured_frame)

        section_row = QHBoxLayout()
        section_text = QVBoxLayout()
        section_text.setSpacing(3)
        section_title = QLabel("Другие игры")
        section_title.setObjectName("sectionTitle")
        section_subtitle = QLabel("Каждый сценарий тренирует отдельные навыки")
        section_subtitle.setObjectName("bodyMuted")
        section_text.addWidget(section_title)
        section_text.addWidget(section_subtitle)
        section_row.addLayout(section_text, 1)
        see_all = QPushButton("Вся библиотека  →")
        see_all.setObjectName("textButton")
        see_all.clicked.connect(lambda: self.show_page("catalog"))
        section_row.addWidget(see_all, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(section_row)

        self.home_tiles_host = QWidget()
        self.home_tiles_layout = QHBoxLayout(self.home_tiles_host)
        self.home_tiles_layout.setContentsMargins(0, 0, 0, 0)
        self.home_tiles_layout.setSpacing(13)
        layout.addWidget(self.home_tiles_host)
        self._rebuild_home_tiles()

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)
        self.game_count_card = self._metric_card("▦", "Игры в каталоге", "0", COLORS["blue"])
        self.launch_count_card = self._metric_card("↗", "Всего тренировок", "0", COLORS["yellow"])
        self.total_time_card = self._metric_card("◷", "Время занятий", "0 мин", COLORS["green"])
        self.controller_metric_card = self._metric_card("⌁", "Контроллер", "Проверяем…", COLORS["blue"])
        for card in (self.game_count_card, self.launch_count_card, self.total_time_card, self.controller_metric_card):
            metrics_row.addWidget(card, 1)
        layout.addLayout(metrics_row)
        layout.addStretch(1)
        self._update_home_metrics()
        return self._wrap_in_scroll(body)

    def _rebuild_home_tiles(self) -> None:
        if not hasattr(self, "home_tiles_layout"):
            return
        while self.home_tiles_layout.count():
            item = self.home_tiles_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        featured = next((game for game in self.config.games if game.id == "maze"), None)
        if featured is None and self.config.games:
            featured = self.config.games[0]
        games = [game for game in self.config.games if not featured or game.id != featured.id][:3]
        for game in games:
            tile = HomeGameTile(game, self.launch_game, self.statistics.game_summary(game.id), self.show_game_stats)
            self.home_tiles_layout.addWidget(tile, 1)
        if not games:
            empty = QLabel("Добавь ещё игры — они появятся здесь.")
            empty.setObjectName("bodyMuted")
            self.home_tiles_layout.addWidget(empty)

    def _metric_card(self, icon_text: str, label_text: str, value_text: str, accent: str) -> QFrame:
        card = SoftPanel(object_name="metricCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 14, 15, 14)
        layout.setSpacing(12)
        icon_box = QLabel(icon_text)
        icon_box.setObjectName("metricIcon")
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setFixedSize(42, 42)
        icon_box.setStyleSheet(f"color: {accent}; background: rgba(169,200,216,0.09); border-radius: 12px;")
        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel(label_text)
        title.setObjectName("bodyMuted")
        value = QLabel(value_text)
        value.setObjectName("metricValue")
        value.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(value)
        layout.addWidget(icon_box)
        layout.addLayout(text, 1)
        card.value_label = value  # type: ignore[attr-defined]
        return card

    def _populate_featured(self) -> None:
        while self.featured_layout.count():
            item = self.featured_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        featured = next((game for game in self.config.games if game.id == "maze"), None)
        if not featured and self.config.games:
            featured = self.config.games[0]
        if not featured:
            empty = QLabel("Добавь первую игру в каталоге, чтобы начать работу.")
            empty.setObjectName("bodyMuted")
            self.featured_layout.addWidget(empty)
            return

        artwork = GameArtwork(featured.art, featured.accent, featured.id)
        artwork.setMinimumWidth(300)
        self.featured_layout.addWidget(artwork, 5)

        details = QVBoxLayout()
        details.setSpacing(10)
        tag = QLabel("РЕКОМЕНДУЕМАЯ ИГРА  ·  " + featured.category.upper())
        tag.setObjectName("microLabel")
        tag.setStyleSheet(f"color: {featured.accent};")
        title = QLabel(featured.title)
        title.setObjectName("featuredTitle")
        title.setWordWrap(True)
        description = QLabel(featured.description)
        description.setObjectName("featuredDescription")
        description.setWordWrap(True)
        game_stats = self.statistics.game_summary(featured.id)
        summary = QLabel(f"{game_stats['launch_count']} запусков  ·  {format_duration(game_stats['total_duration_seconds'])} в игре")
        summary.setObjectName("bodyMuted")
        details.addWidget(tag)
        details.addWidget(title)
        details.addWidget(description)
        details.addWidget(summary)
        details.addStretch(1)
        button_row = QHBoxLayout()
        launch = QPushButton("▶   Начать тренировку")
        launch.setObjectName("featuredLaunchButton")
        launch.clicked.connect(lambda: self.launch_game(featured.id))
        stats_button = QPushButton("↗")
        stats_button.setObjectName("iconButton")
        stats_button.setFixedSize(45, 42)
        stats_button.setToolTip("Подробная статистика игры")
        stats_button.clicked.connect(lambda: self.show_game_stats(featured.id, "home"))
        configure = QPushButton("•••")
        configure.setObjectName("iconButton")
        configure.setFixedSize(45, 42)
        configure.setToolTip("Настройки запуска")
        configure.clicked.connect(lambda: self.configure_game(featured.id))
        button_row.addWidget(launch, 1)
        button_row.addWidget(stats_button)
        button_row.addWidget(configure)
        details.addLayout(button_row)
        self.featured_layout.addLayout(details, 4)

    def _build_catalog_page(self) -> QWidget:
        body = QWidget()
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 23, 28, 28)
        layout.setSpacing(16)

        header = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(5)
        title = QLabel("Библиотека игр")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Игры находятся автоматически в ../games и в выбранной папке.")
        subtitle.setObjectName("bodyMuted")
        subtitle.setWordWrap(True)
        labels.addWidget(title)
        labels.addWidget(subtitle)
        header.addLayout(labels, 1)
        self.refresh_games_button = QPushButton("⟳  Обновить")
        self.refresh_games_button.setObjectName("secondaryButton")
        self.refresh_games_button.clicked.connect(self.refresh_game_library)
        add_button = QPushButton("＋  Добавить вручную")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self.add_game)
        header.addWidget(self.refresh_games_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(add_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        filters_row = QHBoxLayout()
        filters_row.setSpacing(10)
        self.catalog_search_edit = QLineEdit()
        self.catalog_search_edit.setObjectName("catalogSearch")
        self.catalog_search_edit.setPlaceholderText("Поиск по названию, описанию или категории…")
        self.catalog_search_edit.setClearButtonEnabled(True)
        self.catalog_search_edit.textChanged.connect(self._rebuild_catalog)
        self.catalog_category_combo = QComboBox()
        self.catalog_category_combo.setMinimumWidth(170)
        self.catalog_category_combo.addItem("Все категории")
        self.catalog_category_combo.currentTextChanged.connect(self._rebuild_catalog)
        filters_row.addWidget(self.catalog_search_edit, 1)
        filters_row.addWidget(self.catalog_category_combo)
        layout.addLayout(filters_row)

        self.catalog_count_label = QLabel("")
        self.catalog_count_label.setObjectName("bodyMuted")
        layout.addWidget(self.catalog_count_label)

        self.catalog_grid_host = QWidget()
        self.catalog_grid_host.setMinimumWidth(0)
        # Both the scroll content and the grid host must expand horizontally;
        # otherwise Qt can size the grid to a single card's sizeHint.
        self.catalog_grid_host.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.catalog_grid = QGridLayout(self.catalog_grid_host)
        self.catalog_grid.setContentsMargins(0, 0, 0, 0)
        self.catalog_grid.setHorizontalSpacing(15)
        self.catalog_grid.setVerticalSpacing(15)
        layout.addWidget(self.catalog_grid_host)
        layout.addStretch(1)
        # Keep the scroll area so column calculation can use the actual viewport,
        # rather than a possibly stale/narrow grid-host width.
        self.catalog_scroll = self._wrap_in_scroll(body)
        self._rebuild_catalog()
        # The first build happens before the page is attached to the main window.
        # Repeat after Qt assigns the real window and viewport geometries.
        QTimer.singleShot(0, self._rebuild_catalog)
        QTimer.singleShot(150, self._rebuild_catalog)
        return self.catalog_scroll

    def _rebuild_catalog(self, *_args) -> None:
        if not hasattr(self, "catalog_grid"):
            return
        while self.catalog_grid.count():
            item = self.catalog_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if hasattr(self, "catalog_category_combo"):
            selected_category = self.catalog_category_combo.currentText() or "Все категории"
            categories = sorted({game.category for game in self.config.games if game.category})
            self.catalog_category_combo.blockSignals(True)
            self.catalog_category_combo.clear()
            self.catalog_category_combo.addItem("Все категории")
            self.catalog_category_combo.addItems(categories)
            category_index = self.catalog_category_combo.findText(selected_category)
            self.catalog_category_combo.setCurrentIndex(category_index if category_index >= 0 else 0)
            self.catalog_category_combo.blockSignals(False)
            selected_category = self.catalog_category_combo.currentText()
        else:
            selected_category = "Все категории"

        query = self.catalog_search_edit.text().strip().casefold() if hasattr(self, "catalog_search_edit") else ""
        visible_games = [
            game for game in self.config.games
            if (selected_category == "Все категории" or game.category == selected_category)
            and (not query or query in " ".join((game.title, game.description, game.category, game.id)).casefold())
        ]
        if hasattr(self, "catalog_count_label"):
            self.catalog_count_label.setText(f"Показано {len(visible_games)} из {len(self.config.games)} игр")

        # Calculate columns from the actual scroll viewport, not grid_host.width().
        # The host can temporarily retain a one-card sizeHint after startup/maximizing.
        # Catalog page margins are 28 px on both sides.
        viewport_width = self.catalog_scroll.viewport().width() if hasattr(self, "catalog_scroll") else 0
        if viewport_width < 100:
            sidebar_width = self.sidebar.width() if hasattr(self, "sidebar") else 190
            viewport_width = max(1, self.width() - sidebar_width - 20)
        grid_width = max(1, viewport_width - 56)
        # Keep the grid host as wide as the content area. This is important because
        # QScrollArea can otherwise leave the child close to its sizeHint, producing
        # a narrow centered column even on a wide/full-screen window.
        self.catalog_grid_host.setMinimumWidth(grid_width)
        card_target_width = 320
        horizontal_spacing = self.catalog_grid.horizontalSpacing()
        column_count = max(
            1,
            (grid_width + horizontal_spacing) // (card_target_width + horizontal_spacing),
        )
        self.catalog_columns = int(column_count)

        for index, game in enumerate(visible_games):
            card = GameCard(
                game,
                self.launch_game,
                self.configure_game,
                self.resolve_game_path,
                self._show_game_menu,
                self.statistics.game_summary(game.id),
                self.show_game_stats,
            )
            row_index, column_index = divmod(index, self.catalog_columns)
            self.catalog_grid.addWidget(
                card, row_index, column_index,
                alignment=Qt.AlignmentFlag.AlignHCenter,
            )

        # Clear old stretch settings when the grid gains or loses columns.
        for column_index in range(max(4, self.catalog_columns + 1)):
            self.catalog_grid.setColumnStretch(column_index, 1 if column_index < self.catalog_columns else 0)

        if not visible_games:
            empty = QLabel(
                "Ничего не найдено. Измени поисковый запрос или обнови библиотеку."
                if self.config.games else
                "Игры не найдены. Проверь папку ../games или выбери «Добавить вручную»."
            )
            empty.setObjectName("bodyMuted")
            empty.setWordWrap(True)
            self.catalog_grid.addWidget(empty, 0, 0, 1, self.catalog_columns)

    def _build_results_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(17)
        title = QLabel("Твой прогресс")
        title.setObjectName("pageTitle")
        subtitle = QLabel("История занятий, динамика активности и индивидуальные показатели игр.")
        subtitle.setObjectName("bodyMuted")
        subtitle.setWordWrap(True)
        self.stats_source_label = QLabel("")
        self.stats_source_label.setObjectName("statsSourceLabel")
        self.stats_source_label.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.stats_source_label)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self.result_sessions_card = self._metric_card("↗", "Тренировок", "0", COLORS["blue"])
        self.result_time_card = self._metric_card("◷", "Общее время", "0 мин", COLORS["yellow"])
        self.result_games_card = self._metric_card("▦", "Игр с запусками", "0", COLORS["green"])
        summary_row.addWidget(self.result_sessions_card, 1)
        summary_row.addWidget(self.result_time_card, 1)
        summary_row.addWidget(self.result_games_card, 1)
        layout.addLayout(summary_row)

        chart_panel = SoftPanel(object_name="chartPanel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(18, 16, 18, 16)
        chart_layout.setSpacing(10)
        chart_title = QLabel("Активность за последние 7 дней")
        chart_title.setObjectName("sectionTitle")
        chart_subtitle = QLabel("Минуты тренировок по дням")
        chart_subtitle.setObjectName("bodyMuted")
        self.activity_chart = ActivityChart()
        chart_layout.addWidget(chart_title)
        chart_layout.addWidget(chart_subtitle)
        chart_layout.addWidget(self.activity_chart)
        layout.addWidget(chart_panel)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)
        game_panel = SoftPanel(object_name="dashboardPanel")
        game_layout = QVBoxLayout(game_panel)
        game_layout.setContentsMargins(17, 16, 17, 17)
        game_layout.setSpacing(10)
        games_title = QLabel("Статистика по играм")
        games_title.setObjectName("sectionTitle")
        self.game_stats_container = QWidget()
        self.game_stats_layout = QVBoxLayout(self.game_stats_container)
        self.game_stats_layout.setContentsMargins(0, 0, 0, 0)
        self.game_stats_layout.setSpacing(8)
        game_layout.addWidget(games_title)
        game_layout.addWidget(self.game_stats_container)
        game_layout.addStretch(1)

        history_panel = SoftPanel(object_name="dashboardPanel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(17, 16, 17, 17)
        history_layout.setSpacing(10)
        history_title = QLabel("Последние занятия")
        history_title.setObjectName("sectionTitle")
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        history_layout.addWidget(history_title)
        history_layout.addWidget(self.history_container)
        history_layout.addStretch(1)
        bottom_row.addWidget(game_panel, 1)
        bottom_row.addWidget(history_panel, 2)
        layout.addLayout(bottom_row)
        layout.addStretch(1)
        self._refresh_results_page()
        return self._wrap_in_scroll(body)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _refresh_results_page(self) -> None:
        if not hasattr(self, "result_sessions_card"):
            return
        summary_count = self.statistics.session_count()
        total_seconds = self.statistics.total_duration()
        games_with_sessions = len({
            item.get("game_id") for item in self.statistics.data.get("sessions", [])
            if isinstance(item, dict) and item.get("status") != "failed_to_start" and item.get("game_id")
        })
        self.result_sessions_card.value_label.setText(str(summary_count))
        self.result_time_card.value_label.setText(format_duration(total_seconds))
        self.result_games_card.value_label.setText(str(games_with_sessions))
        self.activity_chart.set_data(self.statistics.activity_by_day(7))

        source_hint = (
            f"Источник: {self.statistics.external_path}"
            if self.statistics.external_available
            else f"Ожидается файл: {self.statistics.external_path}"
        )
        if self.statistics.external_last_error:
            source_hint += f" · {self.statistics.external_last_error}"
        elif self.statistics.external_available:
            source_hint += f" · импортировано сессий в исходном файле: {self.statistics.external_session_count}"
        self.stats_source_label.setText(source_hint)

        self._clear_layout(self.game_stats_layout)
        game_map = {game.id: game for game in self.config.games}
        discovered_ids = {
            str(item.get("game_id")) for item in self.statistics.data.get("sessions", [])
            if isinstance(item, dict) and item.get("game_id")
        }
        for game_id in discovered_ids:
            if game_id not in game_map:
                latest_session = next((
                    item for item in reversed(self.statistics.data.get("sessions", []))
                    if isinstance(item, dict) and str(item.get("game_id") or "") == game_id
                ), {})
                title_from_data = str(latest_session.get("game_title") or game_id.replace("_", " ").title())
                game_map[game_id] = GameEntry(
                    id=game_id,
                    title=title_from_data,
                    description="Игра, обнаруженная в общей статистике.",
                    category="Импортированная игра",
                    accent=COLORS["blue"],
                    art="dots",
                )
        game_rows = []
        for game in game_map.values():
            summary = self.statistics.game_summary(game.id)
            if summary["launch_count"]:
                game_rows.append((game, summary))
        game_rows.sort(key=lambda pair: (pair[1]["launch_count"], pair[1]["total_duration_seconds"]), reverse=True)
        if not game_rows:
            empty = QLabel("Данные появятся после первых запусков игр.")
            empty.setObjectName("bodyMuted")
            empty.setWordWrap(True)
            self.game_stats_layout.addWidget(empty)
        for game, game_summary in game_rows:
            row = SoftPanel(object_name="historyRow")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(11, 9, 11, 9)
            row_layout.setSpacing(4)
            name = QLabel(game.title)
            name.setObjectName("historyTitle")
            details = QLabel(
                f"{game_summary['launch_count']} тренировок  ·  "
                f"{format_duration(game_summary['total_duration_seconds'])}"
            )
            details.setObjectName("bodyMuted")
            row_layout.addWidget(name)
            row_layout.addWidget(details)
            metric_lines = metric_items(game_summary.get("latest_metrics"), 2)
            if metric_lines:
                metric_label = QLabel("   ·   ".join(f"{key}: {value}" for key, value in metric_lines))
                metric_label.setObjectName("metricDetail")
                metric_label.setWordWrap(True)
                row_layout.addWidget(metric_label)
            detail_button = QPushButton("Подробная статистика  →")
            detail_button.setObjectName("textButton")
            detail_button.setCursor(Qt.CursorShape.PointingHandCursor)
            detail_button.clicked.connect(
                lambda checked=False, selected_game_id=game.id: self.show_game_stats(selected_game_id, "results")
            )
            row_layout.addWidget(detail_button, 0, Qt.AlignmentFlag.AlignLeft)
            self.game_stats_layout.addWidget(row)
        self.game_stats_layout.addStretch(1)

        self._clear_layout(self.history_layout)
        recent_sessions = self.statistics.recent_sessions(6)
        if not recent_sessions:
            empty = QLabel("Пока нет записанных тренировок. Запусти игру, чтобы начать собирать статистику.")
            empty.setObjectName("bodyMuted")
            empty.setWordWrap(True)
            self.history_layout.addWidget(empty)
        status_labels = {
            "running": "В процессе",
            "completed": "Завершена",
            "failed": "Игра завершилась с ошибкой",
            "failed_to_start": "Не удалось запустить",
            "interrupted": "Прервана",
            "launcher_closed": "Лаунчер закрыт во время игры",
        }
        for session in recent_sessions:
            row = SoftPanel(object_name="historyRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(11, 9, 11, 9)
            row_layout.setSpacing(12)
            details_layout = QVBoxLayout()
            details_layout.setSpacing(3)
            game_title = QLabel(str(session.get("game_title") or session.get("game_id") or "Игра"))
            game_title.setObjectName("historyTitle")
            start_dt = parse_timestamp(session.get("started_at"))
            date_text = start_dt.astimezone().strftime("%d.%m.%Y · %H:%M") if start_dt else "Дата неизвестна"
            meta = QLabel(f"{date_text}  ·  {format_duration(session.get('duration_seconds', 0))}")
            meta.setObjectName("bodyMuted")
            details_layout.addWidget(game_title)
            details_layout.addWidget(meta)
            metric_lines = metric_items(session.get("metrics"), 2)
            if metric_lines:
                metric_label = QLabel("   ·   ".join(f"{key}: {value}" for key, value in metric_lines))
                metric_label.setObjectName("metricDetail")
                metric_label.setWordWrap(True)
                details_layout.addWidget(metric_label)
            row_layout.addLayout(details_layout, 1)
            status_label = QLabel(status_labels.get(str(session.get("status")), str(session.get("status", ""))))
            status_label.setObjectName("sessionStatus")
            status = str(session.get("status"))
            color = COLORS["green"] if status == "completed" else (COLORS["yellow"] if status in {"running", "interrupted", "launcher_closed"} else COLORS["muted"])
            status_label.setStyleSheet(f"color: {color};")
            status_label.setWordWrap(True)
            status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(status_label)
            self.history_layout.addWidget(row)
        self.history_layout.addStretch(1)

    def _build_game_stats_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(17)

        back_button = QPushButton("←  Назад")
        back_button.setObjectName("textButton")
        back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        back_button.clicked.connect(lambda: self.show_page(self.stats_return_page))
        layout.addWidget(back_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.game_detail_title = QLabel("Статистика игры")
        self.game_detail_title.setObjectName("pageTitle")
        self.game_detail_title.setWordWrap(True)
        self.game_detail_subtitle = QLabel("Подробные результаты и история тренировок.")
        self.game_detail_subtitle.setObjectName("bodyMuted")
        self.game_detail_subtitle.setWordWrap(True)
        layout.addWidget(self.game_detail_title)
        layout.addWidget(self.game_detail_subtitle)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(11)
        self.detail_launch_card = self._metric_card("↗", "Сессий", "0", COLORS["blue"])
        self.detail_time_card = self._metric_card("◷", "Общее время", "0 сек", COLORS["yellow"])
        self.detail_completed_card = self._metric_card("✓", "Завершено", "0", COLORS["green"])
        self.detail_interrupted_card = self._metric_card("↻", "Прервано / ошибок", "0", COLORS["red"])
        for card in (self.detail_launch_card, self.detail_time_card, self.detail_completed_card, self.detail_interrupted_card):
            summary_row.addWidget(card, 1)
        layout.addLayout(summary_row)

        metrics_panel = SoftPanel(object_name="dashboardPanel")
        metrics_layout = QVBoxLayout(metrics_panel)
        metrics_layout.setContentsMargins(17, 16, 17, 17)
        metrics_layout.setSpacing(10)
        metrics_title = QLabel("Показатели последней сессии с данными")
        metrics_title.setObjectName("sectionTitle")
        metrics_hint = QLabel("Отображаются метрики, которые сама игра записала в общий файл статистики.")
        metrics_hint.setObjectName("bodyMuted")
        metrics_hint.setWordWrap(True)
        self.game_metric_grid_host = QWidget()
        self.game_metric_grid = QGridLayout(self.game_metric_grid_host)
        self.game_metric_grid.setContentsMargins(0, 0, 0, 0)
        self.game_metric_grid.setHorizontalSpacing(10)
        self.game_metric_grid.setVerticalSpacing(8)
        metrics_layout.addWidget(metrics_title)
        metrics_layout.addWidget(metrics_hint)
        metrics_layout.addWidget(self.game_metric_grid_host)
        layout.addWidget(metrics_panel)

        history_panel = SoftPanel(object_name="dashboardPanel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(17, 16, 17, 17)
        history_layout.setSpacing(10)
        history_title = QLabel("История занятий")
        history_title.setObjectName("sectionTitle")
        history_hint = QLabel("Последние 12 сессий этой игры")
        history_hint.setObjectName("bodyMuted")
        self.game_history_container = QWidget()
        self.game_history_layout = QVBoxLayout(self.game_history_container)
        self.game_history_layout.setContentsMargins(0, 0, 0, 0)
        self.game_history_layout.setSpacing(8)
        history_layout.addWidget(history_title)
        history_layout.addWidget(history_hint)
        history_layout.addWidget(self.game_history_container)
        layout.addWidget(history_panel)
        layout.addStretch(1)
        return self._wrap_in_scroll(body)

    def show_game_stats(self, game_id: str, return_page: str | None = None) -> None:
        if return_page in {"home", "catalog", "results", "settings"}:
            self.stats_return_page = return_page
        elif hasattr(self, "pages"):
            current_pages = {0: "home", 1: "catalog", 2: "results", 3: "settings", 4: "game_stats"}
            current_key = current_pages.get(self.pages.currentIndex(), "catalog")
            self.stats_return_page = current_key if current_key != "game_stats" else self.stats_return_page
        self.selected_stats_game_id = str(game_id)
        try:
            self.statistics.sync_external_statistics(force=True)
        except (OSError, RuntimeError):
            pass
        self._refresh_game_stats_page()
        self.show_page("game_stats")

    def _refresh_game_stats_page(self) -> None:
        if not hasattr(self, "game_metric_grid") or not self.selected_stats_game_id:
            return
        game_id = self.selected_stats_game_id
        game = next((item for item in self.config.games if item.id == game_id), None)
        sessions = [
            dict(item) for item in self.statistics.data.get("sessions", [])
            if isinstance(item, dict) and str(item.get("game_id") or "") == game_id
        ]
        sessions.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        if game is None:
            latest_session = sessions[0] if sessions else {}
            game = GameEntry(
                id=game_id,
                title=str(latest_session.get("game_title") or game_id.replace("_", " ").title()),
                description="Игра присутствует в истории статистики, но её исполняемый файл сейчас не найден в каталоге.",
                category="История статистики",
                accent=COLORS["blue"],
                art="dots",
            )

        self.game_detail_title.setText(game.title)
        self.game_detail_subtitle.setText(f"{game.category}  ·  ID: {game.id}  ·  {game.description}")
        summary = self.statistics.game_summary(game_id)
        completed_count = sum(1 for item in sessions if item.get("status") == "completed")
        interrupted_count = sum(
            1 for item in sessions
            if item.get("status") in {"interrupted", "launcher_closed", "failed", "failed_to_start"}
        )
        self.detail_launch_card.value_label.setText(str(summary["launch_count"]))
        self.detail_time_card.value_label.setText(format_duration(summary["total_duration_seconds"]))
        self.detail_completed_card.value_label.setText(str(completed_count))
        self.detail_interrupted_card.value_label.setText(str(interrupted_count))

        self._clear_layout(self.game_metric_grid)
        latest_metrics = summary.get("latest_metrics", {})
        metrics = metric_items(latest_metrics, limit=100)
        if not metrics:
            empty = QLabel("Для этой игры пока нет записанных метрик. История запусков при этом может быть доступна ниже.")
            empty.setObjectName("bodyMuted")
            empty.setWordWrap(True)
            self.game_metric_grid.addWidget(empty, 0, 0, 1, 2)
        else:
            for index, (metric_name, metric_value) in enumerate(metrics):
                metric_card = SoftPanel(object_name="metricDetailCard")
                metric_card_layout = QVBoxLayout(metric_card)
                metric_card_layout.setContentsMargins(12, 10, 12, 10)
                metric_card_layout.setSpacing(4)
                name_label = QLabel(metric_name)
                name_label.setObjectName("bodyMuted")
                name_label.setWordWrap(True)
                value_label = QLabel(metric_value)
                value_label.setObjectName("detailMetricValue")
                value_label.setWordWrap(True)
                metric_card_layout.addWidget(name_label)
                metric_card_layout.addWidget(value_label)
                self.game_metric_grid.addWidget(metric_card, index // 2, index % 2)
        self.game_metric_grid.setColumnStretch(0, 1)
        self.game_metric_grid.setColumnStretch(1, 1)

        self._clear_layout(self.game_history_layout)
        if not sessions:
            empty = QLabel("По этой игре пока нет сессий. Запусти её из каталога, чтобы начать вести историю.")
            empty.setObjectName("bodyMuted")
            empty.setWordWrap(True)
            self.game_history_layout.addWidget(empty)
        status_labels = {
            "running": "В процессе",
            "completed": "Завершена",
            "failed": "Завершилась с ошибкой",
            "failed_to_start": "Не удалось запустить",
            "interrupted": "Прервана",
            "launcher_closed": "Лаунчер закрыт во время игры",
        }
        for session in sessions[:12]:
            row = SoftPanel(object_name="historyRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(12)
            info = QVBoxLayout()
            info.setSpacing(4)
            started = parse_timestamp(session.get("started_at"))
            date_text = started.astimezone().strftime("%d.%m.%Y · %H:%M") if started else "Дата неизвестна"
            date_label = QLabel(date_text)
            date_label.setObjectName("historyTitle")
            duration_label = QLabel(f"Длительность: {format_duration(session.get('duration_seconds', 0))}")
            duration_label.setObjectName("bodyMuted")
            info.addWidget(date_label)
            info.addWidget(duration_label)
            preview_metrics = metric_items(session.get("metrics"), limit=2)
            if preview_metrics:
                preview_label = QLabel("   ·   ".join(f"{name}: {value}" for name, value in preview_metrics))
                preview_label.setObjectName("metricDetail")
                preview_label.setWordWrap(True)
                info.addWidget(preview_label)
            row_layout.addLayout(info, 1)
            status = str(session.get("status") or "unknown")
            status_widget = QLabel(status_labels.get(status, status.replace("_", " ").capitalize()))
            status_widget.setObjectName("sessionStatus")
            status_color = COLORS["green"] if status == "completed" else (
                COLORS["yellow"] if status in {"running", "interrupted", "launcher_closed"} else COLORS["muted"]
            )
            status_widget.setStyleSheet(f"color: {status_color};")
            status_widget.setWordWrap(True)
            status_widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(status_widget)
            self.game_history_layout.addWidget(row)
        self.game_history_layout.addStretch(1)

    def _build_settings_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(18)
        title = QLabel("Настройки")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Настрой среду запуска и подключаемое устройство.")
        subtitle.setObjectName("bodyMuted")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        source_panel = SoftPanel(object_name="settingsPanel")
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(20, 19, 20, 20)
        source_layout.setSpacing(11)
        source_heading = QLabel("Каталог игр по умолчанию")
        source_heading.setObjectName("sectionTitle")
        source_hint = QLabel("Эта папка используется по умолчанию. Лаунчер дополнительно сканирует ../games и ищет игры в подпапках, пропуская assets и __pycache__.")
        source_hint.setObjectName("bodyMuted")
        source_hint.setWordWrap(True)
        source_row = QHBoxLayout()
        self.source_path_edit = QLineEdit(self.config.default_source)
        self.source_path_edit.setPlaceholderText("Папка с файлами игр")
        browse_source = QPushButton("Обзор…")
        browse_source.setObjectName("secondaryButton")
        browse_source.clicked.connect(self._browse_default_source)
        save_source = QPushButton("Сохранить каталог")
        save_source.setObjectName("primaryButton")
        save_source.clicked.connect(self._save_default_source)
        source_row.addWidget(self.source_path_edit, 1)
        source_row.addWidget(browse_source)
        source_row.addWidget(save_source)
        source_layout.addWidget(source_heading)
        source_layout.addWidget(source_hint)
        source_layout.addLayout(source_row)
        layout.addWidget(source_panel)

        python_panel = SoftPanel(object_name="settingsPanel")
        python_layout = QVBoxLayout(python_panel)
        python_layout.setContentsMargins(20, 19, 20, 20)
        python_layout.setSpacing(11)
        heading = QLabel("Среда Python")
        heading.setObjectName("sectionTitle")
        hint = QLabel("Используется для запуска файлов .py. Для .exe отдельный интерпретатор не нужен.")
        hint.setObjectName("bodyMuted")
        hint.setWordWrap(True)
        path_row = QHBoxLayout()
        self.python_path_edit = QLineEdit(self.config.python_executable)
        self.python_path_edit.setPlaceholderText("python или полный путь к python.exe")
        browse_python = QPushButton("Обзор…")
        browse_python.setObjectName("secondaryButton")
        browse_python.clicked.connect(self._browse_python)
        path_row.addWidget(self.python_path_edit, 1)
        path_row.addWidget(browse_python)
        save_python = QPushButton("Сохранить интерпретатор")
        save_python.setObjectName("primaryButton")
        save_python.clicked.connect(self._save_python_settings)
        python_layout.addWidget(heading)
        python_layout.addWidget(hint)
        python_layout.addLayout(path_row)
        python_layout.addWidget(save_python, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(python_panel)

        controller_panel = SoftPanel(object_name="settingsPanel")
        controller_layout = QVBoxLayout(controller_panel)
        controller_layout.setContentsMargins(20, 19, 20, 20)
        controller_layout.setSpacing(11)
        controller_heading = QLabel("Контроллер MoveUp")
        controller_heading.setObjectName("sectionTitle")
        controller_hint = QLabel("Лаунчер обнаруживает последовательные порты. Сам факт наличия порта ещё не подтверждает обмен данными с прошивкой.")
        controller_hint.setObjectName("bodyMuted")
        controller_hint.setWordWrap(True)
        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(230)
        self.refresh_ports_button = QPushButton("Обновить список")
        self.refresh_ports_button.setObjectName("secondaryButton")
        self.refresh_ports_button.clicked.connect(self.refresh_controller_status)
        self.save_port_button = QPushButton("Сохранить порт")
        self.save_port_button.setObjectName("primaryButton")
        self.save_port_button.clicked.connect(self._save_preferred_port)
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_ports_button)
        port_row.addWidget(self.save_port_button)
        self.controller_settings_state = QLabel("Поиск последовательных портов…")
        self.controller_settings_state.setObjectName("bodyMuted")
        controller_layout.addWidget(controller_heading)
        controller_layout.addWidget(controller_hint)
        controller_layout.addLayout(port_row)
        controller_layout.addWidget(self.controller_settings_state)
        layout.addWidget(controller_panel)

        config_panel = SoftPanel(object_name="settingsPanel")
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(20, 19, 20, 20)
        config_layout.setSpacing(9)
        config_heading = QLabel("Файлы конфигурации")
        config_heading.setObjectName("sectionTitle")
        self.config_location_label = QLabel(str(self.config.path))
        self.config_location_label.setObjectName("bodyMuted")
        self.config_location_label.setWordWrap(True)
        config_button_row = QHBoxLayout()
        open_config_folder = QPushButton("Открыть папку настроек")
        open_config_folder.setObjectName("secondaryButton")
        open_config_folder.clicked.connect(self._open_config_folder)
        config_button_row.addWidget(open_config_folder)
        config_button_row.addStretch(1)
        config_layout.addWidget(config_heading)
        config_layout.addWidget(self.config_location_label)
        config_layout.addLayout(config_button_row)
        layout.addWidget(config_panel)
        layout.addStretch(1)
        return self._wrap_in_scroll(body)

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("bottomStatusbar")
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(9)
        self.bottom_controller_indicator = QLabel("●")
        self.bottom_controller_indicator.setObjectName("statusDot")
        self.bottom_controller_label = QLabel("Контроллер: проверка…")
        self.bottom_controller_label.setObjectName("bottomStatusText")
        layout.addWidget(self.bottom_controller_indicator)
        layout.addWidget(self.bottom_controller_label)
        layout.addStretch(1)
        self.bottom_version_label = QLabel(f"MOVEUP  /  {APP_VERSION}")
        self.bottom_version_label.setObjectName("bottomStatusText")
        layout.addWidget(self.bottom_version_label)
        return bar

    def apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                color: {COLORS['milk']};
                font-family: 'Inter', 'Segoe UI', 'Arial', sans-serif;
                font-size: 13px;
            }}
            QMainWindow, QWidget#appRoot {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #17232D, stop:0.58 #1B2B36, stop:1 #203542);
            }}
            QStackedWidget#pagesStack {{ background: {COLORS['bg']}; border: none; }}
            QScrollArea#pageScroll {{ background: {COLORS['bg']}; border: none; }}
            QWidget#sidebar {{ background: {COLORS['sidebar']}; border-right: 1px solid {COLORS['border']}; }}
            QWidget#contentWrap {{ background: {COLORS['bg']}; }}
            QWidget#topbar {{ background: {COLORS['bg']}; border-bottom: 1px solid rgba(169,200,216,0.12); }}
            QWidget#bottomStatusbar {{ background: {COLORS['bg']}; border-top: 1px solid rgba(169,200,216,0.12); }}
            QFrame#sidebarDivider {{ background: {COLORS['border']}; }}
            QLabel#brandLogo {{ background: {COLORS['blue']}; color: {COLORS['bg']}; border-radius: 13px; font-size: 25px; font-weight: 900; }}
            QLabel#brandName {{ color: {COLORS['milk']}; font-size: 18px; font-weight: 900; letter-spacing: 2px; }}
            QLabel#brandDescriptor {{ color: {COLORS['blue']}; font-size: 8px; font-weight: 700; letter-spacing: 1.4px; }}
            QLabel#versionLabel {{ color: #748895; font-size: 10px; padding-top: 10px; }}
            QPushButton#navButton, QPushButton#bottomNavButton {{
                background: transparent; color: {COLORS['muted']}; border: none; border-radius: 10px;
                padding: 12px 12px; text-align: left; font-size: 13px; font-weight: 600;
            }}
            QPushButton#navButton:hover, QPushButton#bottomNavButton:hover {{ background: rgba(169,200,216,0.08); color: {COLORS['milk']}; }}
            QPushButton#navButton:checked {{ background: rgba(169,200,216,0.15); color: {COLORS['blue_bright']}; border-left: 3px solid {COLORS['blue']}; padding-left: 9px; }}
            QFrame#sideStatusCard {{ background: #243541; border: 1px solid {COLORS['border']}; border-radius: 14px; }}
            QLabel#controllerGlyph {{ color: {COLORS['blue']}; font-size: 24px; }}
            QLabel#sideStatusTitle {{ color: {COLORS['milk']}; font-weight: 700; }}
            QLabel#sideStatusText {{ color: {COLORS['muted']}; font-size: 11px; }}
            QPushButton#textButton {{ background: transparent; color: {COLORS['blue']}; border: none; padding: 4px 2px; font-weight: 700; }}
            QPushButton#textButton:hover {{ color: {COLORS['blue_bright']}; }}
            QLabel#topbarTitle {{ font-size: 18px; font-weight: 800; color: {COLORS['milk']}; }}
            QLabel#topbarSubtitle {{ color: {COLORS['muted']}; font-size: 11px; }}
            QLabel#topbarStatus {{ color: {COLORS['muted']}; font-size: 11px; padding-right: 9px; }}
            QPushButton#topIconButton {{ background: #243541; border: 1px solid {COLORS['border']}; border-radius: 12px; color: {COLORS['blue']}; font-size: 18px; }}
            QPushButton#topIconButton:hover {{ background: {COLORS['panel_alt']}; }}
            QScrollArea#pageScroll {{ background: {COLORS['bg']}; border: none; }}
            QLabel#heroEyebrow {{ color: {COLORS['yellow']}; font-size: 9px; font-weight: 800; letter-spacing: 1.5px; background: transparent; }}
            QLabel#heroTitle {{ color: {COLORS['milk']}; font-size: 29px; font-weight: 900; background: transparent; }}
            QLabel#heroSubtitle {{ color: #D0DCE2; font-size: 12px; background: transparent; }}
            QLabel#heroSmall {{ color: #91AAB9; font-size: 8px; font-weight: 700; letter-spacing: 1px; background: transparent; }}
            QLabel#sectionTitle {{ color: {COLORS['milk']}; font-size: 17px; font-weight: 800; }}
            QLabel#bodyMuted {{ color: {COLORS['muted']}; font-size: 11px; }}
            QFrame#featuredGamePanel {{ background: #223440; border: 1px solid {COLORS['border']}; border-radius: 18px; }}
            QLabel#microLabel {{ color: {COLORS['muted']}; font-size: 8px; font-weight: 800; letter-spacing: 1px; }}
            QLabel#featuredTitle {{ color: {COLORS['milk']}; font-size: 22px; font-weight: 800; }}
            QFrame#metricCard {{ background: #223440; border: 1px solid rgba(169,200,216,0.12); border-radius: 15px; }}
            QFrame#nextStepsPanel {{ background: #1B2B36; border: 1px solid rgba(169,200,216,0.16); border-radius: 16px; }}
            QLabel#stepNumber {{ color: {COLORS['yellow']}; font-size: 10px; font-weight: 900; letter-spacing: 1px; }}
            QLabel#stepTitle {{ color: {COLORS['milk']}; font-size: 13px; font-weight: 800; }}
            QLabel#metricIcon {{ font-size: 23px; font-weight: 700; }}
            QLabel#metricValue {{ color: {COLORS['milk']}; font-size: 14px; font-weight: 800; }}
            QFrame#gameCard {{ background: #223440; border: 1px solid rgba(169,200,216,0.14); border-radius: 17px; }}
            QFrame#gameCard:hover {{ background: #253B49; border: 1px solid rgba(169,200,216,0.52); }}
            QLabel#cardTitle {{ color: {COLORS['milk']}; font-size: 17px; font-weight: 800; }}
            QLabel#pageTitle {{ color: {COLORS['milk']}; font-size: 25px; font-weight: 900; }}
            QFrame#emptyStatePanel {{ background: #223440; border: 1px solid {COLORS['border']}; border-radius: 18px; }}
            QLabel#emptyStateIcon {{ color: {COLORS['blue']}; font-size: 48px; padding-bottom: 6px; }}
            QFrame#settingsPanel {{ background: #223440; border: 1px solid rgba(169,200,216,0.14); border-radius: 16px; }}
            QPushButton#primaryButton {{ background: {COLORS['blue']}; color: {COLORS['bg']}; border: none; border-radius: 10px; padding: 10px 16px; font-weight: 800; }}
            QPushButton#primaryButton:hover {{ background: {COLORS['blue_bright']}; }}
            QPushButton#primaryButton:pressed {{ background: #94B5C6; }}
            QPushButton#secondaryButton {{ background: #2D4352; color: {COLORS['milk']}; border: 1px solid {COLORS['border']}; border-radius: 10px; padding: 9px 13px; font-weight: 600; }}
            QPushButton#secondaryButton:hover {{ background: #375163; border: 1px solid #527386; }}
            QPushButton#iconButton {{ background: #2D4352; color: {COLORS['blue']}; border: 1px solid {COLORS['border']}; border-radius: 10px; font-weight: 900; }}
            QPushButton#iconButton:hover {{ background: #375163; }}
            QLabel#statusDot {{ color: {COLORS['green']}; font-size: 12px; }}
            QLabel#bottomStatusText {{ color: #899DA9; font-size: 10px; }}
            QLineEdit, QComboBox {{ background: #192833; color: {COLORS['milk']}; border: 1px solid {COLORS['border']}; border-radius: 9px; padding: 9px 11px; selection-background-color: #527386; min-height: 18px; }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {COLORS['blue']}; }}
            QLineEdit:disabled, QComboBox:disabled {{ color: #718591; background: #1A2933; }}
            QComboBox::drop-down {{ border: none; width: 23px; }}
            QComboBox QAbstractItemView {{ background: #223440; color: {COLORS['milk']}; selection-background-color: #395567; border: 1px solid {COLORS['border']}; }}
            QDialog {{ background: {COLORS['bg']}; }}
            QMenu {{ background: #223440; color: {COLORS['milk']}; border: 1px solid {COLORS['border']}; padding: 6px; border-radius: 9px; }}
            QMenu::item {{ padding: 8px 22px 8px 12px; border-radius: 5px; }}
            QMenu::item:selected {{ background: #395567; color: {COLORS['milk']}; }}
            QMenu::separator {{ height: 1px; background: {COLORS['border']}; margin: 5px 4px; }}
            QDialogButtonBox QPushButton {{ min-width: 82px; }}
            QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #3D5666; min-height: 28px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QLabel#homeHeadline {{ color: {COLORS['milk']}; font-size: 25px; font-weight: 900; letter-spacing: 0.2px; }}
            QFrame#immersiveFeatured {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #263F4E,stop:0.55 #203541,stop:1 #182732); border: 1px solid #3D5969; border-radius: 20px; }}
            QFrame#homeGameTile {{ background: #202F3A; border: 1px solid rgba(169,200,216,0.18); border-radius: 16px; }}
            QFrame#homeGameTile:hover {{ background: #273D4B; border: 1px solid rgba(169,200,216,0.5); }}
            QLabel#tileTitle {{ color: {COLORS['milk']}; font-size: 15px; font-weight: 800; }}
            QLabel#featuredDescription {{ color: #D0DCE2; font-size: 12px; line-height: 1.4; }}
            QLabel#gameStatsSmall {{ color: #8FA6B3; font-size: 10px; padding-top: 2px; }}
            QPushButton#featuredLaunchButton {{ background: {COLORS['yellow']}; color: #17232D; border: none; border-radius: 11px; padding: 12px 16px; font-weight: 900; font-size: 13px; }}
            QPushButton#featuredLaunchButton:hover {{ background: #F0D582; }}
            QFrame#chartPanel, QFrame#dashboardPanel {{ background: #20313D; border: 1px solid rgba(169,200,216,0.16); border-radius: 17px; }}
            QWidget#activityChart {{ background: #1B2B36; border-radius: 12px; }}
            QFrame#historyRow {{ background: #1B2B36; border: 1px solid rgba(169,200,216,0.10); border-radius: 11px; }}
            QLabel#historyTitle {{ color: {COLORS['milk']}; font-size: 12px; font-weight: 800; }}
            QLabel#metricDetail {{ color: #B9D6E4; font-size: 10px; }}
            QLabel#statsSourceLabel {{ color: #8097A4; font-size: 10px; padding-bottom: 2px; }}
            QLabel#sessionStatus {{ color: {COLORS['muted']}; font-size: 9px; font-weight: 700; }}
            QLabel#detailMetricValue {{ color: {COLORS['milk']}; font-size: 14px; font-weight: 800; }}
            QFrame#metricDetailCard {{ background: #1B2B36; border: 1px solid rgba(169,200,216,0.12); border-radius: 11px; }}
            QLineEdit#catalogSearch {{ padding-left: 14px; min-height: 21px; }}
            QPushButton:disabled {{ color: #637985; background: #263742; border-color: #354956; }}
            QToolTip {{ color: {COLORS['milk']}; background: #263B49; border: 1px solid {COLORS['border']}; padding: 5px 7px; }}
            QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: #3D5666; min-width: 28px; border-radius: 4px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        """)

    def show_page(self, key: str) -> None:
        pages = {"home": 0, "catalog": 1, "results": 2, "settings": 3, "game_stats": 4}
        if key not in pages:
            key = "home"
        self.pages.setCurrentIndex(pages[key])
        for button_key, button in self.nav_buttons.items():
            button.setChecked(button_key == key)
        titles = {
            "home": ("Добро пожаловать в MoveUp", "Реабилитация без скуки"),
            "catalog": ("Библиотека игр", "Найди нужный сценарий и открой его статистику"),
            "results": ("Твой прогресс", "Показатели и история тренировок"),
            "settings": ("Настройки системы", "Подготовь устройство и среду запуска"),
            "game_stats": ("Статистика игры", "Индивидуальные показатели и история занятий"),
        }
        self.topbar_title.setText(self.game_detail_title.text() if key == "game_stats" and hasattr(self, "game_detail_title") else titles[key][0])
        self.topbar_subtitle.setText(titles[key][1])
        if key == "settings":
            self._select_preferred_port_in_combo()
        elif key == "results":
            self._refresh_results_page()
        elif key == "game_stats":
            self._refresh_game_stats_page()
        if key == "catalog":
            # The stacked page may have changed size since the last time it was visible.
            QTimer.singleShot(0, self._rebuild_catalog)

    def launch_game(self, game_id: str) -> None:
        game = next((item for item in self.config.games if item.id == game_id), None)
        if not game:
            return
        if game_id in self.game_processes and self.game_processes[game_id].poll() is None:
            QMessageBox.information(self, "Игра уже запущена", f"«{game.title}» уже работает.")
            return

        game_path = self.resolve_game_path(game)
        if not game_path or not game_path.is_file():
            answer = QMessageBox.question(
                self,
                "Нужно указать файл",
                f"Для игры «{game.title}» не найден файл запуска.\n\nПроверенный путь: {game_path or 'не задан'}\n\nВыбрать .py или .exe сейчас?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.configure_game(game_id)
            return

        game_path = game_path.resolve(strict=False)
        if game_path.suffix.lower() not in {".py", ".exe"}:
            QMessageBox.warning(self, "Неподдерживаемый файл", "Поддерживаются файлы .py и .exe.")
            return

        command: list[str]
        if game_path.suffix.lower() == ".py":
            python_path = self.config.python_executable.strip() or find_default_python()
            resolved_python = shutil.which(python_path) if not Path(python_path).is_file() else python_path
            if not resolved_python:
                QMessageBox.critical(
                    self,
                    "Python не найден",
                    "Не удалось найти интерпретатор Python. Укажи корректный путь в настройках.",
                )
                self.show_page("settings")
                return
            command = [resolved_python, str(game_path)]
        else:
            command = [str(game_path)]

        if game.args.strip():
            import shlex
            try:
                command.extend(shlex.split(game.args, posix=(os.name != "nt")))
            except ValueError as error:
                QMessageBox.warning(
                    self,
                    "Некорректные аргументы запуска",
                    f"Проверь кавычки и формат аргументов игры «{game.title}».\n\n{error}",
                )
                return

        session_id = ""
        result_path: Path | None = None
        try:
            session_id, result_path = self.statistics.start_session(game)
            child_env = os.environ.copy()
            child_env.update({
                "MOVEUP_GAME_ID": game.id,
                "MOVEUP_SESSION_ID": session_id,
                "MOVEUP_STATS_FILE": str(result_path),
                "MOVEUP_STATS_SCHEMA_VERSION": str(StatisticsStore.SCHEMA_VERSION),
            })
            process = subprocess.Popen(
                command,
                cwd=str(game_path.parent),
                shell=False,
                stdout=None,
                stderr=None,
                env=child_env,
            )
            self.game_processes[game_id] = process
            self.active_session_ids[game_id] = session_id
            self.session_result_paths[game_id] = result_path
            self.bottom_controller_label.setToolTip(f"Запущена игра «{game.title}» · PID {process.pid}")
            self._refresh_views()
            self._refresh_results_page()
        except (OSError, ValueError, RuntimeError) as error:
            if session_id:
                try:
                    self.statistics.finish_session(session_id, -1, result_path)
                except (OSError, RuntimeError):
                    pass
            QMessageBox.critical(
                self,
                "Не удалось запустить игру",
                f"Команда запуска:\n{' '.join(command)}\n\nОшибка: {error}",
            )

    def configure_game(self, game_id: str) -> None:
        game_index = next((i for i, item in enumerate(self.config.games) if item.id == game_id), None)
        if game_index is None:
            return
        game = self.config.games[game_index]
        dialog = AddGameDialog(self, game, Path(self.config.default_source))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        game.title = values["title"]
        game.description = values["description"]
        game.category = values["category"]
        game.path = values["path"]
        game.args = values["args"]
        try:
            self.config.save()
        except RuntimeError as error:
            QMessageBox.critical(self, "Ошибка сохранения", str(error))
        self._refresh_views()

    def add_game(self) -> None:
        dialog = AddGameDialog(self, default_source_path=Path(self.config.default_source))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        game = GameEntry(
            id=f"custom_{os.urandom(4).hex()}",
            title=values["title"],
            description=values["description"],
            category=values["category"],
            path=values["path"],
            args=values["args"],
            accent="#A9C8D8",
            art="dots",
        )
        self.config.games.append(game)
        try:
            self.config.save()
        except RuntimeError as error:
            QMessageBox.critical(self, "Ошибка сохранения", str(error))
        self._refresh_views()

    def _refresh_views(self) -> None:
        self._rebuild_catalog()
        self._populate_featured()
        self._rebuild_home_tiles()
        self._update_home_metrics()
        self._refresh_results_page()
        if hasattr(self, "game_metric_grid") and self.selected_stats_game_id:
            self._refresh_game_stats_page()

    def _update_home_metrics(self) -> None:
        # Poll child processes and commit a session as soon as a game exits.
        completed_any = False
        for game_id, process in list(self.game_processes.items()):
            exit_code = process.poll()
            if exit_code is None:
                continue
            self.game_processes.pop(game_id, None)
            session_id = self.active_session_ids.pop(game_id, "")
            result_path = self.session_result_paths.pop(game_id, None)
            if session_id:
                try:
                    self.statistics.finish_session(session_id, int(exit_code), result_path)
                except (OSError, RuntimeError) as error:
                    self.bottom_controller_label.setToolTip(f"Ошибка сохранения статистики: {error}")
            completed_any = True

        self._heartbeat_ticks += 1
        if self._heartbeat_ticks >= 10:
            self._heartbeat_ticks = 0
            try:
                self.statistics.heartbeat_running_sessions(set(self.active_session_ids.values()))
            except OSError as error:
                self.bottom_controller_label.setToolTip(f"Не удалось сохранить статистику: {error}")

        # Defensive initialization: home-page construction may refresh metrics before
        # all timers/counters are initialized in older local copies.
        self._external_sync_ticks = getattr(self, "_external_sync_ticks", 0) + 1
        if self._external_sync_ticks >= 5:
            self._external_sync_ticks = 0
            try:
                if self.statistics.sync_external_statistics():
                    completed_any = True
            except (OSError, RuntimeError) as error:
                self.bottom_controller_label.setToolTip(f"Ошибка синхронизации статистики игр: {error}")

        summary_count = self.statistics.session_count()
        total_seconds = self.statistics.total_duration()
        if hasattr(self, "game_count_card"):
            self.game_count_card.value_label.setText(str(len(self.config.games)))  # type: ignore[attr-defined]
        if hasattr(self, "launch_count_card"):
            self.launch_count_card.value_label.setText(str(summary_count))  # type: ignore[attr-defined]
        if hasattr(self, "total_time_card"):
            self.total_time_card.value_label.setText(format_duration(total_seconds))  # type: ignore[attr-defined]
        if hasattr(self, "controller_metric_card"):
            text = "Подключён" if getattr(self, "_pico_port", "") else "Не обнаружен"
            self.controller_metric_card.value_label.setText(text)  # type: ignore[attr-defined]
        if completed_any:
            self._refresh_views()
        elif hasattr(self, "activity_chart") and self.pages.currentIndex() == 2 and self._heartbeat_ticks == 0:
            self._refresh_results_page()
        elif hasattr(self, "game_metric_grid") and self.pages.currentIndex() == 4 and self._heartbeat_ticks == 0:
            self._refresh_game_stats_page()

    def resolve_game_path(self, game: GameEntry) -> Path | None:
        """Resolve a game-specific override or its default-source relative path."""
        if game.path.strip():
            candidate = Path(game.path).expanduser()
            if not candidate.is_absolute():
                candidate = Path(self.config.default_source) / candidate
            return candidate.resolve(strict=False)
        if game.source_path.strip():
            candidate = Path(game.source_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path(self.config.default_source) / candidate
            return candidate.resolve(strict=False)
        return None

    def _show_game_menu(self, game_id: str, anchor: QPushButton) -> None:
        menu = QMenu(anchor)
        configure_action = menu.addAction("Изменить путь и параметры игры…")
        source_action = menu.addAction("Изменить общий каталог игр…")
        menu.addSeparator()
        open_folder_action = menu.addAction("Открыть папку игры")
        game = next((item for item in self.config.games if item.id == game_id), None)
        if game is None:
            return
        resolved_path = self.resolve_game_path(game)
        open_folder_action.setEnabled(bool(resolved_path and resolved_path.exists()))
        selected_action = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if selected_action == configure_action:
            self.configure_game(game_id)
        elif selected_action == source_action:
            self._browse_default_source()
        elif selected_action == open_folder_action and resolved_path:
            folder = resolved_path if resolved_path.is_dir() else resolved_path.parent
            self._open_folder(folder)

    def _browse_default_source(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Выбери каталог игр MoveUp",
            self.source_path_edit.text().strip() or self.config.default_source,
        )
        if selected:
            self.source_path_edit.setText(selected)
            self._save_default_source()

    def _save_default_source(self) -> None:
        candidate = Path(self.source_path_edit.text().strip()).expanduser()
        if not str(self.source_path_edit.text()).strip():
            QMessageBox.warning(self, "Не указан каталог", "Укажи папку, в которой находятся файлы игр.")
            return
        if not candidate.is_absolute():
            candidate = app_dir() / candidate
        self.config.default_source = str(candidate.resolve(strict=False))
        self.source_path_edit.setText(self.config.default_source)
        self._sync_discovered_games()
        try:
            self.config.save()
            self._refresh_views()
            self.refresh_controller_status()
            self.bottom_controller_label.setToolTip(f"Каталог игр: {self.config.default_source}")
            QMessageBox.information(self, "Каталог сохранён", "Каталог игр по умолчанию сохранён.")
        except RuntimeError as error:
            QMessageBox.critical(self, "Ошибка сохранения", str(error))

    def _open_folder(self, folder: Path) -> None:
        if not folder.is_dir():
            QMessageBox.warning(self, "Папка не найдена", f"Каталог не существует:\n{folder}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except (OSError, AttributeError) as error:
            QMessageBox.warning(self, "Не удалось открыть папку", f"Путь:\n{folder}\n\nОшибка: {error}")

    def _browse_python(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите интерпретатор Python",
            str(Path.home()),
            "Python (python*;*.exe);;Все файлы (*)",
        )
        if selected:
            self.python_path_edit.setText(selected)

    def _save_python_settings(self) -> None:
        candidate = self.python_path_edit.text().strip()
        if not candidate:
            QMessageBox.warning(self, "Пустое значение", "Укажи команду python или путь к интерпретатору.")
            return
        if Path(candidate).is_absolute() and not Path(candidate).is_file():
            QMessageBox.warning(self, "Файл не найден", "Указанный интерпретатор не найден. Проверь путь.")
            return
        if not Path(candidate).is_absolute() and shutil.which(candidate) is None:
            QMessageBox.warning(self, "Интерпретатор не найден", f"Команда «{candidate}» не найдена в PATH.")
            return
        self.config.python_executable = candidate
        try:
            self.config.save()
            QMessageBox.information(self, "Сохранено", "Интерпретатор Python сохранён.")
        except RuntimeError as error:
            QMessageBox.critical(self, "Ошибка сохранения", str(error))

    def _serial_ports(self) -> list[tuple[str, str, bool]]:
        """Return (device, display label, is_pico) using USB identifiers and descriptors."""
        ports: list[tuple[str, str, bool]] = []
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]
            for port in list_ports.comports():
                description = port.description or "Последовательное устройство"
                manufacturer = getattr(port, "manufacturer", None) or ""
                product = getattr(port, "product", None) or ""
                hwid = getattr(port, "hwid", None) or ""
                descriptor_text = " ".join((description, manufacturer, product, hwid)).lower()
                vendor_id = getattr(port, "vid", None)
                product_id = getattr(port, "pid", None)
                has_pico_vid = vendor_id == 0x2E8A
                has_pico_descriptor = any(
                    marker in descriptor_text
                    for marker in ("raspberry pi pico", "rp2040", "pico", "board in fs mode")
                )
                is_pico = bool(has_pico_vid or has_pico_descriptor)
                if has_pico_vid and product_id is not None:
                    label = f"{port.device} — Raspberry Pi Pico (USB {vendor_id:04X}:{product_id:04X})"
                elif is_pico:
                    label = f"{port.device} — Raspberry Pi Pico ({description})"
                else:
                    label = f"{port.device} — {description}"
                ports.append((port.device, label, is_pico))
        except ImportError:
            pass

        # Port-name fallback provides a useful device list, but cannot identify Pico by itself.
        if not ports and os.name != "nt":
            candidates = list(Path("/dev").glob("ttyACM*")) + list(Path("/dev").glob("ttyUSB*")) + list(Path("/dev").glob("cu.*"))
            ports = [(str(path), str(path), False) for path in sorted(candidates)]
        return ports

    def refresh_controller_status(self) -> None:
        ports = self._serial_ports()
        self._detected_ports = ports
        pico_ports = [item for item in ports if item[2]]
        chosen_port = self.config.preferred_port
        selected_pico = next((item for item in pico_ports if item[0] == chosen_port), None)
        detected_pico = selected_pico or (pico_ports[0] if pico_ports else None)
        self._pico_port = detected_pico[0] if detected_pico else ""

        if hasattr(self, "port_combo"):
            previous = self.port_combo.currentData() or self.config.preferred_port
            self.port_combo.blockSignals(True)
            self.port_combo.clear()
            self.port_combo.addItem("Автоматически выбирать Raspberry Pi Pico", "")
            for device, label, _is_pico in ports:
                self.port_combo.addItem(label, device)
            index = self.port_combo.findData(previous)
            if index < 0 and detected_pico:
                index = self.port_combo.findData(detected_pico[0])
            self.port_combo.setCurrentIndex(index if index >= 0 else 0)
            self.port_combo.blockSignals(False)

        if detected_pico:
            port_text = detected_pico[0]
            other_pico_count = len(pico_ports)
            extra = f" (+ ещё {other_pico_count - 1})" if other_pico_count > 1 else ""
            status_text = f"Джойстик подключён · Raspberry Pi Pico: {port_text}{extra}"
            status_color = COLORS["green"]
            self.sidebar_controller_state.setText(f"Raspberry Pi Pico обнаружен на {port_text}. Джойстик считается подключённым.")
            self.topbar_status.setText(f"●  Джойстик подключён · {port_text}")
            self.topbar_status.setStyleSheet(f"color: {status_color};")
            self.bottom_controller_label.setText(f"Джойстик MoveUp подключён: {port_text}")
            self.bottom_controller_indicator.setStyleSheet(f"color: {status_color};")
            if hasattr(self, "controller_settings_state"):
                self.controller_settings_state.setText(
                    f"Raspberry Pi Pico обнаружен на {port_text}.\n"
                    "Определение выполняется по USB VID 0x2E8A и описанию устройства. "
                    "Наличие платы подтверждено; обмен данными с прошивкой этим тестом не проверяется."
                )
        elif ports:
            devices = ", ".join(item[0] for item in ports[:4])
            if len(ports) > 4:
                devices += ", …"
            status_text = "Найдены другие USB/COM-устройства, Pico не обнаружен"
            self.sidebar_controller_state.setText(status_text + f": {devices}")
            self.topbar_status.setText("●  Raspberry Pi Pico не обнаружен")
            self.topbar_status.setStyleSheet(f"color: {COLORS['yellow']};")
            self.bottom_controller_label.setText("Джойстик MoveUp: Raspberry Pi Pico не обнаружен")
            self.bottom_controller_indicator.setStyleSheet(f"color: {COLORS['yellow']};")
            if hasattr(self, "controller_settings_state"):
                self.controller_settings_state.setText(
                    f"Обнаружены последовательные устройства: {devices}. "
                    "Ни одно из них не распознано как Raspberry Pi Pico. Проверь USB-кабель и режим работы платы."
                )
        else:
            self.sidebar_controller_state.setText("Raspberry Pi Pico не обнаружен")
            self.topbar_status.setText("●  Джойстик не подключён")
            self.topbar_status.setStyleSheet(f"color: {COLORS['muted']};")
            self.bottom_controller_label.setText("Джойстик MoveUp: не обнаружен")
            self.bottom_controller_indicator.setStyleSheet("color: #657C89;")
            if hasattr(self, "controller_settings_state"):
                if self._pyserial_missing():
                    self.controller_settings_state.setText(
                        "Автоматическое обнаружение требует pyserial. Установи пакет командой: python -m pip install pyserial."
                    )
                else:
                    self.controller_settings_state.setText(
                        "Raspberry Pi Pico не обнаружен. Проверь USB-кабель, драйвер и прошивку платы. "
                        "Если плата работает только в BOOTSEL-режиме, она может не появиться как последовательный порт."
                    )
        self._update_home_metrics()

    def _pyserial_missing(self) -> bool:
        try:
            import serial.tools.list_ports  # noqa: F401
            return False
        except ImportError:
            return True

    def _select_preferred_port_in_combo(self) -> None:
        if not hasattr(self, "port_combo"):
            return
        index = self.port_combo.findData(self.config.preferred_port)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def _save_preferred_port(self) -> None:
        self.config.preferred_port = str(self.port_combo.currentData() or "")
        try:
            self.config.save()
            self.refresh_controller_status()
            QMessageBox.information(self, "Сохранено", "Настройка порта сохранена.")
        except RuntimeError as error:
            QMessageBox.critical(self, "Ошибка сохранения", str(error))

    def _open_config_folder(self) -> None:
        self._open_folder(self.config.path.parent)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if not hasattr(self, "catalog_grid_host"):
            return
        # Defer until the scroll viewport and its layouts have updated their geometry.
        QTimer.singleShot(0, self._rebuild_catalog)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Finalize children that have already exited; retain observed time for still-running children.
        for game_id, process in list(self.game_processes.items()):
            exit_code = process.poll()
            session_id = self.active_session_ids.get(game_id, "")
            result_path = self.session_result_paths.get(game_id)
            try:
                if session_id and exit_code is not None:
                    self.statistics.finish_session(session_id, int(exit_code), result_path)
                elif session_id:
                    self.statistics.close_tracking(session_id)
            except (OSError, RuntimeError):
                pass
        super().closeEvent(event)



def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("MoveUp")
    icon_path = app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    # Set palette roles explicitly; the positional QPalette constructor differs
    # across Qt/PySide6 versions and may reject QColor arguments.
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["milk"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["sidebar"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["milk"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["milk"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["milk"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLORS["yellow"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["blue"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(COLORS["muted"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(COLORS["muted"]))
    app.setPalette(palette)
    app.setFont(QFont("Inter", 10))
    window = MoveUpLauncher()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
