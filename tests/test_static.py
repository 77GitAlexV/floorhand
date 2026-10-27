"""Rules the web front-end keeps (static/README.md). No browser needed: these read
the files and the one server-rendered print page."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "floorhand" / "static"
SCREENS = STATIC / "js" / "screens"
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F900-\U0001F9FF]")


def _bundles(src: str):
    """Yield (en_keys, ru_keys) for every addStrings({ en: {...}, ru: {...} }) call."""
    for m in re.finditer(r"^addStrings\(\{(.*?)^\}\);", src, re.S | re.M):
        body = m.group(1)
        langs = {}
        for lm in re.finditer(r"\b(en|ru):\s*\{", body):
            depth, i = 1, lm.end()
            while depth and i < len(body):      # find the matching brace
                depth += {"{": 1, "}": -1}.get(body[i], 0)
                i += 1
            langs[lm.group(1)] = set(re.findall(r"(?:^|[{,]\s*)([A-Za-z_]\w*):\s", body[lm.end():i - 1]))
        yield langs.get("en", set()), langs.get("ru", set())


def test_every_string_exists_in_both_languages():
    files = [*(STATIC / "js").glob("*.js"), *SCREENS.glob("*.js")]
    checked = 0
    for f in files:
        for en, ru in _bundles(f.read_text(encoding="utf-8")):
            assert en and ru, f"{f.name}: a bundle is missing a language"
            assert en == ru, f"{f.name}: keys differ: {en ^ ru}"
            checked += 1
    assert checked >= 8


def test_every_screen_is_registered():
    index = (SCREENS / "index.js").read_text(encoding="utf-8")
    for f in SCREENS.glob("*.js"):
        if f.name == "index.js":
            continue
        assert f'"./{f.name}"' in index, f"{f.name} is not imported in screens/index.js"
        assert "registerScreen(" in f.read_text(encoding="utf-8")


def test_no_emoji_and_no_browser_dialogs():
    for f in STATIC.rglob("*"):
        if f.suffix not in (".js", ".html", ".css"):
            continue
        src = f.read_text(encoding="utf-8")
        assert not EMOJI.search(src), f"{f.name}: emoji are not icons here"
        if f.suffix == ".js":
            assert not re.search(r"\b(confirm|prompt|alert)\(", src), f"{f.name}: use sheet()/flash()"


def test_every_token_has_a_dark_value():
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    root = css.split("@media")[0]
    light = set(re.findall(r"(--[a-z][\w-]*):", root))
    colours = {t for t in light if re.search(rf"{re.escape(t)}:\s*#", root)}
    dark = set(re.findall(r"(--[a-z][\w-]*):", css.split('[data-theme="dark"]')[-1]))
    assert colours and colours <= dark, colours - dark


def test_index_loads_modules_and_answer_layer():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'type="module"' in html and "/static/js/app.js" in html
    for id_ in ("app", "sheet", "toasts", "answer"):
        assert f'id="{id_}"' in html


def test_delivery_note_prints_from_template(tmp_path, monkeypatch):
    import random
    from datetime import date

    from fastapi.testclient import TestClient

    from floorhand import db, domain, seed

    path = tmp_path / "t.db"
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    conn = db.connect(path)
    seed.build(conn, random.Random(3), today=date.today())
    sid = conn.execute("SELECT id FROM shipments LIMIT 1").fetchone()[0]
    inv = domain.invoice(conn, sid)
    office = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    conn.close()

    from floorhand.main import app
    c = TestClient(app)
    assert c.get(f"/invoice/{sid}").status_code == 401        # rule 7: no session, no page
    c.post("/api/login", json={"person_id": office})
    page = c.get(f"/invoice/{sid}").text
    assert "/static/print/print.css" in page
    assert f"№ {sid}" in page and inv["customer"] in page
    assert f"{inv['currency']}{inv['total']}" in page          # rule 6: one total, from one function
    assert "{" not in page.split("<script>")[0]                # every placeholder filled
