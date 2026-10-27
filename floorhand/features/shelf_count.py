"""A shelf photo offers a stock count; only a person's Yes changes stock."""
import hashlib
import importlib
import json
import math
from email import policy
from email.parser import BytesParser

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from .. import domain, models
from ..domain import DomainError
from ..shelf_vision import MAX_BYTES, MAX_PIXELS, PNG, count_offline
from ..web import Session, get_conn, office

SCHEMA = """
CREATE TABLE IF NOT EXISTS shelf_items (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    name_ru  TEXT NOT NULL,
    unit     TEXT NOT NULL DEFAULT 'pc',
    unit_ru  TEXT NOT NULL DEFAULT 'шт.',
    qty      INTEGER NOT NULL CHECK (qty >= 0),
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stock_counts (
    id         INTEGER PRIMARY KEY,
    action_id  INTEGER NOT NULL UNIQUE REFERENCES agent_actions(id),
    person_id  INTEGER NOT NULL REFERENCES people(id),
    created_at TEXT NOT NULL,
    item_id    INTEGER NOT NULL REFERENCES shelf_items(id),
    before_qty INTEGER NOT NULL CHECK (before_qty >= 0),
    after_qty  INTEGER NOT NULL CHECK (after_qty >= 0),
    photo_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS stock_counts_no_update
BEFORE UPDATE ON stock_counts
BEGIN SELECT RAISE(ABORT, 'stock_counts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS stock_counts_no_delete
BEFORE DELETE ON stock_counts
BEGIN SELECT RAISE(ABORT, 'stock_counts is append-only'); END;
CREATE TABLE IF NOT EXISTS shelf_count_requests (
    person_id    INTEGER NOT NULL REFERENCES people(id),
    idem_key     TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    action_id    INTEGER NOT NULL UNIQUE REFERENCES agent_actions(id),
    PRIMARY KEY (person_id, idem_key)
);
"""

router = APIRouter(prefix="/api/shelf", tags=["shelf"])


def seed(conn, rnd):
    """Fictional semi-finished stock, independent of materials until integration."""
    conn.executemany(
        "INSERT OR IGNORE INTO shelf_items (name, name_ru, qty) VALUES (?, ?, ?)",
        [("Demo armrests", "Демо-подлокотники", 25),
         ("Demo seat frames", "Демо-каркасы сидений", 14),
         ("Demo back cushions", "Демо-подушки спинки", 18)])


def _office(conn, person_id):
    if domain.person(conn, person_id)["role"] not in ("manager", "owner"):
        raise DomainError("Only the office can count stock. / Считать запасы может только офис.")


def _item(conn, item_id):
    row = conn.execute("SELECT * FROM shelf_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise DomainError("Choose an existing shelf item. / Выберите существующую складскую позицию.")
    return dict(row)


def _sam_result(result):
    """Reject malformed detections rather than storing an unsafe stock offer."""
    width, height = result["width"], result["height"]
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or width * height > MAX_PIXELS):
        raise ValueError("Invalid image dimensions")
    boxes, confidence = result["boxes"], result["confidence"]
    if not isinstance(boxes, list) or len(boxes) > 4096:
        raise ValueError("Invalid boxes")
    if (type(confidence) not in (int, float) or not math.isfinite(confidence)
            or not 0 <= confidence <= 1):
        raise ValueError("Invalid confidence")
    for box in boxes:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
            raise ValueError("Invalid box")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError("Box outside photo")
    if "count" in result and (type(result["count"]) is not int or result["count"] != len(boxes)):
        raise ValueError("Count disagrees with boxes")
    return {"count": len(boxes), "boxes": [list(b) for b in boxes], "width": width,
            "height": height, "confidence": float(confidence), "confidence_kind": "model-score"}


