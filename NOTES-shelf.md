# Shelf photo count / Подсчёт деталей по фото

## Built / Сделано

- `floorhand/features/shelf_count.py`: auto-loaded schema and office-only `/api/shelf` router; no changes to shared modules or frontend files.
- `floorhand/shelf_vision.py`: offline connected-component counter, boxes in original-image pixel coordinates, conservative confidence. Standard-library PNG decoder with CRC checks, bounded decompression, all PNG row filters, grayscale/RGB and alpha variants. No new packages.
- `agent_actions.kind = 'shelf_count'` stores the complete offered count, stock snapshot/revision, prompt, boxes, confidence, photo SHA-256 and bilingual messages. Counting never changes stock.
- Yes re-reads the stored proposal by ID and atomically updates `shelf_items`, appends `stock_counts`, and records the decision. No rejects without changing stock. Apply accepts only the decision: a client cannot substitute count, item or photo hash.
- `stock_counts` has who/when/item/before/after/photo hash, one adjustment per action, and append-only triggers. Times use `domain.now(conn)` and the shop timezone. Closed decisions are idempotent; stale snapshots are refused even if another adjustment returns the quantity to its old value.
- Optional `idem_key` is scoped to the proposing person and bound to image hash/item/prompt. Reusing it returns the same action; different input is refused. Concurrent retries create one proposal. Simultaneous *first* requests can both perform inference before the proposal is stored; use sequential retries to avoid duplicate model work. Confirmation always records once.
- Invented, bilingual stock seeds: demo armrests, seat frames, back cushions. Quantities are fictional. Responses label stock as FACT and inferred count/confidence/boxes as ESTIMATE.

Подсчёт создаёт только предложение. Остаток меняется после «Да» по сохранённому ID;
«Нет» не меняет запасы. Повторы не создают второй корректировки, устаревшие предложения
отклоняются. Все данные демонстрационные, сообщения доступны на английском и русском.

## Run / Запуск

From the worktree root, with a functioning project interpreter:

```powershell
$python = 'E:/Work/Floorhand/floorhand/.venv/Scripts/python'
& $python -m pytest -q
& $python -m floorhand.seed --fresh
& $python -m uvicorn floorhand.main:app --port 8017
```

`--fresh` recreates the demo database. Existing databases get the tables at startup, but
the demo stock items are inserted by the seed hook, not by counting requests.

The supplied venv launcher currently points to a missing base interpreter. A local
Python 3.13.13 bundled with Blender matches the existing dependencies. The following
tested workaround installs nothing, disables bytecode writes, and keeps test files
inside this worktree:

```powershell
New-Item -ItemType Directory -Path data -Force | Out-Null
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -c "import sys,runpy; sys.path.insert(0, r'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); sys.argv=['pytest','-q','--basetemp=data/shelf-test-tmp']; runpy.run_module('pytest',run_name='__main__')"
```

To seed or start the app with this same workaround, replace `sys.argv` and the module
passed to `runpy.run_module` with the corresponding command, e.g.
`sys.argv=['seed','--fresh']; runpy.run_module('floorhand.seed',run_name='__main__')`.
No packages were installed and no commits were made.

## API for the frontend / API для интерфейса

Every route requires an office session (`manager` or `owner`); use the existing login.

| Method | Route | Request / response |
| --- | --- | --- |
| GET | `/api/shelf/items` | Bilingual item names/units, stock quantity and revision; evidence FACT. |
| POST | `/api/shelf/count` | Multipart `image` file, UTF-8 `prompt`, `item_id`, optional `idem_key`. Returns a proposal. |
| GET | `/api/shelf/{id}` | Stored count and status; closed proposals have no choices and include the decision result. |
| POST | `/api/shelf/{id}/apply` | Exactly `{"decision":"yes"}` or `{"decision":"no"}`. No client count or item accepted. |

Raw-body upload is also supported: `Content-Type: image/png` or `image/jpeg`, with
`item_id`, `prompt`, optional `idem_key` in the query string. Multipart parsing uses
the standard-library MIME parser because `python-multipart` is absent.

Example from a signed-in frontend:

```javascript
const body = new FormData();
body.append('image', file);
body.append('prompt', 'armrest'); // or 'подлокотник'
body.append('item_id', String(selectedItem.id));
body.append('idem_key', crypto.randomUUID()); // reuse for this request's retries
const proposal = await fetch('/api/shelf/count', {method: 'POST', body}).then(r => r.json());
// Show message/message_ru and note/note_ru. Keep the uploaded photo locally.
// Draw boxes [x0,y0,x1,y1], upper bounds exclusive, using width/height for scaling.
// The local photo must use the same EXIF orientation as the displayed image.
const result = await fetch(`/api/shelf/${proposal.id}/apply`, {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({decision: 'yes'}) // 'no' for No / Нет
}).then(r => r.json());
```

