"""HTTP API and static files. Run: uvicorn floorhand.main:app --reload"""
import os
import secrets
from html import escape as esc
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, domain, features, intake
from .domain import DomainError
from .web import COOKIE, Session, get_conn, office, session, worker

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Floorhand")
for router in features.load():  # each feature module brings its own tables and routes
    app.include_router(router)
_conn = db.connect()
db.init(_conn)  # new tables appear in an existing demo database
_conn.close()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(DomainError)
def domain_error(_: Request, exc: DomainError) -> JSONResponse:
    # A refusal is an answer, not a crash: the screen shows it in red as-is.
    return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# --- sign-in (demo: pick a person; real PIN/QR sign-in comes later) -------

@app.get("/api/demo/people")
def demo_people(conn=Depends(get_conn)) -> list[dict]:
    rows = conn.execute("SELECT p.id, p.name, p.role, s.name_en AS stage, s.name_ru AS stage_ru "
                        "FROM people p LEFT JOIN stages s ON s.id = p.stage_id "
                        "ORDER BY p.role DESC, s.seq, p.name").fetchall()
    return [dict(r) for r in rows]


class LoginIn(BaseModel):
    person_id: int
    tablet: bool = False


@app.post("/api/login")
def login(body: LoginIn, response: Response, conn=Depends(get_conn)) -> dict:
    who = domain.person(conn, body.person_id)
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (token, person_id, tablet, created_at) VALUES (?, ?, ?, ?)",
                 (token, who["id"], int(body.tablet),
                  domain.now(conn).isoformat(timespec="seconds")))
    conn.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response, conn=Depends(get_conn)) -> dict:
    conn.execute("DELETE FROM sessions WHERE token = ?", (request.cookies.get(COOKIE),))
    conn.commit()
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(s: Session = Depends(session), conn=Depends(get_conn)) -> dict:
    who = domain.person(conn, s.person_id)
    shop = conn.execute("SELECT value FROM settings WHERE key = 'shop_name'").fetchone()
    return {"id": who["id"], "name": who["name"], "role": who["role"],
            "lang": who["lang"], "tablet": s.tablet, "stage_id": s.stage_id,
            "shop": shop["value"] if shop else "",
            "demo": os.environ.get("FLOORHAND_DEMO") == "1"}


# --- worker ---------------------------------------------------------------

@app.get("/api/work")
def work(s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return domain.worker_home(conn, s.person_id, show_money=not s.tablet)


class DoneIn(BaseModel):
    order_id: int | None = None
    unit_id: int | None = None
    idem_key: str | None = None
    method: str = "button"


@app.post("/api/work/done")
def work_done(body: DoneIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    if body.unit_id is None and body.order_id is None:
        raise DomainError("Say which item: order or item number.")
    unit_id = body.unit_id or domain.next_unit_for(conn, s.person_id, body.order_id)
    res = domain.mark_done(conn, s.person_id, unit_id, idem_key=body.idem_key,
                           method=body.method)
    out = res.__dict__.copy()
    if s.tablet:  # shared tablet never shows money
        out["amount"] = None
        out["message"] = out["message"].rsplit(",", 1)[0] + "." if not res.already \
            else out["message"]
    return out


class UndoIn(BaseModel):
    event_id: int


@app.post("/api/work/undo")
def work_undo(body: UndoIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return domain.undo(conn, s.person_id, body.event_id).__dict__


class TakeIn(BaseModel):
    order_id: int
    n: int = 1


@app.post("/api/work/take")
def work_take(body: TakeIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    n = domain.take(conn, s.person_id, body.order_id, body.n)
    return {"ok": True, "taken": n}


@app.get("/api/money")
def money(s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    if s.tablet:
        raise HTTPException(403, "Money is not shown on the shared tablet.")
    return domain.earnings(conn, s.person_id)


# --- office ---------------------------------------------------------------

@app.get("/api/floor")
def floor(s: Session = Depends(office), conn=Depends(get_conn)) -> list[dict]:
    return domain.floor_overview(conn)


@app.get("/api/orders")
def orders(q: str = "", view: str = "open", s: Session = Depends(office),
           conn=Depends(get_conn)) -> dict:
    return domain.orders_list(conn, q, view)


@app.get("/api/orders/{order_id}")
def order(order_id: int, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return domain.order_detail(conn, order_id)


class IntakeIn(BaseModel):
    text: str


@app.post("/api/intake")
def intake_propose(body: IntakeIn, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return intake.propose(conn, s.person_id, body.text)


class ApplyIn(BaseModel):
    picks: dict[str, dict[str, int | str]] = {}


@app.post("/api/intake/{action_id}/apply")
def intake_apply(action_id: int, body: ApplyIn, s: Session = Depends(office),
                 conn=Depends(get_conn)) -> dict:
    return intake.apply(conn, s.person_id, action_id, body.picks)


@app.get("/api/shipping")
def shipping(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return domain.shipping_board(conn)


@app.get("/api/shipping/{customer_id}")
def shipping_customer(customer_id: int, s: Session = Depends(office),
                      conn=Depends(get_conn)) -> dict:
    return domain.customer_shipping(conn, customer_id)


class ShipIn(BaseModel):
    customer_id: int
    unit_ids: list[int]
    payment: str
    paid_amount: int = 0


@app.post("/api/ship")
def ship(body: ShipIn, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    sid = domain.ship(conn, s.person_id, body.customer_id, body.unit_ids, body.payment,
                      body.paid_amount)
    inv = domain.invoice(conn, sid)
    return {"ok": True, "shipment_id": sid, "total": inv["total"],
            "message": f"Shipped {len(inv['lines'])} pc(s) to {inv['customer']}, "
                       f"{inv['currency']}{inv['total']}."}


@app.get("/invoice/{shipment_id}", response_class=HTMLResponse)
def invoice_page(shipment_id: int, s: Session = Depends(office),
                 conn=Depends(get_conn)) -> str:
    inv = domain.invoice(conn, shipment_id)
    cur = inv["currency"]
    rows = "".join(
        f"<tr><td>{esc(l['label'])}</td><td>{esc(l['product'])}<br><small>{esc(l['variant'])}"
        f"</small></td><td class=n>{cur}{l['price']}</td></tr>" for l in inv["lines"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Invoice {inv['id']}</title><style>
body{{font:16px/1.4 system-ui,sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#1d1d1b}}
table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #ccc;padding:8px;text-align:left}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}@media print{{button{{display:none}}}}
</style></head><body>
<h1>{esc(inv['shop'])} — delivery note №{inv['id']}</h1>
<p>{esc(inv['date'])} · To: <b>{esc(inv['customer'])}</b>, {esc(inv['city'] or '')}</p>
<table><tr><th>Item</th><th>Product</th><th class=n>Price</th></tr>{rows}
<tr><th colspan=2>Total</th><th class=n>{cur}{inv['total']}</th></tr>
<tr><td colspan=2>Paid</td><td class=n>{cur}{inv['paid']}</td></tr>
<tr><td colspan=2>Owed</td><td class=n>{cur}{inv['owed']}</td></tr></table>
<p><button onclick="print()">Print</button></p>
<script>addEventListener('load', () => print())</script></body></html>"""
