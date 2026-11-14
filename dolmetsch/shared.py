"""Общая папка Capri и Dolmetsch. Оба приложения (и Claude, если подключить папку к чату) работают с ней:

    Документы\\Capri\\
        Бестиарий\\        словарь терминов: CSV «Deutsch;Русский;Заметка;Источник;Тема»
        Уроки\\            текст и конспекты уроков (папка на каждый урок)
        Собеседования\\    текст собеседований (папка на каждое)
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path


def documents_dir() -> Path:
    """Папка «Документы» (у Игоря она в OneDrive — спрашиваем у Windows, а не гадаем)."""
    if os.name == "nt":
        try:
            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                            ("d", ctypes.c_ubyte * 8)]
            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def root() -> Path:
    return documents_dir() / "Capri"


def bestiary_dir() -> Path:
    return root() / "Бестиарий"


def lessons_dir() -> Path:
    return root() / "Уроки"


def interviews_dir() -> Path:
    return root() / "Собеседования"


def meeting_dir(kind: str) -> Path:
    """Куда писать текст встречи: «lesson» → Уроки, «important» → Собеседования."""
    return interviews_dir() if kind == "important" else lessons_dir()


def ensure() -> Path:
    """Создать общую папку и подпапки (ничего не трогает, если они уже есть)."""
    for folder in (bestiary_dir(), lessons_dir(), interviews_dir()):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    readme = root() / "Прочти меня.txt"
    if not readme.exists():
        try:
            readme.write_text(
                "Общая папка Capri и Dolmetsch.\n\n"
                "Бестиарий\\      — словарь терминов. Файлы CSV с колонками Deutsch;Русский;Заметка;Источник;Тема.\n"
                "                  Положи сюда свой файл — оба приложения подхватят его сами.\n"
                "Уроки\\          — текст и конспекты уроков, папка на каждый урок.\n"
                "Собеседования\\  — текст собеседований.\n\n"
                "Чтобы Claude мог читать бестиарий и уроки, подключи эту папку к чату в приложении Claude.\n",
                encoding="utf-8")
        except OSError:
            pass
    return root()
