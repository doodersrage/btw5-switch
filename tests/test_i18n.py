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
