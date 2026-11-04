# Translating BT-W Control

The settings window and tray icon can be translated. (The `btwctl` command line output stays in
English, like most Linux command line tools.)

Translations are ordinary gettext `.po` files, so any `.po` editor works: [Poedit](https://poedit.net),
Lokalize, GNOME Translation Editor, or a plain text editor.

## Add a language

1. Copy the template `btwctl/locale/btwctl.pot` to `btwctl/locale/<code>.po`, where `<code>` is the
   language code: `de`, `fr`, `es`, `pt_BR`, `zh_CN`, …
2. Fill in the `msgstr ""` lines. Keep anything in braces, like `{headset}`, exactly as it is (it's
   replaced by a name or number), and keep the `…` ellipses.
3. Try it: `LANGUAGE=<code> python -m btwctl gui` from the source folder.
4. Optionally translate the app menu names too: add `Name[<code>]=…` and `Comment[<code>]=…` lines to
   `contrib/btwctl.desktop` and `contrib/btwctl-tray.desktop`.
5. Open a pull request with the file(s).

Untranslated messages simply show in English, so a partial translation is fine.

## Update a language after the program changed

Run `python tools/update-translations.py`. It refreshes the template and every `.po` file: existing
translations are kept, new messages are added empty, and messages that no longer exist are removed. It
prints how much of each language is translated.

## For developers

Mark user-visible text in `btwctl/window.py` and `btwctl/tray.py` with `tr("…")`. For values, use a
placeholder and `.format()`: `tr("Connected to {headset}.").format(headset=name)`, never an f-string,
so translators can move the value within the sentence. Then run `python tools/update-translations.py`;
CI fails if the template is out of date.
