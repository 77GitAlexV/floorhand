"""Request plumbing shared by main.py and every feature module."""
from typing import Callable, Iterator

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from . import db

COOKIE = "fh_session"


def get_conn() -> Iterator:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


# Extra checks a feature can run on every signed-in request, e.g. the shared shop
# tablet signs its person out after a quiet spell. A check raises HTTPException
# to refuse. Signature: check(conn, token, request, row).
SESSION_CHECKS: list[Callable] = []


class Session(BaseModel):
    person_id: int
    role: str
    stage_id: int | None
    tablet: bool


def session(request: Request, conn=Depends(get_conn)) -> Session:
    token = request.cookies.get(COOKIE)
    row = token and conn.execute(
        "SELECT s.person_id, s.tablet, p.role, p.stage_id FROM sessions s "
        "JOIN people p ON p.id = s.person_id WHERE s.token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "Sign in first.")
    for check in SESSION_CHECKS:
        check(conn, token, request, row)
    return Session(person_id=row["person_id"], role=row["role"],
                   stage_id=row["stage_id"], tablet=bool(row["tablet"]))


def worker(s: Session = Depends(session)) -> Session:
    if s.stage_id is None:
        raise HTTPException(403, "This screen is for the shop floor.")
    return s


def office(s: Session = Depends(session)) -> Session:
    if s.role not in ("manager", "owner"):
        raise HTTPException(403, "This screen is for the office.")
    return s