def count_with_sam(conn, image: bytes, prompt: str) -> dict:
    """Adapter seam: sam.count(conn, image, prompt), with transport/logging in models.py.

    The forthcoming adapter returns pixel xyxy boxes, oriented width/height and
    confidence. It must use models.py for its paid request and accounting. The
    guard here also prevents importing/calling an adapter when off or exhausted.
    """
    models.check(conn, models.unit_cost_micro("sam-3.1", 1))
    try:
        sam = importlib.import_module("floorhand.meta.sam")
        counter = getattr(sam, "count", None)
        if not callable(counter):
            raise models.BudgetStop("SAM adapter is not connected; using the offline counter.")
        return _sam_result(counter(conn, image, prompt))
    except ImportError as exc:
        raise models.BudgetStop("SAM adapter is not installed; using the offline counter.") from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise models.BudgetStop("SAM could not count this photo; using the offline counter.") from exc


def _action(conn, action_id):
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = 'shelf_count'",
                       (action_id,)).fetchone()
    if row is None:
        raise DomainError("No shelf proposal found. Count the photo first. / "
                          "Предложение по запасам не найдено. Сначала посчитайте детали на фото.")
    return row


def _reply(row, already=False):
    reply = {"ok": True, "id": row["id"], "model": row["model"], "status": row["status"],
             "already": already, **json.loads(row["payload"])}
    if row["status"] != "proposed":
        result = json.loads(row["result"])
        reply.update(result=result, choices=[], message=result["message"], message_ru=result["message_ru"])
    return reply


def _request_hit(conn, person_id, key, fingerprint):
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM shelf_count_requests WHERE person_id = ? AND idem_key = ?",
        (person_id, key)).fetchone()
    if row is None:
        return None
    if row["request_hash"] != fingerprint:
        raise DomainError("This retry key belongs to another photo or item. Use a new key. / "
                          "Ключ повтора относится к другому фото или позиции. Используйте новый ключ.")
    return _reply(_action(conn, row["action_id"]), already=True)


def propose(conn, person_id: int, image: bytes, prompt: str, item_id: int,
            idem_key: str | None = None) -> dict:
    _office(conn, person_id)
    prompt = prompt.strip()
    if not prompt or len(prompt) > 200:
        raise DomainError("Describe the part briefly, for example 'armrest'. / "
                          "Кратко опишите деталь, например «подлокотник».")
    if idem_key is not None and (not idem_key.strip() or len(idem_key) > 120):
        raise DomainError("Use a short, nonempty retry key. / Используйте короткий непустой ключ повтора.")
    if not image or len(image) > MAX_BYTES:
        raise DomainError("Upload a photo up to CALC: 8 MiB. / Загрузите фото размером до CALC: 8 МиБ.")
    if not image.startswith(PNG) and not image.startswith(b"\xff\xd8"):
        raise DomainError("Upload a PNG or JPEG photo. / Загрузите фото в формате PNG или JPEG.")
    item = _item(conn, item_id)
    photo_hash = hashlib.sha256(image).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([item_id, prompt, photo_hash]).encode()).hexdigest()
    previous = _request_hit(conn, person_id, idem_key, fingerprint)
    if previous:
        return previous
    try:
        counted, model = count_with_sam(conn, image, prompt), "sam-3.1"
        label = "SAM 3.1"
        note = "Check every overlay box before confirming. Hidden parts may be missed."
        note_ru = "Проверьте каждую рамку перед подтверждением. Скрытые детали могут быть пропущены."
    except models.BudgetStop:
        counted, model = count_offline(image), "offline-components"
        label = "offline counter"
        note = ("SAM is unavailable or its budget is off/exhausted. Offline counts separated "
                "foreground shapes, not part types. Shelves, shadows and touching parts can "
                "miscount. Use a plain background and check every box; confidence is uncalibrated.")
        note_ru = ("SAM недоступен или его бюджет выключен/исчерпан. Офлайн-счётчик считает отдельные "
                   "контрастные области, не распознавая тип деталей. Полки, тени и соприкасающиеся "
                   "детали искажают счёт. Используйте однотонный фон и проверьте рамки; уверенность "
                   "не калибрована.")
    payload = {**counted, "item_id": item_id, "item_name": item["name"],
               "item_name_ru": item["name_ru"], "prompt": prompt, "before": item["qty"],
               "revision": item["revision"], "photo_hash": photo_hash,
               "box_format": "xyxy-pixels", "note": note, "note_ru": note_ru,
               "evidence": {"count": "ESTIMATE", "before": "FACT", "confidence": "ESTIMATE",
                            "boxes": "ESTIMATE", "dimensions": "FACT"},
               "choices": [{"decision": "yes", "label": "Yes", "label_ru": "Да"},
                           {"decision": "no", "label": "No", "label_ru": "Нет"}],
               "message": (f"ESTIMATE: I see {counted['count']} {prompt} ({label}). "
                           f"FACT: Stock says {item['qty']} {item['unit']}. "
                           f"Set stock to ESTIMATE: {counted['count']}?"),
               "message_ru": (f"ESTIMATE: На фото {counted['count']} — {prompt} "
                              f"({'SAM 3.1' if model == 'sam-3.1' else 'офлайн-счётчик'}). "
                              f"FACT: В учёте {item['qty']} {item['unit_ru']} "
                              f"Установить остаток ESTIMATE: {counted['count']}?")}
    with conn:
        # Two simultaneous retries must create one stored offer.
        conn.execute("BEGIN IMMEDIATE")
        previous = _request_hit(conn, person_id, idem_key, fingerprint)
        if previous:
            return previous
        action_id = conn.execute(
            "INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
            "VALUES ('shelf_count', ?, ?, ?, ?)",
            (json.dumps(payload, ensure_ascii=False), model,
             domain.now(conn).isoformat(timespec="seconds"), person_id)).lastrowid
        if idem_key:
            conn.execute("INSERT INTO shelf_count_requests VALUES (?, ?, ?, ?)",
                         (person_id, idem_key, fingerprint, action_id))
    return _reply(_action(conn, action_id))


