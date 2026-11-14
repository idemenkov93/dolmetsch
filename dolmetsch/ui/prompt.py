"""Карточка-вопрос в правом нижнем углу: «Включить субтитры Windows?» — Да / Нет.

Своя карточка, а не системное уведомление: кнопки в уведомлениях Windows требуют регистрации
приложения и в сборке PyInstaller работают ненадёжно. Фокус карточка не забирает.
Без ответа закрывается сама через полминуты — это считается ответом «Нет».
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .family import tokens, win
from .overlay import FLAGS

AUTO_CLOSE = 30         # секунд до автоматического «Нет»


class AskCard(QWidget):
    answered = Signal(bool)
    how = Signal()

    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(360)
        c = self.c = tokens.DARK
        self.setStyleSheet(
            f"QLabel#title {{ color: {c['text']}; font-size: 14px; font-weight: 600; }}"
            f"QLabel#hint {{ color: {c['text3']}; font-size: 12px; }}"
            f"QPushButton {{ min-height: 30px; padding: 0 16px; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" color: {c['text']}; background: rgba(255,255,255,0.06); border: 1px solid {c['stroke']}; font-size: 14px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.10); }}"
            f"QPushButton#yes {{ background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 600; }}"
            f"QPushButton#yes:hover {{ background: #7AD6FF; }}"
            f"QPushButton#link {{ background: transparent; border: none; color: {c['accent']}; padding: 0; font-size: 12px; }}")
        self.title = QLabel(objectName="title", wordWrap=True)
        self.hint = QLabel("Работают с любым звуком: Zoom, Meet, видео. Язык распознавания — Deutsch.",
                           objectName="hint", wordWrap=True)
        self.yes, self.no = QPushButton("Да", objectName="yes"), QPushButton("Нет")
        self.link = QPushButton("Как включить в Teams", objectName="link")
        for b in (self.yes, self.no, self.link):
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
        self.yes.clicked.connect(lambda: self._done(True))
        self.no.clicked.connect(lambda: self._done(False))
        self.link.clicked.connect(self.how.emit)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.yes)
        buttons.addWidget(self.no)
        buttons.addStretch(1)
        buttons.addWidget(self.link)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(6)
        box.addWidget(self.title)
        box.addWidget(self.hint)
        box.addSpacing(6)
        box.addLayout(buttons)
        self.left = 0
        self.timer = QTimer(self, interval=1000, timeout=self._tick)

    def ask(self, teams_running: bool) -> None:
        """Показать карточку над областью уведомлений."""
        self.title.setText("Субтитров Teams нет. Включить субтитры Windows?" if teams_running
                           else "Встреча не в Teams? Включить субтитры Windows?")
        self.left = AUTO_CLOSE
        self.no.setText(f"Нет · {self.left}")
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)
        self.show()
        self.timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        win.round_corners(self)
        win.no_activate(self)

    def _tick(self) -> None:
        self.left -= 1
        self.no.setText(f"Нет · {self.left}")
        if self.left <= 0:
            self._done(False)

    def _done(self, yes: bool) -> None:
        self.timer.stop()
        self.hide()
        self.answered.emit(yes)

    def dismiss(self) -> None:
        """Убрать карточку без ответа (субтитры появились сами)."""
        self.timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), tokens.qcolor(self.c["surface"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(tokens.qcolor(self.c["stroke"]), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), tokens.RADIUS_CARD, tokens.RADIUS_CARD)
        p.end()
