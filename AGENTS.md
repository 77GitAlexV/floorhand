# Working on Floorhand (for humans and coding agents)

Floorhand is a shop-floor system for small furniture workshops, built for the Meta Global AI
Developer Hackathon (Oct 26 – Nov 4, 2026). Everything here is new work; all demo data is made up.
Never add real company names, people, prices or data from any real factory.

## Layout
- `floorhand/db.py` – core schema. `floorhand/domain.py` – core rules (marking work, orders, shipping).
- `floorhand/features/<name>.py` – one module per feature. It may define `SCHEMA` (its own
  `CREATE TABLE IF NOT EXISTS ...`), `router = APIRouter()` (routes under `/api/<name>/...`) and
  `seed(conn, rnd)` (demo data). They load automatically: do not edit `main.py`/`db.py` for a feature.
- `floorhand/web.py` – `get_conn`, `session`, `worker`, `office` dependencies for routes.
- `floorhand/models.py` – the ONLY way to call Meta models. It has a hard budget and is off unless
  `FLOORHAND_META_LIVE=1`. Every model feature must have an offline path that works without it.
- `floorhand/intake.py` – orders from text/tables with quotes.
- `floorhand/static/` – the web app (vanilla JS, no build step).
- Tests: `tests/`, run `python -m pytest -q`. Demo: `python -m floorhand.seed --fresh`, then
  `python -m uvicorn floorhand.main:app --port <port>`.

## Rules every change keeps (each has a test; add tests for new rules)
1. A model never writes to the database. It proposes (`agent_actions` row); a person confirms;
   the server applies the stored proposal by id, accepting only what was offered.
2. `work_events` is append-only (triggers). Undo / rework are new visible records.
3. Repeating an action never undoes it ("Already recorded"). Retries with the same key record once.
4. Order status is derived from units, events and shipments – never set by hand.
5. "No rate" is not 0. Money follows the date of the work. Paid work is frozen by a payout link.
6. Only finished units ship, each once; one function computes a shipment total.
7. Money is cut on the server by role; the shared shop tablet never shows money; `/api` without a
   session is 401.
8. Anything sent outside (e-mail, marketplaces) is dry-run by default: it goes to an outbox the
   user can see; real sending only when configured.
9. Every action answers in words: what was done, or why not and what to do.
10. Explicit shop time zone (`domain.now/today`). Answers with numbers mark them FACT / CALC / ESTIMATE.

## Style
Match the surrounding code: small functions, plain SQL, short docstrings that say why.
User-facing text in English and Russian. Commit with clear messages; keep tests green.
