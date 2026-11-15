"""Общая папка с Capri и бестиарий."""
from dolmetsch import shared
from dolmetsch.capture.selector import Selector
from dolmetsch.glossary import Glossary, read_csv, variants


def test_shared_folders_are_created_once(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "documents_dir", lambda: tmp_path)
    root = shared.ensure()
    assert root == tmp_path / "Capri"
    assert all((root / name).is_dir() for name in ("Бестиарий", "Уроки", "Собеседования"))
    readme = root / "Прочти меня.txt"
    readme.write_text("своё", encoding="utf-8")
    shared.ensure()
    assert readme.read_text(encoding="utf-8") == "своё"         # повторный запуск ничего не затирает
    assert shared.meeting_dir("important") == root / "Собеседования" and shared.meeting_dir("lesson") == root / "Уроки"


def test_csv_with_header_semicolon_cp1251_and_without_header(tmp_path):
    a = tmp_path / "a.csv"
    a.write_bytes("Deutsch;Русский;Заметка\nFreigabe;общий доступ;сеть\n;пусто;\n".encode("cp1251"))
    assert read_csv(a) == [("Freigabe", "общий доступ")]
    b = tmp_path / "b.csv"
    b.write_text("Server,сервер\nClient,клиент\n", encoding="utf-8")
    assert read_csv(b) == [("Server", "сервер"), ("Client", "клиент")] and read_csv(b, need_header=True) == []


def test_variants():
    assert variants("Active Directory (AD)") == ["Active Directory", "AD"]
    assert variants("die Freigabe") == ["Freigabe"]


def test_hints_find_terms_in_phrase(tmp_path):
    folder = tmp_path / "Бестиарий"
    folder.mkdir()
    (folder / "it.csv").write_text(
        "Deutsch;Русский\nGruppenrichtlinie (GPO);групповая политика\ndie Freigabe;общий доступ\n"
        "Active Directory (AD);служба каталогов\nOrganisationseinheit (OU);подразделение\n", encoding="utf-8")
    (folder / "_пропущено.txt").write_text("x", encoding="utf-8")
    md = tmp_path / "Словарь.md"
    md.write_text("| Deutsch | Русский | Заметка |\n|---|---|---|\n| Subnetzmaske | маска подсети | |\n", encoding="utf-8")
    g = Glossary([folder])
    assert g.load() == 4
    assert g.hints("Die Freigaben im AD prüfen wir später.") == [("die Freigabe", "общий доступ"),
                                                                  ("Active Directory (AD)", "служба каталогов")]
    assert g.hints("Das Bad ist dort.") == []                    # «AD» внутри слова и другим регистром — не термин
    assert len(g.hints("GPO, OU, AD und Freigabe in einem Satz", limit=3)) == 3
    (folder / "neu.csv").write_text("Deutsch;Русский\nSubnetzmaske;маска подсети\n", encoding="utf-8")
    assert g.load() == 5                                         # новый файл подхватывается сам


def test_glossary_reads_shared_folder_and_capri_dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "documents_dir", lambda: tmp_path)
    shared.ensure()
    (shared.bestiary_dir() / "a.csv").write_text("Server;сервер\n", encoding="utf-8")       # в Бестиарии — любой CSV
    (shared.root() / "Bestiarium.csv").write_text("Deutsch;Русский\nDrucker;принтер\n", encoding="utf-8")
    (shared.root() / "usage.csv").write_text("a;b\n1;2\n", encoding="utf-8")                # не словарь
    (shared.lessons_dir() / "Словарь.md").write_text("| Deutsch | Русский |\n|---|---|\n| Treiber | драйвер |\n",
                                                     encoding="utf-8")
    g = Glossary()
    assert g.load() == 3 and [de for de, _ru, _p in g.terms] == ["Server", "Drucker", "Treiber"]


# ---------- режим источника
class Src:
    def __init__(self, name, status="no_window"):
        self.name, self.status, self.found, self.launched, self.title = name, status, False, 0, ""

    def find(self):
        return self.found

    def poll(self, now):
        return []

    def launch(self):
        self.launched += 1
        return True


def test_source_mode_windows_opens_captions_itself_and_ignores_teams():
    t, w, asked = Src("Teams"), Src("Субтитры Windows"), []
    t.found = True
    sel = Selector(t, w, on_event=lambda e: None, on_ask=lambda: asked.append(1), mode="windows")
    sel.step(0)
    assert w.launched == 1 and sel.active is None and not asked
    w.found = True
    sel.step(5)
    assert sel.active is w
    sel.step(60)
    assert sel.active is w and w.launched == 1                   # на Teams не переключается


def test_source_mode_teams_never_asks():
    t, w, asked = Src("Teams"), Src("Субтитры Windows"), []
    w.found = True
    sel = Selector(t, w, on_event=lambda e: None, on_ask=lambda: asked.append(1), mode="teams")
    for now in range(0, 120, 5):
        sel.step(now)
    assert sel.active is None and not asked and w.launched == 0


def test_second_launch_passes_command_to_running_program(qapp):
    """Второй запуск не открывает копию, а передаёт команду первой (так Capri включает перевод)."""
    from dolmetsch import ipc
    got = []
    assert not ipc.send("toggle", name="dolmetsch-test")          # никто не слушает
    server = ipc.listen(got.append, name="dolmetsch-test")
    assert ipc.send("toggle", name="dolmetsch-test") and ipc.send("rm -rf", name="dolmetsch-test")
    for _ in range(20):
        qapp.processEvents()
    server.close()
    assert got == ["toggle"]                                      # чужие строки не выполняются
