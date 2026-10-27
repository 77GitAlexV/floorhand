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