def apply(conn, person_id: int, action_id: int, decision: str = "yes") -> dict:
    """Apply only the stored count, atomically with its decision and audit row."""
    _office(conn, person_id)
    if decision not in ("yes", "no"):
        raise DomainError("Choose an offered Yes or No. / Выберите предложенное «Да» или «Нет».")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _action(conn, action_id)
        expected_status = "applied" if decision == "yes" else "rejected"
        if row["status"] != "proposed":
            if row["status"] != expected_status:
                raise DomainError("This proposal is closed. Count a new photo to change stock. / "
                                  "Предложение закрыто. Для изменения остатка посчитайте новое фото.")
            return {**json.loads(row["result"]), "already": True,
                    "message": "Already recorded. Nothing changed.",
                    "message_ru": "Уже записано. Ничего не изменилось."}
        payload = json.loads(row["payload"])
        when = domain.now(conn).isoformat(timespec="seconds")
        result = {"ok": True, "id": action_id, "status": expected_status, "already": False}
        if decision == "yes":
            item = _item(conn, payload["item_id"])
            if item["qty"] != payload["before"] or item["revision"] != payload["revision"]:
                raise DomainError("Stock changed since this photo proposal. Count again before confirming. / "
                                  "После предложения остаток изменился. Посчитайте заново перед подтверждением.")
            conn.execute("UPDATE shelf_items SET qty = ?, revision = revision + 1 WHERE id = ?",
                         (payload["count"], item["id"]))
            adjustment_id = conn.execute(
                "INSERT INTO stock_counts (action_id, person_id, created_at, item_id, "
                "before_qty, after_qty, photo_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (action_id, person_id, when, item["id"], item["qty"], payload["count"],
                 payload["photo_hash"])).lastrowid
            result.update(adjustment_id=adjustment_id, item_id=item["id"],
                          before=item["qty"], after=payload["count"], evidence="FACT",
                          count_source="ESTIMATE",
                          message=f"FACT: Stock set from {item['qty']} to {payload['count']} {item['unit']} after your confirmation.",
                          message_ru=f"FACT: После вашего подтверждения остаток изменён с {item['qty']} на {payload['count']} {item['unit_ru']}")
        else:
            result.update(message="Count rejected. Stock was not changed.",
                          message_ru="Подсчёт отклонён. Остаток не изменён.")
        conn.execute("UPDATE agent_actions SET status = ?, decided_by = ?, decided_at = ?, "
                     "result = ? WHERE id = ?",
                     (expected_status, person_id, when, json.dumps(result, ensure_ascii=False), action_id))
    return result


