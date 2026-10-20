"""Shared MoveUp session-statistics persistence for standalone games and the launcher."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def metric(label: str, value: Any, unit: str) -> Dict[str, Any]:
    """Create a metric entry compatible with statistics.json."""
    if isinstance(value, float):
        value = round(value, 3)
    return {"label": label, "value": value, "unit": unit}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json_write(destination: Path, payload: Dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(destination.parent),
            prefix=".moveup-stats-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2, allow_nan=False)
            temporary_file.write("\n")
            temporary_path = temporary_file.name
        os.replace(temporary_path, destination)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def resolve_statistics_path(script_file: str) -> Path:
    """Use the nearest common Games folder, otherwise save beside this game."""
    location_path = Path(sys.executable if getattr(sys, "frozen", False) else script_file).resolve()
    script_directory = location_path.parent
    candidate_directories = [script_directory, *script_directory.parents]
    for directory in candidate_directories:
        if directory.name.casefold() == "games":
            return directory / "statistics.json"

    games_directory = next(
        (child for child in script_directory.iterdir()
         if child.is_dir() and child.name.casefold() == "games"),
        None,
    )
    if games_directory is not None:
        return games_directory / "statistics.json"
    return script_directory / "statistics.json"


class MoveUpStatistics:
    """Append or update one session in the common UTF-8 JSON history."""

    def __init__(self, game_title: str, default_game_id: str, script_file: str) -> None:
        self.game_title = game_title
        self.game_id = os.environ.get("MOVEUP_GAME_ID", default_game_id).strip() or default_game_id
        self.session_id = os.environ.get("MOVEUP_SESSION_ID", "").strip() or str(uuid.uuid4())
        self.stats_path = resolve_statistics_path(script_file)
        self.handoff_path = os.environ.get("MOVEUP_STATS_FILE", "").strip()
        if self.handoff_path:
            self.handoff_path = Path(os.path.abspath(os.path.expanduser(self.handoff_path)))
        else:
            self.handoff_path = None
        try:
            self.schema_version = max(1, int(os.environ.get("MOVEUP_STATS_SCHEMA_VERSION", "1")))
        except (TypeError, ValueError):
            self.schema_version = 1
        self.started_at = _utc_now()
        self.started_monotonic = time.monotonic()

    def build_payload(
        self,
        status: str,
        metrics: Dict[str, Any],
        settings: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "metrics_version": 1,
            "session_id": self.session_id,
            "game_id": self.game_id,
            "game_title": self.game_title,
            "started_at": _iso_utc(self.started_at),
            "ended_at": _iso_utc(_utc_now()),
            "duration_seconds": round(max(0.0, time.monotonic() - self.started_monotonic), 3),
            "status": status,
            "metrics": metrics,
        }
        if settings:
            payload["settings"] = settings
        if details:
            payload["details"] = details
        return payload

    def save(
        self,
        status: str,
        metrics: Dict[str, Any],
        settings: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        payload = self.build_payload(status, metrics, settings, details)
        try:
            if self.stats_path.exists():
                try:
                    with self.stats_path.open("r", encoding="utf-8") as statistics_file:
                        history = json.load(statistics_file)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{self.stats_path} содержит некорректный JSON; история не перезаписана"
                    ) from error

                if isinstance(history, dict) and isinstance(history.get("sessions"), list):
                    sessions = history["sessions"]
                elif isinstance(history, dict) and history.get("session_id"):
                    sessions = [history]
                    history = {"schema_version": self.schema_version, "sessions": sessions}
                else:
                    raise ValueError(
                        f"Неподдерживаемая структура {self.stats_path}; история не перезаписана"
                    )
            else:
                history = {"schema_version": self.schema_version, "sessions": []}
                sessions = history["sessions"]

            existing_index = next(
                (index for index, item in enumerate(sessions)
                 if isinstance(item, dict) and item.get("session_id") == self.session_id),
                None,
            )
            if existing_index is None:
                sessions.append(payload)
            else:
                sessions[existing_index] = payload
            try:
                existingSchemaVersion = int(history.get("schema_version", self.schema_version))
            except (TypeError, ValueError):
                existingSchemaVersion = self.schema_version
            history["schema_version"] = max(existingSchemaVersion, self.schema_version)
            history["sessions"] = sessions
            _atomic_json_write(self.stats_path, history)
            print(f"Статистика сохранена: {self.stats_path}")

            if self.handoff_path and self.handoff_path.resolve() != self.stats_path.resolve():
                try:
                    _atomic_json_write(self.handoff_path, payload)
                except (OSError, TypeError, ValueError) as error:
                    print(f"Не удалось сохранить файл передачи лаунчеру: {error}")
            return True
        except (OSError, TypeError, ValueError) as error:
            print(f"Не удалось сохранить статистику MoveUp: {error}")
            return False
