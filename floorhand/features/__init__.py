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
