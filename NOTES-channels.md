# Sales channels / Каналы продаж

Built `floorhand/features/channels.py` and `tests/test_channels.py`. The feature loader
registers its schema, API router and demo seed automatically. No existing application
files or parallel-work feature files were changed. No packages were installed.

Owners and managers can preview stock/sales CSVs, confirm the stored diff, review
stock coverage and prepare make-to-stock cards. Yes creates an order; No records a
rejection. All action responses have English and Russian text. Forecast numbers use
`{value, tag}` with `FACT`, `CALC` or `ESTIMATE`; identifiers and ISO dates remain plain.

Владелец и менеджер могут проверить CSV остатков и продаж, подтвердить сохранённые
изменения, оценить запас и подготовить запуск на склад. Да создаёт заказ, Нет
сохраняет отказ. Цены доступны только владельцу вне общего планшета.

## Run

From this worktree, with a functioning project Python:

```powershell
$python = 'E:/Work/Floorhand/floorhand/.venv/Scripts/python'
& $python -m pytest -q
& $python -m floorhand.seed --fresh
& $python -m uvicorn floorhand.main:app --port 8027
```

`--fresh` resets the configured demo database; omit it when keeping existing work.
Fresh seeding creates three invented channels, nine listings and four weeks of
invented daily sales. Two product lines need replenishment. Existing populated
databases get the schema automatically, but do not get the demo rows unless seeded.

