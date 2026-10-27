# Floorhand

A shop-floor system for small furniture workshops: orders, work by stage, piece-rate pay
and shipping — with Meta's models doing the paperwork nobody wants to do.

Built from scratch for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026),
Indie Developer track. Every line in this repository was written during the hackathon;
see the commit history.

## Why

It comes from running a real upholstered-furniture workshop. In its old system people used
6–7 screens out of 30: half of all screen opens were workers on their phones, and the second
screen they opened was "how much did I earn today". Reports, settings and directories were
almost never opened, and of 9,233 notifications about 16 were read.

So Floorhand is those few screens done well, plus an agent instead of everything else:

- **Worker phone** — one big "+ DONE" button on the work handed to you, money for today on the
  same screen, scan / voice / photo as other ways to say the same thing.
- **Office** — orders from any text, table, photo or voice note; shipping in three taps.
- **Owner** — a feed of decisions: "Upholstery is the bottleneck, here is tomorrow's plan —
  Yes / Change / No / Why?".

## How Meta's models are used (in progress)

| What | Model |
|---|---|
| Orders from e-mails, chat screenshots, messy spreadsheets — every field quoted from the source | Muse Spark |
| Daily plan by stage capacity, late orders and material shortages ahead of time | Muse Spark (tools) |
| "Two hundred fourteen is done" on the shop floor | Muse Voice Transcribe |
| Counting parts on a shelf from a photo | SAM 3.1 |

The model never writes to the database: it proposes a card, a person presses **Yes**, and the
server applies the stored proposal by id.

## Rules the code keeps (each one has a test)

- Work records are append-only (enforced by SQLite triggers). Undo is a visible record.
- Marking the same item twice never undoes it — "Already recorded".
- A retried tap with the same key records once (offline queue).
- An item cannot skip a stage. Order status is derived, never set by hand.
- "No rate" is not $0. Money follows the date of the work.
- Only finished items can be shipped, each one once. One function computes a shipment total.
- `/api` without a session is 401; the shared shop tablet never shows money.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m floorhand.seed --fresh       # fictional workshop, two weeks of history
.venv/bin/python -m uvicorn floorhand.main:app --port 8811
.venv/bin/python -m pytest -q
```

Open http://localhost:8811 and pick a person. All people, customers and numbers in the demo
are made up.
