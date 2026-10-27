"""Rules the web front-end keeps (static/README.md). No browser needed: these read
the files and the one server-rendered print page."""
import ast
import re
import shutil
import subprocess
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


def test_screen_interaction_contracts():
    """Real ES modules: stored decisions, uploads, roles, SSE reconnect and disposal."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional; no packages are installed for front-end checks")
    result = subprocess.run(
        [node, "--experimental-vm-modules", "tests/frontend_screens.mjs"],
        cwd=STATIC.parent.parent, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Front-end interaction contracts passed." in result.stdout


def test_live_motion_is_optional_and_phone_layout_is_present():
    css = (STATIC / "css" / "ui.css").read_text(encoding="utf-8")
    # All hero animations opt in to motion, including the decorative flow rail.
    start = css.index("@media (prefers-reduced-motion: no-preference)", css.index("/* Channels, shelf"))
    motion = css[start:css.index("@keyframes live-pulse", start)]
    for selector in (".flow-track i", ".queue-changed .live-queue b", ".live-ticker li:first-child"):
        assert selector in motion
    phone = css[css.index("@media (max-width: 700px)"):]
    assert ".shelf-layout, .live-bottom" in phone
    assert "overflow-x: auto" in phone  # all office navigation stays reachable
    live = (SCREENS / "live.js").read_text(encoding="utf-8")
    assert not re.search(r"\bmoney\(", live)


def test_route_audit_covers_every_declared_route():
    repo = STATIC.parent.parent
    notes = (repo / "NOTES-screens.md").read_text(encoding="utf-8")
    sources = [repo / "floorhand" / "main.py", *sorted((repo / "floorhand" / "features").glob("*.py"))]
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        prefix = ""
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                if getattr(stmt.value.func, "id", "") == "APIRouter":
                    prefix = next((ast.literal_eval(k.value) for k in stmt.value.keywords if k.arg == "prefix"), "")
        for stmt in tree.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in stmt.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr not in {"get", "post", "put", "delete", "patch"}:
                    continue
                route = prefix + ast.literal_eval(dec.args[0])
                assert f'`{dec.func.attr.upper()} {route}`' in notes, (source.name, route)


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
