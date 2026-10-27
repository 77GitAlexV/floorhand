"""Small offline counter for separated parts on a plain background."""
import io
import math
import struct
import zlib
from statistics import median

from .domain import DomainError

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000
SIDE = 384
PNG = b"\x89PNG\r\n\x1a\n"


def _bad_image():
    return DomainError(
        "Cannot read this photo. Use a non-interlaced 8-bit PNG, or JPEG with Pillow installed. / "
        "Не удалось прочитать фото. Используйте PNG без чересстрочности с глубиной 8 бит "
        "или JPEG при установленном Pillow.")


def _size(width, height):
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise DomainError(
            "Photo is too large or empty. Resize to at most CALC: 4 megapixels. / "
            "Фото слишком большое или пустое. Уменьшите до CALC: 4 мегапикселей.")


def _paeth(a, b, c):
    p = a + b - c
    distances = (abs(p - a), abs(p - b), abs(p - c))
    return (a, b, c)[distances.index(min(distances))]


def _png_grid(image):
    """Decode bounded PNG rows; retain only a small grayscale sampling grid."""
    if not image.startswith(PNG):
        raise _bad_image()
    offset, header, compressed, ended = 8, None, bytearray(), False
    while offset + 12 <= len(image):
        length = struct.unpack_from(">I", image, offset)[0]
        kind = image[offset + 4:offset + 8]
        end = offset + 8 + length
        if end + 4 > len(image):
            raise _bad_image()
        data = image[offset + 8:end]
        if zlib.crc32(kind + data) != struct.unpack_from(">I", image, end)[0]:
            raise _bad_image()
        if header is None and kind != b"IHDR":
            raise _bad_image()
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise _bad_image()
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            ended = length == 0 and end + 4 == len(image)
            break
        elif kind == b"tRNS" or (not kind[0] & 32 and kind != b"PLTE"):
            # Transparency keys and palette images need Pillow.
            raise _bad_image()
        offset = end + 4
    if not header or not ended:
        raise _bad_image()
    width, height, depth, color, compression, filtering, interlace = header
    _size(width, height)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or not channels or compression or filtering or interlace:
        raise _bad_image()
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(compressed), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise _bad_image()
    step = max(1, math.ceil(max(width, height) / SIDE))
    previous, grid = bytearray(stride), []
    for y in range(height):
        start = y * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if filter_type > 4:
            raise _bad_image()
        if filter_type:
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                if filter_type == 1:
                    predictor = a
                elif filter_type == 2:
                    predictor = b
                elif filter_type == 3:
                    predictor = (a + b) // 2
                else:
                    predictor = _paeth(a, b, c)
                row[i] = (row[i] + predictor) & 255
        if y % step == 0:
            sample = []
            for x in range(0, width, step):
                i = x * channels
                gray = row[i] if color in (0, 4) else (
                    299 * row[i] + 587 * row[i + 1] + 114 * row[i + 2]) // 1000
                alpha = row[i + channels - 1] if color in (4, 6) else 255
                sample.append((gray * alpha + 255 * (255 - alpha)) // 255)
            grid.append(sample)
        previous = row
    return grid, width, height


def decode_grid(image):
    if not image or len(image) > MAX_BYTES:
        raise DomainError(
            "Upload a photo up to CALC: 8 MiB. / Загрузите фото размером до CALC: 8 МиБ.")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        try:
            return _png_grid(image)
        except (ValueError, struct.error, zlib.error, IndexError) as exc:
            raise _bad_image() from exc
    try:
        with Image.open(io.BytesIO(image)) as original:
            _size(*original.size)
            oriented = ImageOps.exif_transpose(original)
            width, height = oriented.size
            oriented.thumbnail((SIDE, SIDE))
            rgba = oriented.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            gray = background.convert("L")
            pixels = list(gray.getdata())
            return [pixels[i:i + gray.width] for i in range(0, len(pixels), gray.width)], width, height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise _bad_image() from exc


def count_grid(grid, width=None, height=None):
    """Count connected contrast regions, with conservative uncalibrated confidence."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if not rows or not cols or rows > SIDE or cols > SIDE or any(len(r) != cols for r in grid):
        raise _bad_image()
    if any(type(p) is not int or not 0 <= p <= 255 for r in grid for p in r):
        raise _bad_image()
    width, height = width or cols, height or rows
    _size(width, height)
    border = grid[0] + grid[-1] + [r[0] for r in grid] + [r[-1] for r in grid]
    background = median(border)
    mask = bytearray(abs(p - background) >= 28 for row in grid for p in row)
    minimum = max(6, rows * cols // 2500)
    boxes, touches_border = [], False
    for start in range(rows * cols):
        if not mask[start]:
            continue
        mask[start] = 0
        queue, area = [start], 0
        x0 = x1 = start % cols
        y0 = y1 = start // cols
        while queue:
            position = queue.pop()
            y, x = divmod(position, cols)
            area += 1
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            neighbors = []
            if x:
                neighbors.append(position - 1)
            if x + 1 < cols:
                neighbors.append(position + 1)
            if y:
                neighbors.append(position - cols)
            if y + 1 < rows:
                neighbors.append(position + cols)
            for neighbor in neighbors:
                if mask[neighbor]:
                    mask[neighbor] = 0
                    queue.append(neighbor)
        if area >= minimum:
            boxes.append([math.floor(x0 * width / cols), math.floor(y0 * height / rows),
                          min(width, math.ceil((x1 + 1) * width / cols)),
                          min(height, math.ceil((y1 + 1) * height / rows))])
            touches_border |= x0 == 0 or y0 == 0 or x1 == cols - 1 or y1 == rows - 1
    boxes.sort(key=lambda box: (box[1], box[0]))
    return {"count": len(boxes), "boxes": boxes, "width": width, "height": height,
            "confidence": 0.25 if not boxes else (0.35 if touches_border else 0.6),
            "confidence_kind": "uncalibrated-heuristic"}


def count_offline(image):
    grid, width, height = decode_grid(image)
    return count_grid(grid, width, height)
