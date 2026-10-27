"""Read invented order forms and sheet photos; callers review extracted fields."""
from .. import models
from . import _wire
from .text import _json


def understand(conn, purpose, image_bytes, mime, prompt, schema=None, *,
               model=models.SPARK, max_output_tokens=2000) -> str | dict:
    input_ = _wire.image_input(image_bytes, mime, prompt)
    if schema is not None:
        return _json(conn, purpose, input_, schema, model=model, max_output_tokens=max_output_tokens)
    data = models.respond(conn, purpose, input_, model=model,
                          max_output_tokens=max_output_tokens, store=False)
    text = _wire.output_text(data)
    if not text.strip():
        raise _wire.stop("No image description returned.", "Описание снимка не получено.")
    return text
