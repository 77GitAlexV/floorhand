"""Readiness without a session or a paid model call."""
import sqlite3
from contextlib import closing

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import db, models

router = APIRouter()


@router.get("/healthz")
def health() -> JSONResponse:
    # Read-only mode must fail rather than create a missing database.
    try:
        with closing(sqlite3.connect(db.db_path().resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=2)) as conn:
            left = max(0, models.budget_micro() - models.spent_micro(conn)) / 1e6
    except (sqlite3.Error, OSError, ValueError):
        return JSONResponse({"ok": False, "db": False, "meta_live": models.live(),
                             "budget_left_usd": 0.0,
                             "message": "Database or budget unavailable. Check server logs. / "
                                        "База или бюджет недоступны. Проверьте журнал сервера."},
                            status_code=503)
    return JSONResponse({"ok": True, "db": True, "meta_live": models.live(),
                         "budget_left_usd": left})
