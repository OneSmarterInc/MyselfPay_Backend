"""Produce a review-only pilot report from a CSV of provider pricing URLs."""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from lxml.etree import ParserError

from .dynamic import fetch_public, review_page
from http.client import HTTPException
from .providers import BRANCHES, extract


MAX_PAGE_BYTES = 2_000_000
KEY_FIELDS = ("location_id", "mod", "body", "contrast", "payment_option")
COMPARE_FIELDS = ("amount_cents", "price_type", "payment_terms")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url):
    if url not in BRANCHES:
        raise ValueError("URL does not have a configured provider parser")
    opener = build_opener(NoRedirect())
    request = Request(url, headers={"User-Agent": "MySelfPayPriceReview/0.1", "Accept": "text/html"})
    with opener.open(request, timeout=12) as response:
        if response.headers.get_content_type() != "text/html":
            raise ValueError("Source did not return HTML")
        content = response.read(MAX_PAGE_BYTES + 1)
    if len(content) > MAX_PAGE_BYTES:
        raise ValueError("Source page exceeds the size limit")
    return content


def compare(offers, previous):
    baseline = {}
    for offer in previous:
        key = tuple(offer[field] for field in KEY_FIELDS)
        if key in baseline:
            raise ValueError(f"Duplicate offer in baseline: {key}")
        baseline[key] = offer
    findings = []
    for offer in offers:
        key = tuple(offer[field] for field in KEY_FIELDS)
        old = baseline.get(key)
        if old is None:
            findings.append({"status": "new_offer", "observed": offer})
            continue
        changed = [field for field in COMPARE_FIELDS if old.get(field) != offer.get(field)]
        if changed:
            findings.append({
                "status": "changed_offer", "changed_fields": changed,
                "previous": old, "observed": offer,
            })
    return findings


def run(csv_path, previous, fetch_page=fetch):
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        return run_csv(stream, previous, fetch_page)


def run_csv(stream, previous, fetch_page=fetch):
    rows = csv.DictReader(stream)
    if not rows.fieldnames or "pricing_url" not in rows.fieldnames:
        raise ValueError("CSV needs a pricing_url column")
    urls = list(dict.fromkeys((row.get("pricing_url") or "").strip() for row in rows))
    urls = [url for url in urls if url]
    if not urls:
        raise ValueError("CSV has no pricing URLs")

    centers, offers, issues, candidates = [], [], [], []
    for url in urls:
        try:
            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if url not in BRANCHES:
                content, final_url = fetch_public(url, MAX_PAGE_BYTES)
                rows = review_page(content, final_url, checked_at)
                candidates.extend(rows)
                if not rows:
                    issues.append({"source_url": final_url, "status": "needs_review",
                                   "detail": "No exam and price found together in readable HTML"})
                continue
            content = fetch_page(url)
            center, found = extract(url, content, checked_at)
        except (OSError, ValueError, ParserError, HTTPException, UnicodeError) as exc:
            issues.append({"source_url": url, "status": "source_issue", "detail": str(exc)})
            continue
        centers.append(center)
        offers.extend(found)
    return {"source_count": len(urls), "review_candidates": candidates, "centers": centers, "offers": offers, "findings": compare(offers, previous), "issues": issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls_csv", type=Path)
    parser.add_argument("--baseline", type=Path, help="A reviewed report containing the approved offers")
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["offers"] if args.baseline else []
        if not isinstance(baseline, list):
            raise ValueError("Baseline offers must be a list")
        report = run(args.urls_csv, baseline)
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return int(bool(report["issues"]))


if __name__ == "__main__":
    sys.exit(main())
