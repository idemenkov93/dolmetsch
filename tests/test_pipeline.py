"""Конвейер, текст встречи, клавиши, окно, журнал."""
import datetime as dt

from dolmetsch.capture.base import Event
from dolmetsch.hotkeys import parse
from dolmetsch.pipeline import STALE, Pipeline
from dolmetsch.transcript import Transcript
from dolmetsch.translate.base import Result


class Router:
    def __init__(self):
        self.calls, self.last_error = [], ""
        self.offline = self

    def translate(self, texts, context=""):
        self.calls.append((list(texts), context))
        if texts and isinstance(texts[0], str) and context == "draft":
            return [f"ru:{t}" for t in texts]
        return [Result(f"ru:{t}", "офлайн", 5) for t in texts]


def pipe(**kw):
    out = {"lines": [], "partials": [], "drafts": []}
    clock = [100.0]
    p = Pipeline(kw.pop("router", Router()), on_line=out["lines"].append,
                 on_partial=lambda s, t: out["partials"].append((s, t)), on_draft=out["drafts"].append,
                 clock=lambda: clock[0], **kw)
    return p, out, clock


def test_finals_are_batched_and_partials_keep_only_the_latest():
    p, out, clock = pipe()
    for i in range(3):
        p.push(Event("final", "Anna", f"Satz {i}.", 100.0))
        p.push(Event("partial", "Anna", f"teil {i}", 100.0))
    clock[0] = 100.4
    p.step(0)
    assert p.router.calls == [(["Satz 0.", "Satz 1.", "Satz 2."], "")]
    assert [ln.translation for ln in out["lines"]] == ["ru:Satz 0.", "ru:Satz 1.", "ru:Satz 2."]
    assert out["lines"][0].ms == 400 and out["partials"] == [("Anna", "teil 2")]
    p.push(Event("final", "Anna", "Noch einer.", 100.4))
    p.step(0)
    assert p.router.calls[-1] == (["Noch einer."], "Satz 2.")       # предыдущая фраза — контекст


def test_stale_phrases_are_shown_without_translation():
    p, out, clock = pipe()
    p.push(Event("final", "Anna", "Zu alt.", 100.0))
    p.push(Event("final", "Anna", "Frisch.", 100.0 + STALE))
    clock[0] = 100.0 + STALE + 1
    p.step(0)
    assert [(ln.text, ln.translation) for ln in out["lines"]] == [("Zu alt.", ""), ("Frisch.", "ru:Frisch.")]
    assert p.stats["skipped"] == 1 and "пропущено 1" in p.summary()


def test_draft_translation_only_when_enabled():
    p, out, clock = pipe()
    p.push(Event("partial", "Anna", "wir fangen heute an", 100.0))
    p.step(0)
    assert out["drafts"] == []                       # ноутбук: недописанное не переводим
    p, out, clock = pipe(live_interval=1.5)
    p.push(Event("partial", "Anna", "wir fangen heute an", 100.0))
    p.step(0)
    assert len(out["drafts"]) == 1
    p.step(0)
    assert len(out["drafts"]) == 1                   # тот же текст второй раз не переводится


# ---------- текст встречи
def test_transcript_is_written_line_by_line_in_capri_format(tmp_path):
    times = iter([dt.datetime(2026, 11, 16, 9, 0, 0), dt.datetime(2026, 11, 16, 9, 0, 5),
                  dt.datetime(2026, 11, 16, 9, 1, 15)])
    t = Transcript(root=tmp_path, now=lambda: next(times))
    t.set_title('Netzwerk: DNS/DHCP?')
    t.add("Hans Müller", "Guten Morgen.", "Доброе утро.")
    path = tmp_path / "2026-11-16 0900 — Netzwerk DNS DHCP" / "транскрипт.md"
    assert path.exists() and "**[00:00:05] Hans Müller:** Guten Morgen.\n> Доброе утро.\n" in path.read_text(encoding="utf-8")
    t.add("", "Ohne Übersetzung.", "")
    text = path.read_text(encoding="utf-8")
    assert "**[00:01:15] Говорящий:** Ohne Übersetzung.\n\n" in text and text.startswith("# Транскрипт 16.11.2026 09:00")
    assert t.close() == path and t.path is None


