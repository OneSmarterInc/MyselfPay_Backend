"""Read simple, single self-pay column schedules from public PDF text."""
import io
import re
from .prices import EXAM, parse_value
from .providers import PRICE


def read_schedule(content, add):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ValueError("Install the scraper PDF dependency: python -m pip install 'pypdf>=6.1,<7'") from exc
    try:
        pdf = PdfReader(io.BytesIO(content))
        if pdf.is_encrypted or len(pdf.pages) > 40:
            raise ValueError("PDF needs review (encrypted or longer than 40 pages)")
        for number, page in enumerate(pdf.pages, 1):
            lines = (page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or "").splitlines()
            notes = " ".join(line.strip() for line in lines if line.lstrip().startswith(("*", "†")))
            active = False
            for line in lines:
                line = line.strip()
                if re.fullmatch(r"(?:procedure|exam|service)\s{2,}(?:self[- ]pay|cash price)", line, re.I):
                    active = True
                    continue
                if not active or not line:
                    continue
                if re.fullmatch(r"at time of service[†*]?", line, re.I):
                    continue
                match = re.fullmatch(rf"([^$]+?)\s{{2,}}({PRICE})([*†]*)", line)
                if not match:
                    active = False
                    continue
                if EXAM.search(match[1]):
                    evidence = f"PDF page {number} | {line}"
                    add(match[1], evidence, parse_value(match[2]), "Self-pay schedule. " + notes)
    except (PdfReadError, IndexError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Could not read the PDF price schedule") from exc
