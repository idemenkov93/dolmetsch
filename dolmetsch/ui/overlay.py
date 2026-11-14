"""Окно субтитров поверх экрана.

Устройство (снизу вверх): строка состояния → недописанная немецкая фраза → текущий перевод
(крупно) с именем говорящего → предыдущие переводы, чем старше, тем бледнее.
Размер окна не меняется от текста: новое появляется снизу, старое уезжает вверх — текст не «прыгает».

Окно непрозрачное и без эффектов: на него смотрят весь день, а прозрачные окна без рамки в Qt
заметно грузят процессор. Фокус окно не забирает — можно печатать в Teams.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFontMetricsF, QGuiApplication, QPainter, QPen, QTextLayout
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from .family import icons, tokens, win

FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
SIZES = (18, 20, 22, 24, 28)            # размер текущего перевода, px
PAD_X, PAD_TOP, PAD_BOTTOM = 16, 12, 10
MIN_W, MIN_H = 480, 150


def wrap(text: str, font, width: float, max_lines: int, keep: str = "start") -> list[str]:
    """Разбить текст на строки по ширине. Не помещается в max_lines — обрезать с многоточием:
    keep="start" — оставить начало (перевод), keep="end" — оставить конец (фраза, которая ещё дописывается)."""
    if not text or width <= 10:
        return []
    layout = QTextLayout(text, font)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(text[line.textStart():line.textStart() + line.textLength()].strip())
    layout.endLayout()
    if len(lines) <= max_lines:
        return lines
    metrics = QFontMetricsF(font)
    if keep == "start":
        return lines[:max_lines - 1] + [metrics.elidedText(" ".join(lines[max_lines - 1:]), Qt.ElideRight, width)]
    cut = len(lines) - max_lines + 1
    return [metrics.elidedText(" ".join(lines[:cut]), Qt.ElideLeft, width)] + lines[cut:]


class Grip(QLabel):
    """Уголок для изменения размера. Сделан вручную: системное перетаскивание плохо работает
    у окон, которые не забирают фокус."""

    def __init__(self, window: QWidget):
        super().__init__("◢", window)
        self.window_, self._start = window, None
        self.setCursor(Qt.SizeFDiagCursor)
        self.setFixedSize(16, 16)
        self.setStyleSheet(f"color: {tokens.DARK['fade2']}; font-size: 10px; background: transparent;")

    def mousePressEvent(self, event) -> None:
        self._start = (event.globalPosition().toPoint(), self.window_.size())

    def mouseMoveEvent(self, event) -> None:
        if self._start:
            delta = event.globalPosition().toPoint() - self._start[0]
            self.window_.resize(max(MIN_W, self._start[1].width() + delta.x()),
                                max(MIN_H, self._start[1].height() + delta.y()))

    def mouseReleaseEvent(self, event) -> None:
        self._start = None


class Overlay(QWidget):
    menu_requested = Signal(QPoint)
    geometry_changed = Signal(list)
    pause_clicked = Signal()
    settings_clicked = Signal()
    size_step = Signal(int)                 # +1 / −1: крупнее / мельче
    hide_clicked = Signal()

    def __init__(self, text_px: int = 22, opacity: float = 1.0, show_original: bool = True,
                 geometry: list | None = None):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Dolmetsch")
        self.setMinimumSize(MIN_W, MIN_H)
        self.c = tokens.DARK
        self.text_px, self.show_original = text_px, show_original
        self.lines: list = []               # последние переводы (объекты Line)
        self.partial = ("", "")             # недописанная фраза: (кто, немецкий текст)
        self.draft = ""                     # черновой перевод недописанной фразы (только на видеокарте)
        self.status, self.level = "Запуск…", "warn"
        self.notice = ""                    # временное сообщение вместо строки состояния
        self.running = False
        self._drag = None

        # панель кнопок: видна только при наведении мыши
        self.bar = QWidget(self)
        self.bar.setStyleSheet(
            f"QWidget {{ background: {self.c['overlay']}; }}"
            f"QToolButton {{ border: none; border-radius: {tokens.RADIUS_CONTROL}px; background: transparent; }}"
            f"QToolButton:hover {{ background: {self.c['hover']}; }}")
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(2)
        self.btn_pause = self._button("pause", "Пауза / продолжить", self.pause_clicked.emit, row)
        self._button("font_decrease", "Мельче", lambda: self.size_step.emit(-1), row)
        self._button("font_increase", "Крупнее", lambda: self.size_step.emit(+1), row)
        self._button("settings", "Настройки", self.settings_clicked.emit, row)
        self._button("dismiss", "Спрятать окно", self.hide_clicked.emit, row)
        self.bar.adjustSize()
        self.bar.hide()
        self.grip = Grip(self)
        self.grip.hide()

        self._notice_timer = QTimer(self, singleShot=True, timeout=self._clear_notice)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._emit_geometry)
        self._hover_timer = QTimer(self, singleShot=True, interval=500, timeout=self._check_hover)
        self.setWindowOpacity(min(1.0, max(0.5, opacity)))
        self._place(geometry)

    def _button(self, icon: str, tip: str, slot, row) -> QToolButton:
        b = QToolButton(self.bar)
        b.setIcon(icons.icon(icon, self.c["text2"]))
        b.setIconSize(icons.SIZE)
        b.setFixedSize(32, 32)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        row.addWidget(b)
        return b

    # ---------- положение
    def default_geometry(self, preset: str = "bottom") -> list:
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        w = int(min(1100, max(640, screen.width() * 0.62)))
        w = min(w, screen.width() - 32)
        h = 240
        x = screen.x() + (screen.width() - w) // 2
        y = screen.y() + 48 if preset == "top" else screen.bottom() - h - 48
        return [x, y, w, h]

    def _place(self, geometry: list | None) -> None:
        if geometry and len(geometry) == 4:
            x, y, w, h = geometry
            # сохранённое место годится, если оно видно на каком-то из мониторов
            if any(s.availableGeometry().contains(QPoint(x + 40, y + 20)) for s in QGuiApplication.screens()):
                return self.setGeometry(x, y, max(MIN_W, w), max(MIN_H, h))
        self.setGeometry(*self.default_geometry())

    def place(self, preset: str) -> None:
        """Поставить окно внизу или вверху экрана (вверху удобнее на видеовстречах)."""
        self.setGeometry(*self.default_geometry(preset))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        win.round_corners(self)             # углы скругляет Windows 11 — окно остаётся непрозрачным
        win.no_activate(self)               # не забирать фокус даже по клику

    # ---------- данные
    def set_text_px(self, px: int) -> None:
        self.text_px = min(SIZES[-1], max(SIZES[0], px))
        self.update()

    def step_size(self, step: int) -> int:
        """Следующий размер из ряда 18/20/22/24/28."""
        nearest = min(range(len(SIZES)), key=lambda i: abs(SIZES[i] - self.text_px))
        self.set_text_px(SIZES[min(len(SIZES) - 1, max(0, nearest + step))])
        return self.text_px

    def add_line(self, line) -> None:
        self.lines.append(line)
        del self.lines[:-12]
        self.draft = ""
        self.update()

    def set_partial(self, speaker: str, text: str) -> None:
        self.partial = (speaker, text)
        if not text:
            self.draft = ""
        self.update()

    def set_draft(self, text: str) -> None:
        self.draft = text
        self.update()

    def set_status(self, text: str, level: str = "ok") -> None:
        self.status, self.level = text, level
        self.update()

    def set_running(self, running: bool) -> None:
        self.running = running
        self.btn_pause.setIcon(icons.icon("pause" if running else "play", self.c["text2"]))
        self.update()

    def flash(self, text: str, seconds: float = 8.0) -> None:
        """Временное сообщение (ошибка, подсказка) на месте строки состояния."""
        self.notice = text
        self._notice_timer.start(int(seconds * 1000))
        self.update()

    def _clear_notice(self) -> None:
        self.notice = ""
        self.update()

    def clear(self) -> None:
        self.lines, self.partial, self.draft = [], ("", ""), ""
        self.update()

    # ---------- отрисовка
    def blocks(self, width: float) -> list[tuple]:
        """Что рисовать, снизу вверх: [(строки, шрифт, цвет, высота строки, отступ под блоком)].
        Вынесено из paintEvent, чтобы содержимое можно было проверить тестом без экрана."""
        c, px = self.c, self.text_px
        prev_px, part_px = round(px * 16 / 22), round(px * 15 / 22)
        f_cur, f_prev, f_part = tokens.font(px, True), tokens.font(prev_px), tokens.font(part_px)
        f_small, f_name = tokens.font(12), tokens.font(12, True)
        lh_cur, lh_prev, lh_part = round(px * 30 / 22), round(prev_px * 22 / 16), round(part_px * 20 / 15)
        out = []

        level = {"ok": c["success"], "warn": c["warning"], "bad": c["error"]}.get(self.level, c["text3"])
        if self.notice:
            out.append((wrap(self.notice, f_small, width, 2), f_small, c["warning"], 16, 6))
        else:
            out.append(([self.status], f_small, c["text3"], 16, 6, level))      # level — цвет точки-индикатора

        speaker, text = self.partial
        last = self.lines[-1] if self.lines else None
        if text:                                                # немецкая фраза, которая ещё дописывается
            who = f"{speaker}: " if speaker and not (last and last.speaker == speaker) else ""
            out.append((wrap(f"{who}{text} …", f_part, width, 2, keep="end"), f_part, c["text3"], lh_part, 6))
        if self.draft:
            out.append((wrap(f"{self.draft} …", f_prev, width, 2), f_prev, c["text2"], lh_prev, 4))

        if last is None:
            if not text:
                out.append((["Перевод появится здесь."], f_prev, c["text3"], lh_prev, 8))
            return out
        if getattr(last, "hints", None):                        # термины из бестиария
            terms = "  ·  ".join(f"{de} — {ru}" for de, ru in last.hints)
            out.append((wrap(terms, tokens.font(13), width, 1), tokens.font(13), c["accent"], 18, 6))
        if last.translation:
            if self.show_original:
                out.append((wrap(last.text, f_part, width, 1), f_part, c["text3"], lh_part, 2))
            out.append((wrap(last.translation, f_cur, width, 3), f_cur, c["overlay_text"], lh_cur, 2))
        else:                                                   # перевода нет — показываем оригинал
            out.append((wrap(last.text, f_cur, width, 3), f_cur, c["fade1"], lh_cur, 2))
        if last.speaker:
            out.append(([last.speaker], f_name, c["accent"], 16, 10))

        newer = last
        for age, ln in enumerate(reversed(self.lines[:-1]), 1):  # история: чем старше, тем бледнее
            body = ln.translation or ln.text
            who = f"{ln.speaker}: " if ln.speaker and ln.speaker != newer.speaker else ""
            out.append((wrap(who + body, f_prev, width, 2 if age == 1 else 1), f_prev,
                        c["fade1"] if age == 1 else c["fade2"], lh_prev, 4))
            newer = ln
        return out

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        c = self.c
        p.fillRect(self.rect(), tokens.qcolor(c["overlay"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(tokens.qcolor(c["stroke"]), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), tokens.RADIUS_CARD, tokens.RADIUS_CARD)

        width, y = self.width() - 2 * PAD_X, float(self.height() - PAD_BOTTOM)
        for i, block in enumerate(self.blocks(width)):
            lines, font, color, lh, gap = block[:5]
            if not lines:
                continue
            if i > 0 and y - len(lines) * lh < PAD_TOP:
                break                                           # выше места нет — старое уехало за край
            metrics = QFontMetricsF(font)
            p.setFont(font)
            x = PAD_X
            for s in reversed(lines):
                y -= lh
                baseline = y + (lh - metrics.height()) / 2 + metrics.ascent()
                if len(block) > 5:                              # строка состояния: цветная точка
                    p.setPen(Qt.NoPen)
                    p.setBrush(tokens.qcolor(block[5]))
                    p.drawEllipse(QPointF(PAD_X + 4, y + lh / 2), 3.5, 3.5)
                    x = PAD_X + 14
                p.setPen(tokens.qcolor(color))
                p.drawText(QPointF(x, baseline), s)
            y -= gap
        p.end()

    # ---------- мышь
    def enterEvent(self, event) -> None:
        self.bar.show()
        self.bar.raise_()
        self.grip.show()

    def leaveEvent(self, event) -> None:
        self._hover_timer.start()

    def _check_hover(self) -> None:
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.bar.hide()
            self.grip.hide()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.globalPos())

    def moveEvent(self, event) -> None:
        self._save_timer.start()

    def resizeEvent(self, event) -> None:
        self._save_timer.start()
        self.bar.move(self.width() - self.bar.width() - 8, 8)
        self.grip.move(self.width() - 18, self.height() - 18)

    def _emit_geometry(self) -> None:
        g = self.geometry()
        self.geometry_changed.emit([g.x(), g.y(), g.width(), g.height()])

    def sizeHint(self) -> QSize:
        return QSize(900, 240)
