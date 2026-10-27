"""Feature modules. Each file here may define:

    SCHEMA = "CREATE TABLE IF NOT EXISTS ..."   # its own tables
    router = APIRouter()                         # its own /api/... routes

They are picked up automatically, so a new feature never edits main.py or db.py.
Domain rules still live in plain functions; routes stay thin.
"""
import importlib
import pkgutil

from fastapi import APIRouter

from .. import db


def load() -> list[APIRouter]:
    routers = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if getattr(mod, "SCHEMA", None) and mod.SCHEMA not in db.EXTRA_SCHEMA:
            db.EXTRA_SCHEMA.append(mod.SCHEMA)
        if getattr(mod, "router", None) is not None:
            routers.append(mod.router)
    return routers


def seed_all(conn, rnd) -> None:
    """Let each feature add its demo data after the base workshop is built.
    A module opts in with `def seed(conn, rnd): ...`."""
    finalizers = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if callable(getattr(mod, "seed", None)):
            mod.seed(conn, rnd)
        if callable(getattr(mod, "seed_finalize", None)):
            finalizers.append(mod.seed_finalize)
    # Stored forecasts must see every feature's demo records before taking a snapshot.
    for finalize in finalizers:
        finalize(conn, rnd)