Photos are not saved: only their hashes and detections are retained. For a reloadable
photo overlay, the frontend needs to retain the file or add a separate image store.

## SAM integration still required / Требуется подключение SAM

There is no SAM adapter in this checkout. `count_with_sam(conn, image, prompt)` is the
single swap point. It calls `models.check(conn, models.unit_cost_micro('sam-3.1', 1))`
*before* loading or invoking the adapter, so off/missing-key/exhausted budget raises
`models.BudgetStop` and cannot reach the provider.

Expected adapter contract, to agree with the parallel `floorhand/meta/sam.py` work:

```python
# floorhand.meta.sam
def count(conn, image: bytes, prompt: str) -> dict:
    # Actual paid transport, budget checks and model_calls accounting MUST go
    # through floorhand/models.py. This feature does not implement transport.
    # Return original, EXIF-oriented pixel coordinates; one box per detection.
    return {"boxes": [[10, 10, 26, 35]], "width": 120, "height": 60,
            "confidence": 0.9, "count": 1}  # count is optional; must equal len(boxes)
```

The current `models.py` has no segmentation transport helper. The parallel adapter
must add/use that budgeted entry point and log unit cost on success and conservative
cost on uncertain failures. This feature adds no guessed API endpoint or direct
network call. If the adapter exports another interface, change only `count_with_sam`.
Missing adapter, `BudgetStop`, malformed boxes/scores, and provider I/O failure fall
back offline. Only a valid adapter result is labeled SAM 3.1. Live SAM was tested
with a fake adapter/accounting, not with a paid request.

Платный SAM пока не подключён. Проверка бюджета стоит до вызова адаптера; отключённая
модель и отсутствие адаптера приводят к офлайн-подсчёту. Реальный запрос и учёт расходов
должны выполняться через `models.py`. Результаты офлайн не выдаются за ответ SAM.

## Offline limits / Ограничения офлайн-режима

Pillow, NumPy and python-multipart are absent from the supplied venv. No dependency
is required for the PNG path. Pillow is optional for JPEG, palette/interlaced PNG,
other image decoding and EXIF orientation. Without it, upload a non-interlaced,
8-bit grayscale/RGB/gray-alpha/RGBA PNG; palette, transparency-key, interlaced and
other bit-depth images receive a bilingual conversion request. JPEG gets the same
clear refusal when SAM and Pillow are unavailable.

Limits: CALC: 8 MiB upload, CALC: 4 megapixels decoded, CALC: 384 pixels on the
analysis grid's longest side. The algorithm estimates background from the image
border, thresholds grayscale contrast, rejects tiny specks and finds connected
regions. It counts all separated contrasting shapes; the text prompt does not
classify them. Touching/occluded parts can merge, shelves/shadows can be counted,
low-contrast parts can disappear, and narrow gaps can vanish on downsampling.
Confidence is a conservative, uncalibrated heuristic (ESTIMATE), never a measured
probability or reason to apply automatically. Check overlays before Yes.

Офлайн-счётчик считает отдельные контрастные области на однотонном фоне и не понимает
название детали. Соприкасающиеся детали, полки, тени и слабый контраст дают ошибки.
Уверенность — эвристическая оценка, а не вероятность. Всегда проверяйте рамки перед «Да».

## Materials merge / Объединение с материалами

`shelf_items` deliberately owns only semi-finished demo stock. It does not read or
write the parallel materials tables. Later add a stable `material_id` mapping,
agree integer piece units and bilingual names, and move the confirmed adjustment
into the materials domain transaction. Preserve the stored proposal ID, snapshot
revision, photo hash, append-only audit and exactly-once adjustment. All other
stock writers must increment the same revision, including quantity-neutral changes.
Do not sync by translated display name or make inference write either inventory.

## Validation / Проверка

Tests cover synthetic PNGs and every supported filter/color mode; optional Pillow
image generation; boxes/downsampling/transparency/noise/touching parts; bounded and
invalid uploads; API authentication/office access; UTF-8 multipart and raw uploads;
proposal-only writes; stored-ID confirmation; refused overrides; No; idempotency;
parallel retries/confirmations; stale snapshots/quantity returning to its old value;
atomic rollback and append-only audit; budget off/exhausted; absent/malformed SAM;
and a fake successful SAM adapter with model-call accounting.

FACT: Full suite: **102 passed, 1 skipped**, with the existing Starlette/httpx
deprecation warning. The skipped test generates an image with optional Pillow,
which is absent; the standard-library synthetic-image tests all ran. Verification
used the local Python workaround above. Live SAM/provider requests were not run.