def test_transcript_folder_depends_on_profile_and_survives_write_errors(tmp_path, monkeypatch):
    from dolmetsch import shared
    monkeypatch.setattr(shared, "documents_dir", lambda: tmp_path)
    t = Transcript()
    t.kind = "important"                             # собеседование → отдельная папка
    t.add("Anna", "Erzählen Sie etwas über sich.", "Расскажите о себе.")
    assert t.path.parent.parent == tmp_path / "Capri" / "Собеседования"
    t2 = Transcript()
    t2.add("Anna", "Hallo.", "Привет.")
    assert t2.path.parent.parent == tmp_path / "Capri" / "Уроки"
    blocked = tmp_path / "файл"
    blocked.write_text("x")
    t3 = Transcript(root=blocked)                    # папку создать нельзя — перевод не должен упасть
    t3.add("Anna", "Hallo.", "Привет.")
    assert t3.error and t3.lines == 0


def test_pipeline_writes_transcript(tmp_path):
    t = Transcript(root=tmp_path)
    p, out, clock = pipe(transcript=t)
    p.push(Event("final", "Anna", "Hallo zusammen.", 100.0))
    p.step(0)
    assert "ru:Hallo zusammen." in t.path.read_text(encoding="utf-8")


# ---------- клавиши
def test_hotkey_parsing():
    assert parse("Win+Alt+J") == (0x0008 | 0x0001, ord("J"))
    assert parse("ctrl+shift+f5") == (0x0002 | 0x0004, 0x74)
    assert parse("J") is None and parse("Win+Alt+Foo") is None and parse("") is None


# ---------- окно и строка состояния
def shown(overlay, width=900):
    """Весь текст, который окно нарисует, сверху вниз."""
    return "\n".join(" ".join(block[0]) for block in reversed(overlay.blocks(width)))


def test_overlay_shows_current_translation_history_partial_and_terms(qapp):
    from dolmetsch.pipeline import Line
    from dolmetsch.ui.overlay import Overlay
    o = Overlay()
    assert "Перевод появится здесь." in shown(o)
    for i in range(3):
        o.add_line(Line("Anna", f"Satz {i}.", f"Фраза {i}.", "офлайн", 300))
    o.add_line(Line("Ben", "Die GPO gilt für alle.", "Политика действует для всех.", "офлайн", 300,
                    hints=[("GPO", "групповая политика")]))
    o.set_partial("Ben", "und jetzt")
    text = shown(o)
    order = [text.index(x) for x in ("Фраза 1.", "Anna: Фраза 2.", "Ben", "Политика действует", "Die GPO gilt",
                                     "GPO — групповая политика", "und jetzt …")]
    assert order == sorted(order)                    # старое выше, свежее ниже, недописанное в самом низу
    o.show_original = False
    assert "Die GPO gilt" not in shown(o)
    o.add_line(Line("Ben", "Nicht übersetzt.", "", "", 0, "ошибка"))
    assert "Nicht übersetzt." in shown(o)            # перевода нет — показываем оригинал
    o.flash("Сообщение")
    assert "Сообщение" in shown(o)
    o.show()
    o.grab()                                         # отрисовка проходит без ошибок


def test_wrap_keeps_start_of_translation_and_end_of_unfinished_phrase(qapp):
    from dolmetsch.ui.family import tokens
    from dolmetsch.ui.overlay import wrap
    text = " ".join(f"слово{i}" for i in range(60))
    start = wrap(text, tokens.font(16), 300, 2)
    end = wrap(text, tokens.font(16), 300, 2, keep="end")
    assert len(start) == 2 and start[0].startswith("слово0") and start[-1].endswith("…")
    assert len(end) == 2 and end[-1].endswith("слово59") and end[0].startswith("…")
    assert wrap("коротко", tokens.font(16), 300, 2) == ["коротко"] and wrap("", tokens.font(16), 300, 2) == []