The supplied virtualenv launcher currently fails because its configured base
`C:/Users/muziny/AppData/Local/Programs/Python/Python313/python.exe` is missing.
Validation used the already-installed Codex Python runtime, appending the existing
project packages after its own compatible runtime packages. Nothing was installed
or repaired outside the worktree. The exact working test command is:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:/Users/muziny/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys; sys.path.append('E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); import pytest; raise SystemExit(pytest.main(['-q', '--basetemp=.pytest_cache/channels-tmp']))"
```

The same runtime/search-path workaround can run the demo without repairing the
virtualenv:

```powershell
& 'C:/Users/muziny/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys, runpy; sys.path.append('E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); sys.argv=['floorhand.seed', '--fresh']; runpy.run_module('floorhand.seed', run_name='__main__')"
& 'C:/Users/muziny/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys; sys.path.append('E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); import uvicorn; uvicorn.run('floorhand.main:app', port=8027)"
```

## API handoff

All routes require the existing office session cookie. Anonymous requests get 401;
workers get 403. Prices and currency are omitted on the server for managers and all
shared-tablet sessions, including stored import previews returned on retry.

| Route | Input and result |
| --- | --- |
| `GET /api/channels/overview?target_days=14` | Read-only grouped stock, sales, coverage, launch estimates, stage capacities, pending launch cards and recent import audit records. |
| `POST /api/channels/import` | Raw UTF-8 CSV body (`text/csv`), not JSON or multipart. Returns import `id`, `action_id`, hash, row count, `diff` counts and before/after `changes`. Only audit/proposal rows are written. |
| `POST /api/channels/import/{id}/apply` | Use the **import id**, no body required. Applies exactly the stored diff. |
| `POST /api/channels/suggest` | Optional JSON `{"target_days":14}`, default horizon fourteen calendar days; supported horizons one through ninety. Stores/reuses `make_to_stock` cards, creates no orders. |
| `POST /api/channels/proposals/{id}/apply` | Use the **launch card id**, no body required. Calls `domain.create_order(..., customer_id=None, source="channels", commit=False)`. Returns `order_id`. |
| `POST /api/channels/proposals/{id}/reject` | Use the **action id**. Rejects a launch card or an import's `action_id`, recording who decided and when. |

Confirmation ignores replacement fields sent by the client. Both writes and
decisions commit together under `BEGIN IMMEDIATE`; concurrent/repeated Yes creates
one order or one import application. Retrying a closed action returns its original
status/result and “Already recorded”. A No after Yes does not undo the action.

Import diff counts describe unique listing/sales records, not channels or physical
CSV lines. Channel additions appear separately in `changes`. An identical file
returns its original import card/status, including after rejection; it never opens
a second action. A changed file has a different hash, but uses the same row keys.
Stale confirmations refuse without applying any writes. Reject the stale action
and submit a fresh/corrected export or request new launch cards.

## CSV contract

Columns are matched by header, never position; delimiter detection handles comma,
semicolon, tab, pipe and other sniffable single-character delimiters. UTF-8 BOM,
quoted delimiter-containing cells, reordered columns and Russian header aliases
are supported. The hash is SHA-256 of the submitted UTF-8 bytes, including any BOM.

```csv
channel,kind,product,variant,sku,price,stock_at_channel,date,qty
Northmart,marketplace,Round pouf,velvet beige,DEMO-ROUND-BEIGE,137,3,2026-10-26,2
```

All example names, prices and quantities are invented demo data. For new channels,
`kind` is required (`marketplace`, `webshop`, `wholesale`). New SKUs need an exact
catalog `product` name or `product_id` and explicit stock, including zero. Optional
`price` uses whole shop currency units; an unknown price is NULL, not zero. For an
existing SKU, omitted price/stock/product/variant columns preserve their values.
Blank price also preserves it. A variant column with an empty value means the
empty variant, so omit that column when it should be preserved.

Required headers: `channel`, `sku`; each row supplies stock or both `date` and `qty`.
Stock-only exports and sales-only exports for existing SKUs work. The sale quantity
is the **daily total** for `(channel, sku, date)`, not a transaction delta. Corrected
totals replace the prior total. Listing keys are `(channel, sku)`. Identical repeated
rows collapse; conflicting stock snapshots or daily totals in one file refuse the
whole preview. Existing SKUs cannot be remapped to another product/variant because
that would rewrite the meaning of their historical sales. Use a new SKU instead.

Sales dates are ISO shop-calendar dates, never future dates. Counts/prices must be
non-negative whole numbers. Malformed, empty, oversized or ambiguous files refuse
with an explanation and do not leave partial imports. Limits: one megabyte and
five thousand physical data rows per preview.

## Planning assumptions and limits

- Grouping is exact product id plus variant text, across channels/SKUs. Shop stock
  includes only non-cancelled, unshipped, customer-less units for which
  `domain.current_stage` is finished. Readiness and shipping use domain functions,
  so undo removes a unit from ready stock. Customer work never counts as shop stock.
- Sales averages use the last seven/twenty-eight **completed calendar days** in
  `domain.today`'s shop timezone, excluding the partial current day. Missing days
  count as zero. The demand forecast is the larger average. No sales means unknown
  cover (`null`), not an infinite or zero-day stockout claim.
- Cover = (channel stock + shop-ready stock) / forecast daily demand. Required launch
  = ceiling(demand × horizon) minus channel stock, ready stock and to-stock work in
  progress, floored at zero. Projected cover on a launch card includes work in progress.
- Capacity reads every stage's configured units per workday, identifies the smallest
  capacity as the bottleneck, counts weekdays starting tomorrow through the horizon,
  and subtracts unfinished work at each stage (including customer work). The most
  constrained remaining stage caps total launch quantities. Low-cover products get
  priority; a single order is capped at the domain limit of five hundred units.
- Pending cards reserve capacity when suggesting more work. Repeated Suggest reuses
  pending cards, even from another office user or horizon. Reject old cards to change
  the plan. Confirmation rechecks demand, stock and capacity and applies the exact
  offered quantity or refuses; it never silently changes the offer. Overview's
  `make_next` is the current computed plan; `proposals` are the saved offers.
- This is a horizon-level estimate, not a production schedule. Weekends are excluded
  from production capacity; holidays, product-specific stage effort, lead times,
  future demand shifts and future customer orders are unknown. Those assumptions
  are also returned in English/Russian by the API.

## Missing / deliberately outside this task

No frontend/static changes: the parallel frontend work should render these API
cards and wire Yes/No to the IDs above. No live marketplace polling, stock transfers,
external sending or credential configuration. No new model calls: CSV grounding,
arithmetic and server decisions work fully offline, marked `offline-rules` in the
proposal audit. There is no package or network prerequisite for this feature.

Tests cover parsing, hash/row deduplication, corrected totals, invalid input, atomic
rollback, stale diffs/cards, concurrent confirmations, derived readiness, capacity
sharing, approved/rejected retries, role/tablet money cuts, HTTP body tampering and
the low-stock seed. Full-suite validation: FACT 72 tests passed; the only warning is
the existing Starlette/httpx deprecation.
