"""Verified Meta wire shapes; sources and remaining limits are in NOTES-meta-client.md."""
import base64
import re

from ..models import BudgetStop


def stop(message, ru):
    return BudgetStop(f"{message} Use the offline path. / {ru} Используйте офлайн-режим.")


def messages(system, user):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def json_format(schema):
    # strict=false accepts callers' optional fields without rewriting their schema.
    return {"format": {"type": "json_schema", "name": "floorhand_result",
                       "schema": schema, "strict": False}}


def image_input(image_bytes, mime, prompt):
    supported = {"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp", "image/x-icon"}
    if mime not in supported or not image_bytes or len(image_bytes) > 50_000_000:
        raise stop("Send a supported image under 50 MB.", "Нужен поддерживаемый снимок до 50 МБ.")
    url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")
    return [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": url}]}]


def output_items(data):
    if data.get("status") not in (None, "completed") or data.get("error"):
        raise stop("No complete model answer.", "Нет полного ответа модели.")
    items = data.get("output", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise stop("Invalid model output.", "Некорректный ответ модели.")
    return items


def output_text(data):
    items = output_items(data)
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts = []
    for item in items:
        if item.get("type") != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
            raise stop("Invalid message content.", "Некорректное содержимое сообщения.")
        for part in content:
            if part.get("type") == "refusal":
                raise stop("The model declined this request.", "Модель отклонила запрос.")
            if part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise stop("Invalid output text.", "Некорректный текст ответа.")
                parts.append(part["text"])
    return "".join(parts)


def voice_fields(language):
    settings = {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    if language:
        # The API takes language names, not ISO codes. Russian is not an evaluated language.
        hints = {"en": "English", "ru": "Russian"}
        name = hints.get(language, language)
        supported = {"Arabic", "Bengali", "Dutch", "English", "French", "German", "Hebrew", "Hindi",
                     "Indonesian", "Italian", "Japanese", "Kannada", "Korean", "Malay", "Mandarin Chinese",
                     "Marathi", "Polish", "Portuguese", "Spanish", "Tagalog", "Tamil", "Telugu", "Thai",
                     "Turkish", "Vietnamese"}
        if name not in supported:
            raise stop("This language hint is not supported by Meta Voice.",
                       "Этот язык подсказки не поддерживается Meta Voice.")
        settings["languageBias"] = [name]
    return {"request": settings}


def voice_units(data):
    value = data.get("audioDurationMs")
    if value is None:
        return None
    if type(value) not in (int, float) or value < 0:
        raise ValueError("Invalid duration")
    return value // 1000  # Documented billing rounds DOWN to whole seconds.


def sam_units(data):
    value = (data.get("usage") or {}).get("images_processed")
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("Invalid image count")
    return value


_BOX = r"(?P<id>\d+)<\|box;(?P<box>[^|]+)\|>"
_MASK = r"<\|mask;x=0;y=0(?:;c=(?P<confidence>[\d.]+))?;data=(?P<h>\d+),(?P<w>\d+),(?P<encoding>[~!])(?P<payload>.*?)\|>"
_RECORD = re.compile(_BOX + _MASK)


def segmentation(text):
    """Keep the documented base85 mask opaque; do not invent an RLE decoder."""
    objects = []
    if text.strip():
        if not text.strip().startswith("<0f>"):
            raise ValueError("Expected image frame zero")
        rest = text.strip()[4:]
        while rest:
            match = _RECORD.match(rest)
            if not match:
                raise ValueError("Invalid segmentation record")
            box = dict(field.split("=", 1) for field in match["box"].split(";"))
            values = {k: int(box[k]) for k in ("x1", "y1", "x2", "y2", "w", "h")}
            if not (0 <= values["x1"] <= values["x2"] <= values["w"] and
                    0 <= values["y1"] <= values["y2"] <= values["h"] and
                    values["w"] > 0 and values["h"] > 0):
                raise ValueError("Invalid box geometry")
            mask = {"height": int(match["h"]), "width": int(match["w"]),
                    "encoding": "lossless" if match["encoding"] == "~" else "one_bit",
                    "payload": match["payload"]}
            if not mask["height"] or not mask["width"] or not mask["payload"]:
                raise ValueError("Empty mask")
            obj = {"id": int(match["id"]), "box": values, "mask": mask}
            if "c" in box:
                obj["confidence"] = float(box["c"])
                if not 0 <= obj["confidence"] <= 1 or float(match["confidence"]) != obj["confidence"]:
                    raise ValueError("Invalid confidence")
            objects.append(obj)
            rest = rest[match.end():]
            if rest:
                if not rest.startswith(",") or len(rest) == 1:
                    raise ValueError("Invalid record separator")
                rest = rest[1:]
    if len({obj["id"] for obj in objects}) != len(objects):
        raise ValueError("Duplicate object id")
    count = len(objects)
    return {"objects": objects, "boxes": [obj["box"] for obj in objects],
            "masks": [obj["mask"] for obj in objects], "count": count, "basis": "ESTIMATE",
            "message": f"ESTIMATE: {count} matches; review the image. / "
                       f"ОЦЕНКА: найдено {count}; проверьте снимок."}
