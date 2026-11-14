"""Окно настроек: одна прокручиваемая страница, группы-карточки. Всё применяется сразу, кнопки «ОК» нет.

Окно ничего не знает о переводе: оно читает и пишет настройки и зовёт функции, которые ему передали
(словарь actions). Так его легко проверить отдельно.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import __version__
from .family import tokens, win
from .overlay import SIZES

PROFILES = [("auto", "Авто"), ("lesson", "Урок — только офлайн"), ("important", "Важное — DeepL")]
SOURCES = [("auto", "Авто"), ("teams", "Только Teams"), ("windows", "Субтитры Windows")]
HOTKEYS = [("toggle", "Старт / стоп перевода"), ("window", "Показать / спрятать окно"),
           ("profile", "Профиль «Урок» ↔ «Важное»")]


class Settings(QWidget):
    def __init__(self, cfg, actions: dict):
        super().__init__()
        self.cfg, self.actions = cfg, actions
        self.setWindowTitle("Dolmetsch — настройки")
        self.resize(560, 720)
        c = tokens.colors()
        self.setStyleSheet(tokens.card_qss(c))

        page = QWidget()
        self.col = QVBoxLayout(page)
        self.col.setContentsMargins(20, 16, 20, 20)
        self.col.setSpacing(12)
        self._translation()
        self._source()
        self._window()
        self._files()
        self._hotkeys()
        self._startup()
        self.col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        win.dark_title_bar(self, tokens.system_dark())
        self.refresh()

    # ---------- помощники
    def card(self, title: str) -> QVBoxLayout:
        frame = QFrame(objectName="card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(10)
        box.addWidget(QLabel(title, objectName="title"))
        self.col.addWidget(frame)
        return box

    @staticmethod
    def row(box: QVBoxLayout, label: str, widget: QWidget | None = None, hint: str = "") -> QLabel:
        """Строка настроек: подпись слева, элемент справа, под ними пояснение."""
        line = QHBoxLayout()
        line.setSpacing(16)
        line.addWidget(QLabel(label, wordWrap=True), 1)
        if widget is not None:
            line.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        box.addLayout(line)
        note = QLabel(hint, objectName="hint", wordWrap=True)
        note.setVisible(bool(hint))
        box.addWidget(note)
        return note

    def check(self, grid: QVBoxLayout, key: str, label: str, hint: str = "", then=None) -> QCheckBox:
        box = QCheckBox()
        box.setChecked(bool(self.cfg[key]))

        def changed(on: bool) -> None:
            self.cfg.set(key, bool(on))
            if then:
                then(bool(on))
        box.toggled.connect(changed)
        self.row(grid, label, box, hint)
        return box

    @staticmethod
    def combo(items, current) -> QComboBox:
        box = QComboBox()
        for key, title in items:
            box.addItem(title, key)
        box.setCurrentIndex(max(0, box.findData(current)))
        return box

    def call(self, name: str, *args):
        fn = self.actions.get(name)
        return fn(*args) if fn else None

    # ---------- группы
    def _translation(self) -> None:
        g = self.card("Перевод")
        self.profile = self.combo(PROFILES, self.cfg["profile"])
        self.profile.activated.connect(lambda _i: self.call("profile", self.profile.currentData()))
        self.row(g, "Профиль", self.profile,
                 "«Авто»: в часы уроков — «Урок», в остальное время — «Важное». "
                 "«Урок»: текст никуда не уходит. «Важное»: точнее, но текст отправляется в DeepL.")
        hours = QWidget()
        h = QHBoxLayout(hours)
        h.setContentsMargins(0, 0, 0, 0)
        self.h_from, self.h_to = QSpinBox(), QSpinBox()
        for spin, key in ((self.h_from, "from"), (self.h_to, "to")):
            spin.setRange(0, 24)
            spin.setValue(int(self.cfg["lesson_hours"][key]))
            spin.valueChanged.connect(self._hours)
        h.addWidget(self.h_from)
        h.addWidget(QLabel("–"))
        h.addWidget(self.h_to)
        self.row(g, "Часы уроков (будни)", hours)
        key = QWidget()
        k = QHBoxLayout(key)
        k.setContentsMargins(0, 0, 0, 0)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setMinimumWidth(190)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save_key)
        k.addWidget(self.key)
        k.addWidget(save)
        self.key_note = self.row(g, "Ключ DeepL", key, " ")
        self.key_note.setVisible(True)

    def _hours(self) -> None:
        hours = dict(self.cfg["lesson_hours"])
        hours["from"], hours["to"] = self.h_from.value(), self.h_to.value()
        self.cfg.set("lesson_hours", hours)
        self.call("hours", hours)

    def _save_key(self) -> None:
        self.key_note.setText(self.call("key", self.key.text()) or "")
        self.key.clear()
        self.refresh()

    def _source(self) -> None:
        g = self.card("Источник субтитров")
        self.source = self.combo(SOURCES, self.cfg["source"])
        self.source.activated.connect(lambda _i: (self.cfg.set("source", self.source.currentData()),
                                                  self.call("restart")))
        self.row(g, "Откуда брать текст", self.source,
                 "«Авто»: субтитры Teams, а если их нет — вопрос, включить ли субтитры Windows. "
                 "В Teams видны имена говорящих. Субтитры Windows слышат любой звук: Zoom, Meet, видео.")

    def _window(self) -> None:
        g = self.card("Окно субтитров")
        self.size = self.combo([(s, f"{s} px") for s in SIZES], self.cfg["text_px"])
        self.size.activated.connect(lambda _i: self.call("text_px", self.size.currentData()))
        self.row(g, "Размер перевода", self.size)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(60, 100)
        self.opacity.setFixedWidth(170)
        self.opacity.setValue(int(float(self.cfg["opacity"]) * 100))
        self.opacity.valueChanged.connect(lambda v: self.call("opacity", v / 100))
        self.row(g, "Непрозрачность окна", self.opacity)
        self.check(g, "show_original", "Немецкий оригинал под переводом", then=lambda on: self.call("redraw"))
        self.check(g, "show_terms", "Термины из бестиария под переводом",
                   "Если во фразе прозвучал термин из твоего словаря, под переводом будет его перевод.")
        self.check(g, "hide_from_capture", "Не показывать окно при демонстрации экрана",
                   "Собеседники не увидят окно перевода, когда ты показываешь экран.",
                   then=lambda on: self.call("capture"))
        place = QWidget()
        p = QHBoxLayout(place)
        p.setContentsMargins(0, 0, 0, 0)
        for title, preset in (("Внизу", "bottom"), ("Вверху", "top")):
            b = QPushButton(title)
            b.clicked.connect(lambda _c=False, x=preset: self.call("place", x))
            p.addWidget(b)
        self.row(g, "Поставить окно", place)

    def _files(self) -> None:
        g = self.card("Общая папка с Capri")
        self.check(g, "save_transcript", "Сохранять текст встречи",
                   "Профиль «Урок» пишет в «Уроки», «Важное» — в «Собеседования». Capri делает из текста конспект.")
        folders = QWidget()
        f = QHBoxLayout(folders)
        f.setContentsMargins(0, 0, 0, 0)
        for title, name in (("Уроки", "lessons"), ("Собеседования", "interviews"), ("Бестиарий", "bestiary")):
            b = QPushButton(title)
            b.clicked.connect(lambda _c=False, x=name: self.call("open", x))
            f.addWidget(b)
        self.terms_note = self.row(g, "Открыть папку", folders, " ")
        self.terms_note.setVisible(True)

    def _hotkeys(self) -> None:
        g = self.card("Горячие клавиши")
        self.keys = {}
        for key, title in HOTKEYS:
            edit = QLineEdit(self.cfg["hotkeys"][key])
            edit.setFixedWidth(150)
            self.keys[key] = edit
            self.row(g, title, edit)
        apply = QPushButton("Применить")
        apply.clicked.connect(self._apply_keys)
        self.keys_note = self.row(g, "Пиши как «Win+Alt+J». Ctrl+Alt лучше не брать: на немецкой клавиатуре это AltGr.",
                                  apply, " ")
        self.keys_note.setVisible(True)

    def _apply_keys(self) -> None:
        self.keys_note.setText(self.call("hotkeys", {k: e.text().strip() for k, e in self.keys.items()}) or "")

    def _startup(self) -> None:
        g = self.card("Запуск и помощь")
        self.check(g, "autostart", "Начинать перевод сразу при запуске")
        self.boot = QCheckBox()
        self.boot.toggled.connect(lambda on: self.call("boot", bool(on)))
        self.row(g, "Запускать Dolmetsch вместе с Windows", self.boot)
        help_ = QWidget()
        h = QHBoxLayout(help_)
        h.setContentsMargins(0, 0, 0, 0)
        report, folder = QPushButton("Сохранить отчёт"), QPushButton("Журнал и настройки")
        report.clicked.connect(lambda: self.call("report"))
        folder.clicked.connect(lambda: self.call("open", "appdir"))
        h.addWidget(report)
        h.addWidget(folder)
        self.row(g, f"Dolmetsch {__version__}", help_, "В отчёте нет текста встреч и имён: только журнал, настройки и структура окон.")

    # ---------- обновление подписей
    def refresh(self) -> None:
        """Обновить то, что могло измениться снаружи (профиль с клавиши, размер кнопками в окне)."""
        info = self.call("info") or {}
        self.profile.setCurrentIndex(max(0, self.profile.findData(info.get("profile", self.cfg["profile"]))))
        self.size.setCurrentIndex(max(0, self.size.findData(self.cfg["text_px"])))
        self.key.setPlaceholderText("ключ сохранён" if info.get("has_key") else "ключа нет")
        if info.get("usage"):
            self.key_note.setText(info["usage"])
        self.terms_note.setText(f"В бестиарии терминов: {info.get('terms', 0)}. Папка общая с Capri.")
        self.boot.blockSignals(True)
        self.boot.setChecked(bool(info.get("boot")))
        self.boot.blockSignals(False)