def test_text_size_steps(qapp):
    from dolmetsch.ui.overlay import Overlay
    o = Overlay(text_px=22)
    assert o.step_size(+1) == 24 and o.step_size(+1) == 28 and o.step_size(+1) == 28
    assert o.step_size(-1) == 24


def test_status_text():
    from dolmetsch.app import status_text
    assert status_text(False, "", "ok", "lesson", "", 0, "")[0] == "Пауза"
    assert status_text(True, "", "no_panel", "lesson", "", 0, "") == ("Teams найден, субтитры не включены", "warn")
    text, level = status_text(True, "Teams", "ok", "lesson", "офлайн", 420, "")
    assert text == "Teams · Урок · офлайн 0,4 с" and level == "ok"
    assert status_text(True, "Teams", "ok", "important", "офлайн", 900, "DeepL не ответил вовремя")[1] == "warn"


def make(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from dolmetsch import shared
    monkeypatch.setattr(shared, "documents_dir", lambda: tmp_path / "Документы")
    from dolmetsch.app import make_controller
    return make_controller()(use_hotkeys=False)


def test_controller_starts_and_stops_without_windows(qapp, tmp_path, monkeypatch):
    """Вся сборка целиком: создаётся, стартует, останавливается. Захвата нет (не Windows) — и это не ошибка."""
    from dolmetsch.pipeline import Line
    c = make(tmp_path, monkeypatch)
    assert (tmp_path / "Документы" / "Capri" / "Бестиарий").is_dir()         # общая папка создана
    bestiary = tmp_path / "Документы" / "Capri" / "Бестиарий" / "it.csv"
    bestiary.write_text("Deutsch;Русский\nGruppenrichtlinie (GPO);групповая политика\n", encoding="utf-8")
    c.tick()
    c.start()
    c.on_state("Teams", "ok")
    c.on_line(Line("Anna", "Die GPO gilt für alle.", "Политика действует для всех.", "офлайн", 350))
    assert c.overlay.lines[-1].hints == [("Gruppenrichtlinie (GPO)", "групповая политика")]
    assert c.overlay.status.startswith("Teams · ") and "0,3 с" in c.overlay.status
    c.on_hotkey("profile")
    c.on_hotkey("window")
    c.on_ask()
    assert c.askcard.isVisible()
    c.on_answer(False)
    c.overlay.size_step.emit(+1)
    assert c.cfg["text_px"] == 24
    c.stop()
    assert not c.running and "Пауза" in c.overlay.status
    assert (tmp_path / "Dolmetsch" / "config.json").exists()


def test_tray_menu_is_short_and_settings_apply_at_once(qapp, tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    top = [a.text().split("\t")[0] for a in c.menu.actions() if not a.isSeparator()]
    assert top == ["Показать / спрятать субтитры", "Начать перевод", "Профиль", "Настройки", "Выход"]
    assert c.menu.defaultAction().text().startswith("Показать")
    c.profile_actions["lesson"].trigger()
    assert c.cfg["profile"] == "lesson"

    c.open_settings()
    s = c.settings
    s.size.setCurrentIndex(s.size.findData(28))
    s.size.activated.emit(s.size.currentIndex())
    assert c.overlay.text_px == 28 and c.cfg["text_px"] == 28
    s.opacity.setValue(80)
    assert c.cfg["opacity"] == 0.8
    s.profile.setCurrentIndex(s.profile.findData("important"))
    s.profile.activated.emit(s.profile.currentIndex())
    assert c.router.profile == "important"
    s.h_to.setValue(15)
    assert c.router.lesson_hours["to"] == 15 and c.cfg["lesson_hours"]["to"] == 15
    s.keys["toggle"].setText("Ctrl+Shift+F9")
    s._apply_keys()
    assert c.cfg["hotkeys"]["toggle"] == "Ctrl+Shift+F9" and "сохранены" in s.keys_note.text()
    s.keys["toggle"].setText("просто J")
    s._apply_keys()
    assert "Не понял" in s.keys_note.text() and c.cfg["hotkeys"]["toggle"] == "Ctrl+Shift+F9"
    s.keys["toggle"].setText("Ctrl+Shift+F9")
    s.keys["window"].setText("Ctrl+Shift+F9")
    s._apply_keys()
    assert "разными" in s.keys_note.text()
    s.source.setCurrentIndex(s.source.findData("windows"))
    s.source.activated.emit(s.source.currentIndex())
    assert c.cfg["source"] == "windows"
    s.refresh()
    assert "терминов: 0" in s.terms_note.text()


def test_ask_card_closes_itself_as_no(qapp):
    from dolmetsch.ui.prompt import AskCard
    card, got = AskCard(), []
    card.answered.connect(got.append)
    card.ask(True)
    assert "Teams" in card.title.text() and card.isVisible()
    card.left = 1
    card._tick()
    assert got == [False] and not card.isVisible()
    card.ask(False)
    card.yes.click()
    assert got == [False, True]


def test_log_never_contains_meeting_text(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import dolmetsch.log as L
    for h in list(L.log.handlers):
        L.log.removeHandler(h)
    L.setup()
    secret = "GEHEIMER SATZ AUS DEM UNTERRICHT"
    p, out, clock = pipe()
    p.push(Event("final", "Frau Geheim", secret + ".", 100.0))
    p.step(0)
    L.log.info("стоп: %s", p.summary())
    for h in list(L.log.handlers):               # закрываем файл журнала, чтобы прочитать его целиком
        h.close()
        L.log.removeHandler(h)
    text = (tmp_path / "Dolmetsch" / "dolmetsch.log").read_text(encoding="utf-8")
    assert "фраз 1" in text and secret not in text and "Geheim" not in text


def test_crash_log_has_place_but_not_the_message(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import dolmetsch.log as L
    for h in list(L.log.handlers):
        L.log.removeHandler(h)
    L.setup()
    try:
        raise ValueError("ТЕКСТ ВСТРЕЧИ В ОШИБКЕ")
    except ValueError:
        L.crash("тест")
    for h in list(L.log.handlers):
        h.close()
        L.log.removeHandler(h)
    text = (tmp_path / "Dolmetsch" / "dolmetsch.log").read_text(encoding="utf-8")
    assert "ValueError" in text and "test_pipeline.py" in text and "ТЕКСТ ВСТРЕЧИ" not in text


def test_pipeline_thread_survives_a_bad_phrase(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import time

    class Bad(Router):
        def translate(self, texts, context=""):
            if "kaputt" in texts[0]:
                raise RuntimeError("сбой")
            return super().translate(texts, context)
    p, out, clock = pipe(router=Bad())
    p.start()
    p.push(Event("final", "Anna", "kaputt", 100.0))
    time.sleep(0.3)
    p.push(Event("final", "Anna", "Geht wieder.", 100.0))
    time.sleep(0.4)
    p.stop()
    p.join(2)
    assert [ln.text for ln in out["lines"]] == ["Geht wieder."]


def test_overlay_drag_and_resize_by_hand(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from dolmetsch.ui.overlay import Grip, Overlay
    o = Overlay(geometry=[100, 100, 600, 200])
    o.show()

    def ev(kind, x, y, buttons=Qt.LeftButton):
        return QMouseEvent(kind, QPointF(5, 5), QPointF(x, y), Qt.LeftButton, buttons, Qt.NoModifier)
    o.mousePressEvent(ev(QMouseEvent.MouseButtonPress, 110, 110))
    o.mouseMoveEvent(ev(QMouseEvent.MouseMove, 160, 140))
    o.mouseReleaseEvent(ev(QMouseEvent.MouseButtonRelease, 160, 140, Qt.NoButton))
    assert o.pos() == QPoint(150, 130)
    grip = o.findChild(Grip)
    grip.mousePressEvent(ev(QMouseEvent.MouseButtonPress, 700, 300))
    grip.mouseMoveEvent(ev(QMouseEvent.MouseMove, 760, 340))
    assert (o.width(), o.height()) == (660, 240)
    o.place("top")
    assert o.y() < 200
