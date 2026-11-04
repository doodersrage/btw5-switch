import os
import subprocess
import sys

from btwctl import i18n

PO = r'''# a translator's header
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

#: btwctl/window.py:1
msgid "Audio"
msgstr "Áudio"

msgid "Connected to {headset}."
msgstr ""
"Conectado a "
"{headset}."

#, fuzzy
msgid "Disconnect"
msgstr "Desconectar?"

msgid "Untranslated"
msgstr ""

msgid "Line\nbreak \"quoted\""
msgstr "Linha\nquebra \"citada\""
'''


def test_parse_po():
    cat = i18n.parse_po(PO)
    assert cat == {"Audio": "Áudio", "Connected to {headset}.": "Conectado a {headset}.",
                   'Line\nbreak "quoted"': 'Linha\nquebra "citada"'}   # fuzzy and empty are skipped


def test_languages_from_environment():
    assert i18n.languages({"LANG": "pt_BR.UTF-8"}) == ["pt_BR", "pt"]
    assert i18n.languages({"LANGUAGE": "de:en", "LANG": "pt_BR.UTF-8"}) == ["de", "en"]
    assert i18n.languages({"LC_ALL": "C"}) == []
    assert i18n.languages({}) == []


def test_load_falls_back_from_region_to_language(tmp_path):
    (tmp_path / "pt.po").write_text(PO, encoding="utf-8")
    assert i18n.load({"LANG": "pt_BR.UTF-8"}, str(tmp_path))["Audio"] == "Áudio"
    assert i18n.load({"LANG": "fr_FR.UTF-8"}, str(tmp_path)) == {}


def test_untranslated_text_stays_english(monkeypatch):
    monkeypatch.setattr(i18n, "_catalog", {"Audio": "Áudio"})
    assert i18n.tr("Audio") == "Áudio"
    assert i18n.tr("Dongle") == "Dongle"


def test_shipped_translations_parse_and_keep_placeholders():
    for name in os.listdir(i18n.LOCALE_DIR):
        if name.endswith(".po"):
            cat = i18n.parse_po(open(os.path.join(i18n.LOCALE_DIR, name), encoding="utf-8").read())
            for msgid, msgstr in cat.items():
                import re
                assert set(re.findall(r"\{[^}]*\}", msgid)) == set(re.findall(r"\{[^}]*\}", msgstr)), \
                    f"{name}: placeholders differ in {msgid!r}"


def test_template_is_up_to_date():
    tool = os.path.join(os.path.dirname(__file__), "..", "tools", "update-translations.py")
    r = subprocess.run([sys.executable, tool, "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_window_uses_translations(monkeypatch, tmp_path):
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError:
        pytest.importorskip("PySide6.QtWidgets")
    from btwctl.qt import QtWidgets
    monkeypatch.setattr(i18n, "_catalog", {"Audio": "Áudio", "Paired headsets": "Fones pareados"})
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: __import__(
        "tests.fakes", fromlist=["FakeDongle"]).FakeDongle())
    monkeypatch.setattr("btwctl.service.is_active", lambda: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from btwctl.window import Window
    w = Window()
    try:
        assert w.audio.title() == "Áudio" and w.paired.title() == "Fones pareados"
        assert w.automation.title() == "Automation"     # no translation: English
    finally:
        w.shutdown()
        w.deleteLater()
        app.processEvents()


def run_tool(tmp_path, monkeypatch, po_text):
    """Run update-translations against a copy of the locale dir; return the updated file."""
    import importlib.util
    tool = os.path.join(os.path.dirname(__file__), "..", "tools", "update-translations.py")
    spec = importlib.util.spec_from_file_location("upd", tool)
    upd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upd)
    loc = tmp_path / "locale"
    loc.mkdir(parents=True)
    (loc / "xx.po").write_text(po_text, encoding="utf-8")
    monkeypatch.setattr(upd, "LOCALE", str(loc))
    monkeypatch.setattr(sys, "argv", ["update-translations.py"])
    upd.main()
    return (loc / "xx.po").read_text(encoding="utf-8")


def test_update_keeps_fuzzy_drafts(tmp_path, monkeypatch, capsys):
    out = run_tool(tmp_path, monkeypatch, 'msgid ""\nmsgstr ""\n"Language: xx\\n"\n\n'
                   '#, fuzzy\nmsgid "Audio"\nmsgstr "Draft"\n')
    header, entries = i18n.parse_po_entries(out)
    audio = next(e for e in entries if e["msgid"] == "Audio")
    assert audio["msgstr"] == "Draft" and audio["fuzzy"]
    assert "Language: xx" in header


def test_update_handles_files_without_locations(tmp_path, monkeypatch, capsys):
    po = 'msgid ""\nmsgstr ""\n"Language: xx\\n"\n\nmsgid "Audio"\nmsgstr "Áudio"\n'
    out = run_tool(tmp_path / "first", monkeypatch, po)
    again = run_tool(tmp_path / "second", monkeypatch, out)      # a second update of the result
    ids = [e["msgid"] for e in i18n.parse_po_entries(again)[1]]
    assert len(ids) == len(set(ids))                      # nothing duplicated
    assert i18n.parse_po(again)["Audio"] == "Áudio"
