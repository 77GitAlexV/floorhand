"""SAM image segmentation with encoded masks and source-pixel boxes."""
from .. import models
from . import _wire


def segment(conn, image_bytes, mime, prompt, *, purpose="sam") -> dict:
    input_ = _wire.image_input(image_bytes, mime, prompt)
    data = models.request_units(conn, purpose, "sam-3.1", 1, "/responses",
                                body={"model": "sam-3.1", "input": input_, "stream": True},
                                usage_units=_wire.sam_units)
    try:
        return _wire.segmentation(_wire.output_text(data))
    except (ValueError, KeyError, TypeError) as exc:
        raise _wire.stop("Invalid segmentation; review the image manually.",
                         "Некорректная сегментация; проверьте снимок вручную.") from exc


def count(conn, image, prompt) -> dict:
    """Adapt source-pixel segmentation to shelf proposals without changing stock."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif image.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    else:
        raise _wire.stop("Send a PNG or JPEG photo.",
                         "Нужен снимок PNG или JPEG.")
    result = segment(conn, image, mime, prompt, purpose="shelf_count")
    boxes = result["boxes"]
    if boxes:
        width, height = boxes[0]["w"], boxes[0]["h"]
        if any((b["w"], b["h"]) != (width, height) for b in boxes):
            raise _wire.stop("Image dimensions disagree; review the photo.",
                             "Размеры не совпадают; проверьте снимок.")
    else:
        from ..shelf_vision import decode_grid
        _, width, height = decode_grid(image)
    # Missing scores provide no confidence evidence; never invent a positive score.
    confidence = min((obj.get("confidence", 0.0) for obj in result["objects"]), default=0.0)
    return {"boxes": [[b[k] for k in ("x1", "y1", "x2", "y2")] for b in boxes],
            "width": width, "height": height, "confidence": confidence, "count": result["count"]}
