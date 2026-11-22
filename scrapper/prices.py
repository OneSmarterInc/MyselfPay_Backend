"""Numeric observations for the price table and CSV. All stored amounts are cents."""
import csv
import re
from decimal import Decimal

from .providers import PRICE
from .body_parts import exam_scope


FIELDS = ("center", "location_id", "address", "modality", "exam", "contrast",
          "price_type", "price_usd", "max_price_usd", "payment_terms",
          "status", "source_url", "evidence", "checked_at",
          "body_part", "exam_variant", "exclusions", "body_part_column")
EXAM = re.compile(r"\b(PET\s*/\s*CT|MRI|CT)\b", re.I)
NON_OFFER = re.compile(
    r"\b(?:deposit|copay|co-pay|discount|insurance|insured|average|savings?|"
    r"competitor|other (?:centers|providers)|comparison|hospital charge|list price|retail|deductible|monthly|example|financing|median|national|surcharge|down payment)\b", re.I)
VALUE = re.compile(rf"(?:(starting at|starts at|from|as low as)\s+)?({PRICE})"
                   rf"(?:\s*(?:[-–—]|to)\s*({PRICE}))?", re.I)
PAIR = re.compile(r"(?P<label>[^$|:;!?]{2,150}?)\s*(?:[:|]|\s[-–—]\s)\s*(?P<value>.+)")


def cents(value):
    return int(Decimal(value[1:].replace(",", "")) * 100)


def parse_value(text):
    match = VALUE.fullmatch(text.strip())
    if not match:
        return None
    low = cents(match[2])
    high = cents(match[3]) if match[3] else None
    if high is not None and (match[1] or high < low):
        return None
    kind = "range" if high is not None else "starting" if match[1] else "listed"
    return low, high, kind


def price_rows(report):
    centers = {c["location_id"]: c for c in report["centers"]}
    for row in [*report["offers"], *report["review_candidates"]]:
        if row.get("location_id"):
            center = centers[row["location_id"]]
            name, address = center["name"], center["address"]
            modality, exam = row["mod"], row["body"]
            terms, status = row["payment_terms"], "observed"
        else:
            if row["amount_cents"] is None or not row.get("payment_context"):
                continue
            name, address = row["provider_page_title"], None
            exam = row["exam"]
            modality = EXAM.search(exam)[1].upper()
            terms, status = row["payment_context"], "needs_review"
        high = row.get("max_amount_cents")
        yield {
            **exam_scope(exam),
            "center": name, "location_id": row.get("location_id"), "address": address,
            "modality": modality, "exam": exam, "contrast": row["contrast"],
            "price_type": row["price_type"], "price_usd": row["amount_cents"] / 100,
            "max_price_usd": high / 100 if high is not None else None,
            "payment_terms": terms, "status": status, "source_url": row["source_url"],
            "evidence": row["evidence"], "checked_at": row["checked_at"],
        }


def write_csv(stream, rows, fields=FIELDS):
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Provider-controlled labels must remain text when opened in a spreadsheet.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(
            ("=", "+", "-", "@")) else v for k, v in row.items()})
