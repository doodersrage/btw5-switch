"""Translations for the window and tray.

Translations are standard gettext .po files in btwctl/locale/<language>.po
(e.g. de.po, pt_BR.po), read directly at runtime, so no compile step is needed
in any of the packages. Messages are marked with tr("..."); btwctl/locale/btwctl.pot is the template; see
CONTRIBUTING-translations.md. The command line output stays in English.

The language comes from LANGUAGE / LC_ALL / LC_MESSAGES / LANG, as usual.
"""

import ast
import os

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")


def parse_po(text):
    """{msgid: msgstr} from .po file text (no plurals or contexts; fuzzy entries skipped)."""
    out = {}
    entry = {}
    key = None
    fuzzy = False

    def flush():
        msgid, msgstr = entry.get("msgid"), entry.get("msgstr")
        if msgid and msgstr and not fuzzy:
            out[msgid] = msgstr

    for line in text.splitlines():
        line = line.strip()
        if not line:
            flush()
            entry, key, fuzzy = {}, None, False
            continue
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        if line.startswith("#"):
            continue
        for k in ("msgid", "msgstr"):
            if line.startswith(k + " "):
                key = k
                entry[k] = ast.literal_eval(line[len(k) + 1:])
                break
        else:
            if line.startswith('"') and key:
                entry[key] += ast.literal_eval(line)
    flush()
    return out


def languages(environ=os.environ):
    """Preferred languages, most preferred first, e.g. ['pt_BR', 'pt']."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(var)
        if value:
            langs = []
            for part in value.split(":"):
                lang = part.split(".")[0].split("@")[0]
                if lang and lang not in ("C", "POSIX"):
                    langs.append(lang)
                    if "_" in lang:
                        langs.append(lang.split("_")[0])
            return langs
    return []


def load(environ=os.environ, locale_dir=LOCALE_DIR):
    for lang in languages(environ):
        path = os.path.join(locale_dir, f"{lang}.po")
        try:
            with open(path, encoding="utf-8") as f:
                return parse_po(f.read())
        except OSError:
            continue
    return {}


_catalog = load()


def tr(text):
    """Translate a message (English if there's no translation)."""
    return _catalog.get(text, text)
