"""Запуск и связь модулей: источник субтитров → конвейер перевода → окно, значок в трее, горячие клавиши."""
from __future__ import annotations

import argparse
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import __version__, hardware
from .config import Config, app_dir, get_deepl_key, set_deepl_key
from .log import log, setup as setup_log
from .translate.deepl import DeepLTranslator
from .translate.opusmt import OpusMTTranslator, find_model
from .translate.router import PROFILE_NAMES, Router

HOW_TEAMS = "В звонке Teams: «…» (Weitere) → «Sprache und Sprachausgabe» → «Liveuntertitel anzeigen»."
HOW_WINDOWS = "Не получилось включить субтитры Windows. Нажми Win+Ctrl+L и выбери язык Deutsch."
STATUS = {
    "no_window": ("Ищу встречу…", "warn"),
    "no_panel": ("Teams найден, субтитры не включены", "warn"),
    "panel_empty": ("Субтитры Teams включены, жду речь", "ok"),
    "no_text": ("Субтитры Windows открыты, текста пока нет", "warn"),
    "lost": ("Субтитры пропали, ищу снова…", "warn"),
}


class Report:
    """Отчёт для служебных режимов (--smoke, --bench, --probe): в файл и, если есть консоль, на экран."""

    def __init__(self, path: str | None):
        self.path, self.lines = path, []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)
        if sys.stdout:
            try:
                print(line, flush=True)
            except Exception:  # noqa: BLE001
                pass
        if self.path:
            Path(self.path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def status_text(running: bool, source: str, status: str, profile: str, engine: str, ms: int, error: str) -> tuple[str, str]:
    """Строка состояния под переводом и её цвет."""
    if not running:
        return "Пауза", "warn"
    if status != "ok":
        return STATUS.get(status, ("Ищу встречу…", "warn"))
    parts = [source, PROFILE_NAMES.get(profile, profile)]
    if engine:
        parts.append(f"{engine} {ms / 1000:.1f} с".replace(".", ","))
    if error:
        parts.append(error)
    return " · ".join(parts), ("warn" if error else "ok")


def make_controller():
    """Собирает приложение. Qt подключается здесь, чтобы служебные режимы работали и без него."""
    from PySide6.QtCore import QObject, QTimer, QUrl, Signal
    from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from . import shared
    from .capture.selector import CaptureLoop, Selector
    from .capture.teams import TeamsSource
    from .capture.wincaptions import WindowsCaptionsSource
    from .glossary import Glossary
    from .hotkeys import Hotkeys, parse
    from .pipeline import Pipeline
    from .transcript import Transcript
    from .ui.family import icons, tokens, win
    from .ui.overlay import Overlay
    from .ui.prompt import AskCard
    from .ui.settings import Settings

    class Bridge(QObject):
        """Сигналы из фоновых потоков в окно (Qt сам переносит их в главный поток)."""
        line = Signal(object)
        partial = Signal(str, str)
        draft = Signal(str)
        state = Signal(str, str)
        ask = Signal()
        hotkey = Signal(str)
        notice = Signal(str)
        model = Signal(str)

    class Controller:
        def __init__(self, use_hotkeys: bool = True):
            self.cfg = Config()
            self.hw = hardware.detect()
            shared.ensure()
            self.offline = OpusMTTranslator(device=self.cfg["device"], threads=self.cfg["threads"])
            self.online = DeepLTranslator(get_deepl_key)
            self.router = Router(self.offline, self.online, self.cfg["profile"], self.cfg["lesson_hours"],
                                 state=self.cfg["deepl"])
            self.transcript = Transcript()
            self.glossary = Glossary()
            self.glossary.load()
            self.running = self.closed = False
            self.pipeline = self.capture = self.settings = None
            self.source, self.status, self.engine, self.ms = "", "no_window", "", 0
            self.use_hotkeys, self.hotkeys = use_hotkeys, None

            self.bridge = Bridge()
            self.overlay = Overlay(self.cfg["text_px"], self.cfg["opacity"], self.cfg["show_original"], self.cfg["window"])
            self.askcard = AskCard()
            b, o = self.bridge, self.overlay
            b.line.connect(self.on_line)
            b.partial.connect(o.set_partial)
            b.draft.connect(o.set_draft)
            b.state.connect(self.on_state)
            b.ask.connect(self.on_ask)
            b.hotkey.connect(self.on_hotkey)
            b.notice.connect(o.flash)
            b.model.connect(self.on_model)
            self.askcard.answered.connect(self.on_answer)
            self.askcard.how.connect(lambda: o.flash(HOW_TEAMS, 20))
            o.menu_requested.connect(lambda pos: self.menu.popup(pos))
            o.geometry_changed.connect(lambda g: self.cfg.set("window", g))
            o.pause_clicked.connect(self.toggle)
            o.settings_clicked.connect(self.open_settings)
            o.size_step.connect(lambda step: self.cfg.set("text_px", o.step_size(step)))
            o.hide_clicked.connect(self.toggle_window)

            self.menu = QMenu()
            self.build_menu(QAction, QActionGroup)
            self.tray = None
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray = QSystemTrayIcon(self.icon(False))
                self.tray.setContextMenu(self.menu)
                self.tray.activated.connect(lambda reason: self.toggle_window()
                                            if reason == QSystemTrayIcon.Trigger else None)
                self.tray.show()
            self.start_hotkeys()
            self.timer = QTimer(interval=30_000, timeout=self.tick)
            self.timer.start()
            self.refresh()
            threading.Thread(target=self.load_model, name="model", daemon=True).start()

        def icon(self, active: bool):
            return icons.app_icon("D", tokens.DARK["accent"], active, bars=True)

        # ---------- меню в трее: пять пунктов, остальное — в настройках
        def build_menu(self, QAction, QActionGroup) -> None:
            m, hk = self.menu, self.cfg["hotkeys"]
            show = m.addAction(f"Показать / спрятать субтитры\t{hk['window']}", self.toggle_window)
            m.setDefaultAction(show)                     # жирный пункт — он же левый клик по значку
            self.act_toggle = m.addAction("", self.toggle)
            prof = m.addMenu("Профиль")
            group = QActionGroup(prof)
            for key, title in (("auto", "Авто"), ("lesson", "Урок — офлайн"), ("important", "Важное — DeepL")):
                # Qt передаёт в обработчик флажок «отмечено» — поэтому у лямбды первый параметр checked
                act = prof.addAction(title, lambda checked=False, k=key: self.set_profile(k))
                act.setCheckable(True)
                act.setChecked(self.cfg["profile"] == key)
                group.addAction(act)
            self.profile_actions = dict(zip(("auto", "lesson", "important"), group.actions()))
            m.addAction("Настройки", self.open_settings)
            m.addAction("Выход", self.quit)

        # ---------- настройки
        def open_settings(self) -> None:
            if self.settings is None:
                self.settings = Settings(self.cfg, {
                    "profile": self.set_profile, "hours": self.set_hours, "key": self.save_key,
                    "restart": self.restart, "text_px": self.set_text_px, "opacity": self.set_opacity,
                    "redraw": self.redraw, "capture": self.apply_capture, "place": self.overlay.place,
                    "open": self.open, "hotkeys": self.set_hotkeys, "boot": self.set_boot,
                    "report": self.save_report, "info": self.info})
                self.settings.setWindowIcon(self.icon(True))
            self.settings.show()
            self.settings.raise_()
            self.settings.activateWindow()

        def info(self) -> dict:
            usage = ""
            if self.online.ready():
                usage = f"Через DeepL в этом месяце: {self.router.chars_this_month():,} символов.".replace(",", " ")
            return {"profile": self.router.profile, "has_key": self.online.ready(), "usage": usage,
                    "terms": self.glossary.load(), "boot": win.autostart_enabled("Dolmetsch")}

        def open(self, name) -> None:
            path = {"lessons": shared.lessons_dir, "interviews": shared.interviews_dir,
                    "bestiary": shared.bestiary_dir, "appdir": app_dir}[name]()
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def set_text_px(self, px: int) -> None:
            self.overlay.set_text_px(px)
            self.cfg.set("text_px", self.overlay.text_px)

        def set_opacity(self, value: float) -> None:
            self.overlay.setWindowOpacity(value)
            self.cfg.set("opacity", round(value, 2))

        def redraw(self) -> None:
            self.overlay.show_original = bool(self.cfg["show_original"])
            self.overlay.update()

        def apply_capture(self) -> None:
            for w in (self.overlay, self.askcard):
                win.hide_from_capture(w, bool(self.cfg["hide_from_capture"]))

        def set_hours(self, hours: dict) -> None:
            self.router.lesson_hours = hours
            self.refresh()

        def set_boot(self, on: bool) -> None:
            frozen = getattr(sys, "frozen", False)
            win.autostart("Dolmetsch", f'"{sys.executable}"' if on and frozen else None)

        # ---------- клавиши
        def start_hotkeys(self) -> list[str]:
            if not self.use_hotkeys:
                return []
            if self.hotkeys:
                self.hotkeys.stop()
                self.hotkeys.join(1)
            self.hotkeys = Hotkeys(self.cfg["hotkeys"], self.bridge.hotkey.emit)
            self.hotkeys.start()
            self.hotkeys.ready.wait(2)
            if self.hotkeys.failed:
                self.overlay.flash("Сочетания заняты другой программой: " + ", ".join(self.hotkeys.failed)
                                   + ". Поменяй их в настройках.", 20)
            return self.hotkeys.failed

        def set_hotkeys(self, combos: dict) -> str:
            bad = [c for c in combos.values() if parse(c) is None]
            if bad:
                return "Не понял: " + ", ".join(bad) + ". Пример: Win+Alt+J."
            if len(set(c.lower() for c in combos.values())) < len(combos):
                return "Сочетания должны быть разными."
            self.cfg.set("hotkeys", dict(combos))
            failed = self.start_hotkeys()
            self.refresh()
            return ("Заняты другой программой: " + ", ".join(failed)) if failed else "Клавиши сохранены."

        # ---------- профиль и ключ
        def set_profile(self, profile: str, save: bool = True) -> None:
            self.router.profile = profile
            if save:                         # из меню и настроек — запоминаем; с клавиши — только до перезапуска
                self.cfg.set("profile", profile)
            self.profile_actions[profile].setChecked(True)
            note = f"Профиль: «{PROFILE_NAMES[self.router.active_profile()]}»"
            if profile == "important" and not self.online.ready():
                note += ". Ключа DeepL нет — пока перевожу офлайн (Настройки → Ключ DeepL)."
            self.overlay.flash(note, 6)
            self.refresh()

        def save_key(self, key: str) -> str:
            if not set_deepl_key(key):
                return "Не получилось сохранить ключ в хранилище Windows."
            if not key.strip():
                return "Ключ DeepL удалён."
            try:
                used, limit = self.online.usage()
                self.router.state.pop("blocked_month", None)
                return f"Ключ принят. Использовано {used:,} из {limit:,} символов.".replace(",", " ")
            except Exception as e:  # noqa: BLE001
                return f"Ключ сохранён, но проверка не прошла: {e}"

        # ---------- старт и стоп
        def load_model(self) -> None:
            try:
                self.offline.load()
                log.info("модель: устройство %s, железо класса %s (видеопамять %.1f ГБ, потоков %d)",
                         self.offline.used_device, self.hw.cls, self.hw.vram_gb, self.hw.threads)
                error = ""
            except Exception as e:  # noqa: BLE001
                log.error("модель: %s", e)
                error = str(e)
            if not self.closed:              # программу могли закрыть, пока модель грузилась
                try:
                    self.bridge.model.emit(error)
                except RuntimeError:
                    pass

        def draft_interval(self):
            """Черновой перевод недописанной фразы — только когда модель работает на видеокарте."""
            setting = self.cfg["live_partial"]
            if self.offline.used_device == "cuda" or setting == "on":
                return hardware.live_interval(self.hw, setting)
            return None

        def on_model(self, error: str) -> None:
            if error:
                self.overlay.flash(f"Офлайн-перевод недоступен: {error}", 30)
            if self.pipeline is not None:
                self.pipeline.live_interval = self.draft_interval()
            self.refresh()

        def start(self) -> None:
            if self.running:
                return
            self.running = True
            self.source, self.status, self.engine = "", "no_window", ""
            self.transcript.kind = self.router.active_profile()      # «Урок» → Уроки, «Важное» → Собеседования
            self.pipeline = Pipeline(
                self.router, on_line=self.bridge.line.emit, on_partial=self.bridge.partial.emit,
                on_draft=self.bridge.draft.emit,
                transcript=self.transcript if self.cfg["save_transcript"] else None,
                live_interval=self.draft_interval())
            self.pipeline.start()
            mode = self.cfg["source"]

            def make_selector(auto):
                return Selector(TeamsSource(auto=auto), WindowsCaptionsSource(auto=auto),
                                on_event=self.pipeline.push, on_state=self.bridge.state.emit,
                                on_ask=self.bridge.ask.emit, mode=mode)

            self.capture = CaptureLoop(make_selector, on_error=lambda text: log.warning("%s", text))
            self.capture.start()
            log.info("старт: профиль %s, источник %s", self.router.active_profile(), mode)
            self.refresh()

        def stop(self) -> None:
            if not self.running:
                return
            self.running = False
            self.askcard.dismiss()
            for worker in (self.capture, self.pipeline):
                worker.stop()
            for worker in (self.capture, self.pipeline):
                worker.join(2)
            log.info("стоп: %s", self.pipeline.summary())
            self.log_reads()
            path = self.transcript.close()
            self.overlay.set_partial("", "")
            if path:
                self.overlay.flash(f"Текст встречи сохранён: {path.parent.parent.name}\\{path.parent.name}", 10)
            self.cfg.save()                  # счётчик символов DeepL
            self.refresh()

        def restart(self) -> None:
            """Применить новый источник субтитров, не закрывая программу."""
            if self.running:
                self.stop()
                self.start()

        def toggle(self) -> None:
            self.stop() if self.running else self.start()

        def on_command(self, command: str) -> None:
            """Команда от второго запуска программы (см. ipc.py) — например, от Capri."""
            if command == "show":
                self.overlay.show()
            elif command == "toggle":
                self.toggle()
            elif command == "start":
                self.start()
            elif command == "stop":
                self.stop()
            if command != "stop":
                self.overlay.show()

        def toggle_window(self) -> None:
            self.overlay.setVisible(not self.overlay.isVisible())

        # ---------- события
        def on_hotkey(self, action: str) -> None:
            if action == "toggle":
                self.toggle()
            elif action == "window":
                self.toggle_window()
            elif action == "profile":
                self.set_profile(self.router.toggle(), save=False)

        def on_line(self, line) -> None:
            self.engine, self.ms = line.engine or self.engine, line.ms
            if self.cfg["show_terms"]:
                line.hints = self.glossary.hints(line.text)
            self.overlay.add_line(line)
            if self.transcript.error:
                self.overlay.flash(self.transcript.error, 10)
            self.refresh(line.error if not line.translation else self.router.last_error)

        def on_state(self, source: str, status: str) -> None:
            self.source, self.status = source, status
            selector = self.capture.selector if self.capture else None
            if status == "ok":
                self.askcard.dismiss()
                if selector and source == "Teams":
                    self.transcript.set_title(selector.teams.title)
            log.info("источник: %s / %s", source or "-", status)
            self.refresh()

        def on_ask(self) -> None:
            selector = self.capture.selector if self.capture else None
            self.askcard.ask(bool(selector and selector.teams.status != "no_window"))
            self.apply_capture()

        def on_answer(self, yes: bool) -> None:
            selector = self.capture.selector if self.capture else None
            if selector is None:
                return
            if not selector.answer(yes):
                self.overlay.flash(HOW_WINDOWS, 20)
            elif yes:
                self.overlay.flash("Включаю субтитры Windows. Язык распознавания должен быть Deutsch.", 10)
            log.info("субтитры Windows: ответ %s", "да" if yes else "нет")

        def refresh(self, error: str = "") -> None:
            if not self.offline.ready():
                error = error or "нет офлайн-модели (папка models)"
            text, level = status_text(self.running, self.source, self.status, self.router.active_profile(),
                                      self.engine, self.ms, error)
            if not self.running:
                text = f"Пауза — {self.cfg['hotkeys']['toggle']}, чтобы начать"
            self.overlay.set_status(text, level)
            self.overlay.set_running(self.running)
            self.act_toggle.setText(("Пауза перевода" if self.running else "Начать перевод")
                                    + f"\t{self.cfg['hotkeys']['toggle']}")
            if self.tray:
                self.tray.setIcon(self.icon(self.running))
                self.tray.setToolTip(f"Dolmetsch — {text}"[:120])

        def log_reads(self) -> None:
            """В журнал: сколько миллисекунд занимает одно чтение субтитров (только числа)."""
            selector = self.capture.selector if self.capture else None
            source = selector.active if selector else None
            if source is None or not source.read_ms:
                return
            ms, source.read_ms = sorted(source.read_ms), []
            log.info("чтение %s: %d раз, медиана %.0f мс, 95 %% до %.0f мс", source.name, len(ms),
                     ms[len(ms) // 2], ms[min(len(ms) - 1, int(len(ms) * 0.95))])

        def tick(self) -> None:
            self.glossary.load()             # бестиарий перечитывается, если файлы в общей папке изменились
            if self.running:
                self.log_reads()
                self.refresh()

        # ---------- отчёт и выход
        def save_report(self) -> None:
            self.overlay.flash("Собираю отчёт: около минуты, не закрывай звонок…", 60)

            def work():
                try:
                    from .capture import uia
                    from .tools import uia_dump
                    auto = uia.load()
                    with auto.UIAutomationInitializerInThread():
                        args = argparse.Namespace(snapshots=3, pause=10, max_nodes=8000, max_depth=70,
                                                  show_text=False, also=["livecaptions.exe"])
                        dump = uia_dump.run(auto, args, app_dir(), log=lambda *a: None)
                    out = shared.documents_dir() / f"Dolmetsch-отчёт-{time.strftime('%Y%m%d-%H%M')}.zip"
                    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                        for f in [dump, app_dir() / "dolmetsch.log", app_dir() / "config.json"]:
                            if f and Path(f).exists():
                                z.write(f, Path(f).name)
                    self.bridge.notice.emit(f"Отчёт сохранён в «Документы»: {out.name}")
                except Exception as e:  # noqa: BLE001
                    log.error("отчёт: %s", e)
                    self.bridge.notice.emit(f"Отчёт не собрался: {e}")

            threading.Thread(target=work, name="report", daemon=True).start()

        def shutdown(self) -> None:
            """Остановить всё и убрать окна. Порядок важен: сначала потоки и таймеры, потом окна."""
            self.stop()
            self.closed = True
            self.timer.stop()
            if self.hotkeys:
                self.hotkeys.stop()
            self.cfg.save()
            if self.tray:
                self.tray.hide()
                self.tray.deleteLater()
            for w in (self.askcard, self.settings, self.overlay, self.menu):
                if w is not None:
                    w.close()
                    w.deleteLater()

        def quit(self) -> None:
            self.shutdown()
            QApplication.quit()

    return Controller


# ---------------------------------------------------------------- служебные режимы
def smoke(report) -> int:
    """Проверка сборки: все части подключаются, окно создаётся. Перевод не запускается."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if not sys.platform.startswith("win") else "windows")
    failed = 0
    for module in ["ctranslate2", "sentencepiece", "keyring", "PySide6.QtWidgets"] + \
            (["uiautomation"] if sys.platform.startswith("win") else []):
        try:
            __import__(module)
            report(f"OK   {module}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            report(f"FAIL {module}: {e}")
    report(f"{'OK  ' if find_model() else 'WARN'} офлайн-модель: {find_model() or 'не найдена'}")
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from .pipeline import Line
        from .ui.family import tokens
        app = QApplication.instance() or QApplication([])
        tokens.apply_app_style(app)
        controller = make_controller()(use_hotkeys=False)
        controller.overlay.show()
        controller.overlay.add_line(Line("Тест", "Guten Morgen zusammen.", "Всем доброе утро.", "офлайн", 120))
        controller.overlay.set_partial("Тест", "Wir fangen heute mit")
        controller.askcard.ask(True)
        controller.open_settings()
        QTimer.singleShot(1500, app.quit)
        app.exec()
        from .ui.family import icons
        image = icons.pixmap("settings", "#ffffff").toImage()
        drawn = any(image.pixelColor(x, y).alpha() > 0 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2))
        if not drawn:
            failed += 1
        report(f"{'OK  ' if drawn else 'FAIL'} значки (SVG) {'рисуются' if drawn else 'не рисуются'}")
        report(f"OK   окно, карточка, настройки; стиль {app.style().name()}")
    except Exception as e:  # noqa: BLE001
        failed += 1
        report(f"FAIL окно: {type(e).__name__}: {e}")
    report(f"Dolmetsch {__version__}: {'сборка в порядке' if not failed else 'ЕСТЬ ОШИБКИ'}")
    return 1 if failed else 0


def probe(report, seconds: float) -> int:
    """Проверка захвата без окна: что видит источник субтитров (только длины, без текста)."""
    from .capture.selector import CaptureLoop, Selector
    from .capture.teams import TeamsSource
    from .capture.wincaptions import WindowsCaptionsSource
    holder = {}

    def make(auto):
        holder["sel"] = Selector(
            TeamsSource(auto=auto), WindowsCaptionsSource(auto=auto),
            on_event=lambda e: report(f"{time.strftime('%H:%M:%S')} {e.kind:7} имя {len(e.speaker):2} симв., текст {len(e.text):3} симв."),
            on_state=lambda source, status: report(f"{time.strftime('%H:%M:%S')} источник: {source or '-'} / {status}"),
            on_ask=lambda: report("предложил бы включить субтитры Windows"))
        return holder["sel"]

    loop = CaptureLoop(make, on_error=report)
    loop.start()
    time.sleep(seconds)
    loop.stop()
    loop.join(3)
    source = holder.get("sel") and holder["sel"].active
    if source and source.read_ms:
        ms = sorted(source.read_ms)
        report(f"чтение {source.name}: {len(ms)} раз, медиана {ms[len(ms) // 2]:.0f} мс, худшее {ms[-1]:.0f} мс")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="Dolmetsch", description="Перевод субтитров встреч (DE → RU).")
    parser.add_argument("--smoke", action="store_true", help="проверить сборку и выйти")
    parser.add_argument("--bench", action="store_true", help="замерить скорость офлайн-перевода")
    parser.add_argument("--probe", type=float, metavar="СЕКУНД", help="проверить захват субтитров без окна")
    parser.add_argument("--out", metavar="ФАЙЛ", help="куда записать отчёт служебного режима")
    parser.add_argument("--toggle", action="store_true", help="старт / стоп перевода (если программа уже запущена — в ней)")
    parser.add_argument("--start", action="store_true", help="начать перевод")
    parser.add_argument("--stop", action="store_true", help="остановить перевод")
    parser.add_argument("--version", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:                   # у оконной программы нет консоли — не падаем на справке
        return int(e.code or 0)
    report = Report(args.out)
    if args.version:
        report(f"Dolmetsch {__version__}")
        return 0
    if args.smoke:
        return smoke(report)
    if args.bench:
        from . import bench
        return bench.run(report)
    if args.probe:
        return probe(report, args.probe)

    setup_log()
    from .log import crash
    sys.excepthook = lambda kind, value, tb: crash("окно", value)          # у оконной программы нет консоли —
    threading.excepthook = lambda a: crash(a.thread.name if a.thread else "поток", a.exc_value)   # сбои пишем в журнал
    from PySide6.QtWidgets import QApplication

    from . import ipc
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from .ui.family import tokens
    tokens.apply_app_style(app)
    command = "toggle" if args.toggle else "start" if args.start else "stop" if args.stop else "show"
    if ipc.send(command):                     # программа уже запущена — передали ей команду и вышли
        return 0
    if command == "stop":
        return 0
    log.info("=== Dolmetsch %s ===", __version__)
    controller = make_controller()()
    server = ipc.listen(controller.on_command)
    try:                                      # Capri находит программу по этому файлу
        if getattr(sys, "frozen", False):
            (app_dir() / "path.txt").write_text(sys.executable, encoding="utf-8")
    except OSError:
        pass
    controller.overlay.show()
    controller.apply_capture()
    if controller.cfg["autostart"] or command in ("toggle", "start"):
        controller.start()
    else:
        controller.refresh()
    code = app.exec()
    server.close()
    return code
