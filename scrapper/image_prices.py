"""Read a linked cash schedule with a ruled exam and amount grid."""
import csv
import io
import os
import re
import shutil
import subprocess
from bisect import bisect_right
from collections import defaultdict
from itertools import groupby
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lxml import html

from .prices import EXAM, parse_value


def image_schedule(content, origin):
    if isinstance(content, bytes) and content.lstrip().startswith(b"%PDF-"):
        return None
    doc = html.document_fromstring(content)
    for img in doc.xpath("//img[@src]"):
        src = urljoin(origin, img.get("src"))
        if urlsplit(src).netloc != urlsplit(origin).netloc or urlsplit(src).scheme != "https":
            continue
        if re.search(r"(?:cash|self[- ]pay)[- ]pric", src + " " + img.get("alt", ""), re.I):
            return src
    return None


def lines(positions):
    return [sum(group) // len(group) for group in
            ([x for _, x in seq] for _, seq in groupby(enumerate(positions), lambda p: p[1] - p[0]))]


def recognize(image, xs, ys, binary):
    # Locate every OCR word in the original grid, using its bounding box.
    # Read the amount and exam independently so missing rows never shift values.
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    proc = subprocess.run([binary, "stdin", "stdout", "--psm", "6", "tsv"], input=buf.getvalue(),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=True)
    scale = image.width / (xs[3] - xs[1])
    cells = defaultdict(lambda: [[], []])
    for word in csv.DictReader(io.StringIO(proc.stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word["level"] != "5" or not word["text"].strip():
            continue
        mid = (int(word["top"]) + int(word["height"]) / 2) / scale
        row = bisect_right(ys, mid) - 1
        right = (int(word["left"]) + int(word["width"]) / 2) / scale + xs[1] > xs[2]
        cells[row][int(right)].append((word["text"], float(word["conf"])))
    output = {}
    for row, (label, amount) in cells.items():
        if not (label and len(amount) == 1 and 0 <= row < len(ys) - 1):
            continue
        name = " ".join(w for w, _ in label)
        rate, confidence = amount[0]
        imaging = EXAM.search(name) or re.fullmatch(r"MR (?:joint )?arthrogram", name, re.I)
        if confidence >= 85 and min(c for _, c in label) >= 65 and imaging and parse_value(rate):
            output[row] = name, rate
    return output


def schedule_rows(blob):
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ValueError("Install the scraper image dependency Pillow") from exc
    binary = shutil.which("tesseract")
    if not binary and os.name == "nt":
        path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe"
        binary = str(path) if path.is_file() else None
    if not binary:
        raise ValueError("Image price schedule found. Install Tesseract OCR to read the amounts")
    with Image.open(io.BytesIO(blob)) as src:
        w, h = src.size
        if w * h > 12_000_000 or min(w, h) < 300 or max(w, h) > 6000:
            raise ValueError("Image resolution outside the supported price-grid limits")
        rgba = src.convert("RGBA")
        gray = Image.new("RGB", (w, h), "white")
        gray.paste(rgba, mask=rgba.getchannel("A"))
        grid = gray.convert("L").point(lambda p: 255 if p > 150 else 0)
    xs = lines([x for x in range(w) if grid.crop((x, 0, x + 1, h)).histogram()[0] > .85 * h])
    if len(xs) != 4 or xs[0] > .05 * w or xs[-1] < .95 * w or xs[2] - xs[1] < w / 3:
        return []
    left, right = xs[1] + 6, xs[2] - 6
    ys = lines([y for y in range(h) if grid.crop((left, y, right, y + 1)).histogram()[0] > .85 * (right - left)])
    if not 3 <= len(ys) <= 100:
        return []
    draw = ImageDraw.Draw(grid)
    width = max(2, w // 360)
    for x in xs:
        draw.rectangle((x-width, 0, x+width, h), fill=255)
    for y in ys:
        draw.rectangle((0, y-width, w, y+width), fill=255)
    grid = grid.crop((xs[1], 0, xs[3], h))
    try:
        first = recognize(grid, xs, ys, binary)
        second = recognize(grid.resize((grid.width * 3 // 4, h * 3 // 4)), xs, ys, binary)
    except (subprocess.SubprocessError, KeyError, UnicodeError) as exc:
        raise ValueError("OCR could not complete for the linked schedule") from exc
    return [(n+1, *values) for n, values in first.items() if second.get(n) == values]
