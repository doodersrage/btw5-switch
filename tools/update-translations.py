#!/usr/bin/env python3
"""Collect the tr("...") messages into btwctl/locale/btwctl.pot and update every <lang>.po.

    python tools/update-translations.py           # update the files
    python tools/update-translations.py --check   # fail if the template is out of date (CI)

Existing translations are kept; new messages are added untranslated; messages
that no longer exist are dropped.
"""

import ast
import glob
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SOURCES = ["btwctl/window.py", "btwctl/tray.py"]
LOCALE = os.path.join(ROOT, "btwctl", "locale")
sys.path.insert(0, ROOT)
from btwctl.i18n import parse_po_entries  # noqa: E402


def extract():
    """[(msgid, ['file:line', ...])] in order of first appearance."""
    found = {}
    for rel in SOURCES:
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
    return sorted(found.items(), key=lambda kv: (kv[1][0].split(":")[0], int(kv[1][0].split(":")[1])))


def quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render(messages, translations=None, header=None):
    """translations: {msgid: (msgstr, fuzzy)}; fuzzy entries stay fuzzy (a translator's draft)."""
    out = [header or '''# Translations of BT-W Control (btwctl). See CONTRIBUTING-translations.md.
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
''']
    for msgid, refs in messages:
        out.append(f"#: {' '.join(refs)}")
        if "{" in msgid:
            out.append("#. Keep the {names} in braces as they are; they're filled in by the program.")
        msgstr, fuzzy = (translations or {}).get(msgid, ("", False))
        if fuzzy:
            out.append("#, fuzzy")
        out.append(f"msgid {quote(msgid)}")
        out.append(f"msgstr {quote(msgstr)}")
        out.append("")
    return "\n".join(out)


def main():
    check = "--check" in sys.argv
    messages = extract()
    pot = render(messages)
    pot_path = os.path.join(LOCALE, "btwctl.pot")
    if check:
        current = open(pot_path, encoding="utf-8").read() if os.path.exists(pot_path) else ""

        def messages_only(text):   # source line numbers (#: comments) may drift; that's fine
            return [line for line in text.splitlines() if not line.startswith("#:")]
        if messages_only(current) != messages_only(pot):
            sys.exit("btwctl/locale/btwctl.pot is out of date: run  python tools/update-translations.py")
        print(f"btwctl.pot up to date ({len(messages)} messages)")
        return
    os.makedirs(LOCALE, exist_ok=True)
    open(pot_path, "w", encoding="utf-8").write(pot)
    for po in sorted(glob.glob(os.path.join(LOCALE, "*.po"))):
        text = open(po, encoding="utf-8").read()
        header, entries = parse_po_entries(text)
        done = {e["msgid"]: (e["msgstr"], e["fuzzy"]) for e in entries if e["msgstr"]}
        open(po, "w", encoding="utf-8").write(render(messages, done, header or None))
        have = sum(1 for m, _ in messages if m in done and not done[m][1])
        print(f"{os.path.basename(po)}: {have}/{len(messages)} translated")
    print(f"btwctl.pot: {len(messages)} messages")


if __name__ == "__main__":
    main()