async def _body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise DomainError("Upload is too large. Resize the photo and try again. / "
                              "Загрузка слишком большая. Уменьшите фото и повторите.")
    return bytes(data)


async def _upload(request):
    """Bounded MIME parsing avoids requiring the optional python-multipart package."""
    body = await _body(request, MAX_BYTES + 65536)
    content_type = request.headers.get("content-type", "")
    if content_type.lower().startswith("image/"):
        fields, image = dict(request.query_params), body
    else:
        if (not content_type.lower().startswith("multipart/form-data;")
                or "\r" in content_type or "\n" in content_type):
            raise DomainError("Send a photo upload with prompt and item_id. / "
                              "Загрузите фото, указав prompt и item_id.")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        if not message.is_multipart() or any(part.defects for part in message.walk()):
            raise DomainError("Incomplete photo upload. Try uploading again. / "
                              "Неполная загрузка фото. Загрузите его повторно.")
        fields, image = {}, None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if part.is_multipart() or name not in ("image", "prompt", "item_id", "idem_key") or name in fields:
                raise DomainError("Use image, prompt, item_id and optional idem_key once each. / "
                                  "Передайте image, prompt, item_id и необязательный idem_key по одному разу.")
            content = part.get_payload(decode=True)
            if content is None:
                raise DomainError("The upload is empty. Try again. / Загрузка пуста. Повторите.")
            if name == "image":
                image, fields[name] = content, True
            else:
                try:
                    fields[name] = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DomainError("Use UTF-8 text fields. / Используйте текстовые поля в UTF-8.") from exc
    try:
        item_id = int(fields.get("item_id", ""))
    except ValueError as exc:
        raise DomainError("Choose a shelf item first. / Сначала выберите складскую позицию.") from exc
    if not image or not fields.get("prompt"):
        raise DomainError("Upload a photo and say what to count. / Загрузите фото и укажите, что считать.")
    return image, fields["prompt"], item_id, fields.get("idem_key")


@router.get("/items")
def items_route(s: Session = Depends(office), conn=Depends(get_conn)):
    return {"ok": True, "items": [dict(r) for r in conn.execute("SELECT * FROM shelf_items ORDER BY id")],
            "evidence": "FACT", "message": "Choose the stock item for your photo.",
            "message_ru": "Выберите складскую позицию для фотографии."}


@router.post("/count")
async def count_route(request: Request, s: Session = Depends(office), conn=Depends(get_conn)):
    image, prompt, item_id, key = await _upload(request)
    return await run_in_threadpool(propose, conn, s.person_id, image, prompt, item_id, key)


@router.get("/{action_id}")
def proposal_route(action_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return _reply(_action(conn, action_id))


@router.post("/{action_id}/apply")
async def apply_route(action_id: int, request: Request, s: Session = Depends(office), conn=Depends(get_conn)):
    try:
        body = json.loads(await _body(request, 1024))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError("Confirm with a Yes or No decision. / Подтвердите решением «Да» или «Нет».") from exc
    if not isinstance(body, dict) or set(body) != {"decision"} or body["decision"] not in ("yes", "no"):
        raise DomainError("Only the offered decision is accepted; count and item cannot be changed. / "
                          "Принимается только предложенное решение; количество и позицию менять нельзя.")
    return await run_in_threadpool(apply, conn, s.person_id, action_id, body["decision"])
