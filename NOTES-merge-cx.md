# Merge integration / Integration notes

The working files are integrated on the cx/all worktree. The registered Git
metadata cannot be written from this sandbox. Recoverable merge commits are
preserved on cx/all in .merge-cx/repository inside this worktree.

## Registered-repository stop

Shared-repository step 1 stopped while finalizing cx/meta-client. Both git add
and git commit failed with:

    fatal: Unable to create 'E:/Work/Floorhand/floorhand/.git/worktrees/cx-all/index.lock': Permission denied

The registered cx/all ref and index remain at the starting commit 2358f31.
The prior run had already placed the resolved Meta merge and MERGE_HEAD in the
local staging repository. That merge was resumed there. No main ref, main
checkout, other worktree, or remote was modified. Nothing was pushed.

## Merge order and conflicts

All merges use git merge --no-ff --no-commit, followed by testing and an explicit
commit with the requested Claude co-author trailer.

| Branch | Result |
| --- | --- |
| cx/meta-client | Resumed models.py resolution; retain photo and marking APIs while using reserved-budget voice requests. |
| cx/deploy | Clean merge, including main.py demo flag; maintenance tests now account for feature seed stories. |
| cx/eval | Resolved intake.py after normalizing mixed line endings; retain multilingual extraction, source grounding, manual corrections, photo intake and learned corrections. |
| cx/shelf | Clean merge; add the SAM count adapter and fake-transport integration tests. |
| cx/channels | Clean merge. |
| cx/simulator | Clean merge; refresh its service each app lifespan so phone activity follows the currently configured database. |

requirements.txt keeps all existing dependencies. The shelf/simulator pytest.ini
settings are identical. The deploy ignore rule is retained. Integration scratch
files are ignored through .gitignore.

Harbor is used consistently in the new English/Russian aliases, corpora and
fixtures. A cancel-heading translation key was also renamed to meet the literal
case-insensitive naming check. Historical commits retain their original messages.

## Wiring

- meta.sam.count(conn, image, prompt) calls segment() through models.py, converts
  boxes to pixel xyxy lists, keeps source dimensions, derives photo dimensions
  locally for zero matches, and returns the count and conservative confidence.
  Missing scores yield zero confidence; inconsistent dimensions use the existing
  offline fallback. No stock changes occur until a stored proposal is confirmed.
- models.transcribe preserves marking's string contract while delegating to the
  validated, reserved-budget voice client. Its model attribution constant remains
  available. Photo intake retains its raw response contract and the new
  conservative opaque-image reservation.
- Simulator startup uses its discovered router lifespan. Its author explicitly
  documented that no extra main.py startup hook is needed. A regression found
  that the app retained a simulator bound to the previous database across
  lifespans; startup now creates a fresh service. The cross-feature reproduction
  and an explicit database-switch restart test pass.

## Validation

The supplied venv launcher fails because its configured base interpreter is
missing. No packages or environment files were changed. Validation uses:

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m pytest -q tests -p no:cacheprovider --basetemp=.merge-cx/tests
```

FACT: passing full-suite checkpoints:

| After merge | Passed | Skipped |
| --- | ---: | ---: |
| Meta client | 303 | 0 |
| Deploy | 323 | 0 |
| Eval | 408 | 0 |
| Shelf + SAM wiring | 492 | 1 |
| Channels + voice attribution | 541 | 1 |
| Simulator + lifecycle regression (final) | 574 | 1 |

FACT: the additional successful server-voice attribution regression passed
separately after the shelf checkpoint and is included in subsequent full suites.
The optional Pillow test is skipped because Pillow is not installed. The existing
Starlette/httpx deprecation warning remains. Model transports are faked; no paid
model calls were made. All demo data is invented.

## Seed and HTTP smoke

The requested FLOORHAND_DB=E:/Work/Floorhand/wt/cx-all.db is outside the writable
workspace. The exact fresh-seed command was attempted and failed at
sqlite3.connect with "unable to open database file". The same seed command then
succeeded with FLOORHAND_DB=E:/Work/Floorhand/wt/cx-all/.merge-cx/cx-all.db.

FACT: seed CLI summary: orders 200, units 306, events 1081, shipments 60.
The CLI's orders count describes the base generator; feature seeds also add
fictional demo stories.

Uvicorn was started on loopback port 8860 using the fallback Python and database.
FACT: /healthz returned 200 with ok=true, db=true, meta_live=false and budget left
USD 120. POST /api/login with person_id=1 returned 200. This seeded person is a
worker, so /api/channels/overview and /api/shelf/items correctly returned 403.
After signing in as the seeded manager (person_id=29), both endpoints returned
200 with valid JSON. Authorization was preserved. The task-owned server was
stopped after both smoke attempts; no other server was touched.

Evidence stays locally in .merge-cx/smoke-results.json and
.merge-cx/smoke-server.log. The repeatable smoke script is .merge-cx/smoke.py.
No live external models or messaging were used.

## Recovering the registered cx/all branch

The final local commits are exported in .merge-cx/cx-all.bundle. The working
files already contain the integration. The local resolved index and commits can
be inspected without writing the registered metadata:

```powershell
git --git-dir=.merge-cx/repository --work-tree=. log --first-parent --oneline
git --git-dir=.merge-cx/repository --work-tree=. status --short --branch
```

Outside the restricted sandbox, from this worktree, these commands import the
preserved history and update only cx/all and its index. They leave working files
in place. Stop if the branch or starting commit changed:

```powershell
if ((git branch --show-current) -ne 'cx/all') { throw 'Expected cx/all' }
if ((git rev-parse HEAD) -ne '2358f316dd3001157a3b8da15456b79652240cce') { throw 'Starting commit changed' }
git fetch .merge-cx/cx-all.bundle cx/all
if ($LASTEXITCODE -ne 0) { throw 'Bundle import failed' }
git reset --mixed FETCH_HEAD
```

The registered repository could not be staged because the same index lock is
blocked for git add as for git commit. All merge resolutions, staged content and
commit history are preserved in the local staging repository instead.


## Preserved merge commits

| Branch | Local merge commit |
| --- | --- |
| cx/meta-client | 89fcab2 |
| cx/deploy | f4fd35e |
| cx/eval | 1a9310a |
| cx/shelf | d4f6820 |
| cx/channels | 614a63d |
| cx/simulator | a50134c |

The separate voice-attribution fix is 0e0da9c. All these commits end with the
requested Claude co-author trailer. Every requested branch tip is an ancestor of
the preserved cx/all tip. The final case-insensitive naming check is empty in
both the registered worktree and the local index. No unresolved conflicts remain.

## Проверка и восстановление

ФАКТ: итоговая проверка — 574 теста пройдены, один необязательный тест Pillow пропущен.
Слияния и исправления сохранены в рабочих файлах и локальном репозитории.
Общий индекс Git недоступен для записи в этой среде. Для переноса истории в cx/all
используйте bundle и проверенные команды выше. Основная ветка и другие рабочие
деревья не изменялись; отправки в удалённый репозиторий не было.
