"""Текст встречи: пишется в файл по ходу, строка за строкой (сбой не теряет написанное).

Формат и папка — как у Capri, чтобы она могла сделать конспект и пополнить бестиарий:
    Документы\\Capri\\Уроки\\2026-11-14 0900 — Тема\\транскрипт.md          (профиль «Урок»)
    Документы\\Capri\\Собеседования\\2026-11-14 1400 — Тема\\транскрипт.md  (профиль «Важное»)
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import shared


def _stamp(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class Transcript:
    def __init__(self, root: Path | None = None, now=dt.datetime.now):
        self.root, self.now = root, now
        self.kind = "lesson"                 # lesson → Уроки, important → Собеседования
        self.path: Path | None = None
        self.started: dt.datetime | None = None
        self.title = ""
        self.lines = 0
        self.error = ""

    def set_title(self, title: str) -> None:
        """Название встречи (из заголовка окна). Действует, пока файл ещё не создан."""
        if self.path is None:
            self.title = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", title or "")).strip()[:60]

    def _open(self) -> None:
        self.started = self.now()
        root = self.root or shared.meeting_dir(self.kind)
        name = f"{self.started:%Y-%m-%d %H%M}" + (f" — {self.title}" if self.title else "")
        folder, n = root / name, 2
        while (folder / "транскрипт.md").exists():
            folder, n = root / f"{name} ({n})", n + 1
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "транскрипт.md"
        self.path.write_text(
            f"# Транскрипт {self.started:%d.%m.%Y %H:%M}\n\n"
            "_Текст из субтитров встречи (Dolmetsch). Звук не сохранялся._\n\n", encoding="utf-8")

    def add(self, speaker: str, text: str, translation: str) -> None:
        """Дописать реплику. Ошибка записи не останавливает перевод — только запоминается."""
        try:
            if self.path is None:
                self._open()
            at = _stamp((self.now() - self.started).total_seconds())
            block = f"**[{at}] {speaker or 'Говорящий'}:** {text}\n"
            if translation:
                block += f"> {translation}\n"
            with self.path.open("a", encoding="utf-8") as f:
                f.write(block + "\n")
            self.lines += 1
            self.error = ""
        except OSError as e:
            self.error = f"текст встречи не пишется: {e.strerror or e}"

    def close(self) -> Path | None:
        """Закончить встречу: следующий старт начнёт новый файл."""
        path, self.path, self.started, self.lines = self.path, None, None, 0
        return path
