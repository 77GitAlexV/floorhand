"""HTTP smoke for a disposable, invented demo DB on the already-running server.

Run: python tests/screens_smoke.py. Never point this at a real workshop.
"""
import json
import struct
import threading
import uuid
import zlib
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "http://127.0.0.1:8870"


def client():
    return build_opener(HTTPCookieProcessor(CookieJar()))


def call(opener, path, body=None, *, raw=None, content_type=None, expected=200):
    headers = {}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    try:
        with opener.open(Request(BASE + path, data=data, headers=headers), timeout=15) as response:
            status, payload = response.status, response.read()
    except HTTPError as exc:
        status, payload = exc.code, exc.read()
    assert status == expected, (path, status, payload[:500])
    return json.loads(payload)


def invented_photo():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = []
    for y in range(60):
        rows.append(b"\0" + bytes(0 if 10 <= y < 35 and any(a <= x < a + 16 for a in (10, 45, 80)) else 255 for x in range(120)))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 120, 60, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


def smoke():
    owner, manager, worker, guest = [client() for _ in range(4)]
    health = call(guest, "/healthz")
    assert health["ok"] and not health["meta_live"]
    people = call(guest, "/api/demo/people")
    office = next(p for p in people if p["role"] == "owner")
    desk = next(p for p in people if p["role"] == "manager")
    for session, person in ((owner, office), (manager, desk)):
        call(session, "/api/login", {"person_id": person["id"]})
    paths = ["/api/channels/overview", "/api/shelf/items", "/api/live/snapshot", "/api/sim/status", "/api/owner/outbox", "/api/paperwork/sheets/attention", "/api/marking/review", "/api/orders?view=open"]
    for path in paths:
        call(guest, path, expected=401)
        call(owner, path)
        call(manager, path)
    snap = call(owner, "/api/live/snapshot")
    assert snap["stages"] and snap["marks"] and snap["fact"] == "FACT"
    assert not any(k in json.dumps(snap) for k in ('"price"', '"amount"', '"paid"', '"currency"'))
    sim = call(owner, "/api/sim/status")
    assert sim["running"]
    call(owner, "/api/owner/today")
    call(manager, "/api/owner/today", expected=403)
    suggestions = call(owner, "/api/channels/suggest", {"target_days": 14})
    if suggestions["proposals"]:
        action = suggestions["proposals"][0]["id"]
        call(owner, f"/api/channels/proposals/{action}/reject", {})
    listing = call(owner, "/api/channels/overview")["items"][0]["listings"][0]
    csv = f'channel,sku,stock\n{listing["channel"]},{listing["sku"]},{listing["stock_at_channel"]["value"]}\n'.encode()
    preview = call(manager, "/api/channels/import", raw=csv, content_type="text/csv; charset=utf-8")
    if preview["status"] == "proposed":
        call(manager, f'/api/channels/proposals/{preview["action_id"]}/reject', {})
    item = call(manager, "/api/shelf/items")["items"][0]
    boundary = "floorhand-screens-smoke"
    fields = {"prompt": "demo separated parts", "item_id": str(item["id"]), "idem_key": uuid.uuid4().hex}
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="demo.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + invented_photo() + f'\r\n--{boundary}--\r\n'.encode())
    offer = call(manager, "/api/shelf/count", raw=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}")
    offer = call(manager, f'/api/shelf/{offer["id"]}')
    assert offer["count"] == 3 and offer["model"] == "offline-components" and len(offer["boxes"]) == 3
    assert call(manager, "/api/shelf/items")["items"][0]["qty"] == item["qty"]
    call(manager, f'/api/shelf/{offer["id"]}/apply', {"decision": "yes"})
    assert call(manager, "/api/shelf/items")["items"][0]["qty"] == 3

    ready, event = threading.Event(), []
    def listen():
        with owner.open(BASE + "/api/live", timeout=15) as response:
            assert response.headers["Content-Type"].startswith("text/event-stream")
            ready.set()
            for raw in response:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    data = json.loads(line[5:])
                    if data.get("stage"):
                        event.append(data)
                        return
    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    assert ready.wait(5)
    marked = False
    for person in (p for p in people if p["role"] == "worker"):
        call(worker, "/api/login", {"person_id": person["id"], "tablet": True})
        work = call(worker, "/api/work")
        available = work["mine"] + work["free"]
        if available:
            result = call(worker, "/api/marking/mark", {"order_id": available[0]["order_id"], "idem_key": uuid.uuid4().hex})
            assert result["ok"] and result.get("amount") is None
            marked = True
            break
    assert marked
    thread.join(10)
    assert not thread.is_alive() and event, "SSE did not publish a mark"
    assert all(k in event[0] for k in ("product", "stage_ru", "seq", "created_at"))
    for path in paths[:5]:
        call(worker, path, expected=403)
    result = {"fact": "FACT", "api_paths_checked": paths, "live_stages": len(snap["stages"]), "initial_ticker": len(snap["marks"]), "simulator_running": sim["running"], "shelf_count": offer["count"], "shelf_counter": offer["model"], "sse_mark_received": bool(event), "meta_live": False}
    dest = Path("data/screens-smoke.json")
    dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    smoke()
