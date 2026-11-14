"""Бестиарий: словарь терминов «немецкий → русский» из общей папки. Dolmetsch его только читает.

Под переводом показываются термины, которые прозвучали во фразе: офлайн-перевод иногда переводит
термин по-своему, а подсказка даёт тот перевод, который записан у тебя.
Формат файлов и правила поиска — те же, что в Capri (capri/live/glossary.py).
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from . import shared

ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|(?:\s*([^|]*?)\s*\|)?\s*$")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def read_csv(path: Path, need_header: bool = False) -> list[tuple[str, str]]:
    """CSV с «;» или «,»: колонки Deutsch и Русский (по заголовку или первые две). → [(немецкий, русский)]."""
    text = _decode(Path(path).read_bytes())
    sample = text[:4000]
    rows = list(csv.reader(io.StringIO(text), delimiter=";" if sample.count(";") >= sample.count(",") else ","))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        return next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    i_de, i_ru = col("deutsch", "german", "begriff", "термин"), col("русск", "russ", "перевод")
    has_head = i_de is not None
    if need_header and (not has_head or i_ru is None):
        return []
    i_de, i_ru = (i_de, 1 if i_ru is None else i_ru) if has_head else (0, 1)
    out = []
    for r in rows[1 if has_head else 0:]:
        if len(r) > max(i_de, i_ru) and r[i_de].strip() and r[i_ru].strip():
            out.append((r[i_de].strip(), r[i_ru].strip()))
    return out


def read_md(path: Path) -> list[tuple[str, str]]:
    """Таблица «| Deutsch | Русский | Заметка |» (файл Уроки\\Словарь.md, который ведёт Capri)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        de, ru = m.group(1).strip(), m.group(2).strip()
        if set(de) <= set("-: ") or de.lower() in ("deutsch", "термин", "begriff") or not ru:
            continue
        out.append((de, ru))
    return out


def variants(term: str) -> list[str]:
    """«Active Directory (AD)» → «Active Directory», «AD»; «Blackbox- / Whitebox-Test» → оба; без артикля."""
    out = []
    paren = re.findall(r"\(([^)]+)\)", term)
    base = re.sub(r"\([^)]*\)", " ", term)
    for part in re.split(r"\s*/\s*|,\s+|\s+vs\.?\s+|\s+(?:oder|und)\s+", base) + paren:
        part = re.sub(r"^(?:der|die|das)\s+", "", part.strip(" -–:;."), flags=re.I).strip()
        if len(part) >= 2 and not re.fullmatch(r"[\d.\s]+", part) and part not in out:
            out.append(part)
    return out or [term.strip()]


class Glossary:
    def __init__(self, folders: list[Path] | None = None):
        self.folders = folders             # None — общая папка
        self.terms: list[tuple[str, str, list[re.Pattern]]] = []
        self._stamp = None

    def files(self) -> list[Path]:
        if self.folders is not None:
            dirs, md = self.folders, []
        else:
            dirs, md = [shared.bestiary_dir(), shared.root()], [shared.lessons_dir() / "Словарь.md"]
        out = []
        for i, d in enumerate(dirs):
            if d.exists():
                # в самой папке Бестиарий берём любые CSV; в соседних — только с заголовком Deutsch;Русский
                out += [(f, i > 0) for f in sorted(d.glob("*.csv")) if not f.name.startswith(("_", "usage"))]
        return out + [(f, False) for f in md if f.exists()]

    def load(self) -> int:
        """Перечитать словарь, если файлы изменились. → сколько терминов."""
        files = self.files()
        try:
            stamp = [(str(f), f.stat().st_mtime, f.stat().st_size) for f, _ in files]
        except OSError:
            stamp = None
        if stamp == self._stamp and stamp is not None:
            return len(self.terms)
        self._stamp = stamp
        seen, terms = set(), []
        for f, need_header in files:
            try:
                rows = read_md(f) if f.suffix.lower() == ".md" else read_csv(f, need_header)
            except (OSError, csv.Error):
                continue
            for de, ru in rows:
                if de.lower() in seen:
                    continue
                seen.add(de.lower())
                patterns = []
                for v in variants(de):
                    core = v.lower()
                    # короткие (AD, DNS) — целым словом и с учётом регистра; длинные — по основе («Freigaben»)
                    if len(core) <= 4:
                        patterns.append(re.compile(rf"(?<!\w){re.escape(v)}(?!\w)"))
                    else:
                        patterns.append(re.compile(rf"(?<!\w){re.escape(core[:max(5, len(core) - 2)])}", re.I))
                terms.append((de, ru, patterns))
        self.terms = terms
        return len(terms)

    def hints(self, text: str, limit: int = 3) -> list[tuple[str, str]]:
        """Термины бестиария, которые встретились в немецкой фразе: [(термин, перевод)]."""
        found = []
        for de, ru, patterns in self.terms:
            hit = next((m for m in (p.search(text) for p in patterns) if m), None)
            if hit:
                found.append((hit.start(), de, ru))
        return [(de, ru) for _pos, de, ru in sorted(found)[:limit]]
