"""Produce a review-only pilot report from a CSV of provider pricing URLs."""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lxml.etree import ParserError

from .dynamic import price_links, review_page
from .public_fetch import fetch_public
from http.client import HTTPException
from .body_parts import OVERVIEW_FIELDS, overview, overview_records
from .prices import FIELDS, price_rows, write_csv
from .providers import BRANCHES, extract


MAX_PAGE_BYTES = 2_000_000
KEY_FIELDS = ("location_id", "mod", "body", "contrast", "payment_option")
COMPARE_FIELDS = ("amount_cents", "price_type", "payment_terms")


def fetch(url):
    if url not in BRANCHES:
        raise ValueError("URL does not have a configured provider parser")
    content, final = fetch_public(url, MAX_PAGE_BYTES)
    if urlsplit(final).netloc != urlsplit(url).netloc:
        raise ValueError("Pilot page redirected to a different provider")
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


SOURCE_ERRORS = (OSError, ValueError, ParserError, HTTPException, UnicodeError)


def is_numeric(row):
    return row["amount_cents"] is not None and bool(row["payment_context"])


def read_source(url, checked, attempts, candidates):
    from .dynamic import page_hint
    try:
        content, final = fetch_public(url, MAX_PAGE_BYTES)
    except SOURCE_ERRORS as exc:
        parts = urlsplit(url)
        if str(exc) != "Source returned HTTP 404" or parts.path in ("", "/"):
            raise
        # A provider can move a price list without updating a directory link.
        attempts.append({"url": url, "detail": str(exc)})
        home = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        content, final = fetch_public(home, MAX_PAGE_BYTES)
        if urlsplit(final).netloc != parts.netloc:
            raise ValueError("Directory URL expired and provider homepage moved to another host")

    def inspect(body, origin):
        notes = []
        rows = review_page(body, origin, checked, interactive=True, notes=notes, image_fetch=fetch_public)
        for row in rows:
            row["starting_url"] = url
        count = sum(map(is_numeric, rows))
        priced = sum(row["amount_cents"] is not None and not row["payment_context"] for row in rows)
        notes.insert(0, f"{count} numeric prices" if count else page_hint(body, origin))
        if priced:
            notes.append(f"{priced} amounts with payment terms to verify")
        attempts.append({"url": origin, "detail": " · ".join(notes)})
        candidates.extend(rows)
        return count

    count = inspect(content, final)
    visited = {url, final}
    for linked in price_links(content, final, options_only=bool(count)):
        if linked in visited:
            continue
        visited.add(linked)
        try:
            page, origin = fetch_public(linked, MAX_PAGE_BYTES)
            if urlsplit(origin).netloc != urlsplit(final).netloc:
                raise ValueError("Pricing link redirected outside the provider website")
            if origin != linked and origin in visited:
                continue
            visited.add(origin)
            inspect(page, origin)
        except SOURCE_ERRORS as exc:
            attempts.append({"url": linked, "detail": str(exc)})


def run_csv(stream, previous, fetch_page=fetch, *, snapshots=()):
    rows = csv.DictReader(stream)
    if not rows.fieldnames or "pricing_url" not in rows.fieldnames:
        raise ValueError("CSV needs a pricing_url column")
    urls = list(dict.fromkeys((row.get("pricing_url") or "").strip() for row in rows))
    urls = [url for url in urls if url]
    if not urls:
        raise ValueError("CSV has no pricing URLs")

    from .snapshots import match_snapshots
    saved = match_snapshots(snapshots, urls)
    centers, offers, issues, candidates, sources = [], [], [], [], []
    for url in urls:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        attempts = []
        start = len(candidates)
        found = []
        status, detail = "read", ""
        try:
            if url in saved:
                # Offline evidence is always a review candidate, including on pilot URLs.
                rows = review_page(saved[url], url, checked_at)
                for row in rows:
                    row.update(starting_url=url, evidence_origin="uploaded_html")
                candidates.extend(rows)
                attempts.append({"url": url, "detail": "Read uploaded provider HTML snapshot; capture date requires review"})
            elif url in BRANCHES:
                content = fetch_page(url)
                center, found = extract(url, content, checked_at)
                centers.append(center)
                offers.extend(found)
                attempts.append({"url": url, "detail": "Read provider price list"})
            else:
                read_source(url, checked_at, attempts, candidates)
        except SOURCE_ERRORS as exc:
            status, detail = "unavailable", str(exc)
            attempts.append({"url": url, "detail": detail})
        batch = candidates[start:]
        count = len(found) + sum(is_numeric(row) for row in batch)
        if count:
            status = "prices_found"
        elif status == "read":
            status = "review"
            detail = "No numeric self-pay exam price identified in the checked content."
        if detail:
            issues.append({"source_url": url, "status": "source_issue" if status == "unavailable" else "needs_review",
                           "detail": detail})
        sources.append({"source_url": url, "status": status, "numeric_count": count, "attempts": attempts})
    report = {"source_count": len(urls), "review_candidates": candidates, "source_results": sources,
              "centers": centers, "offers": offers, "findings": compare(offers, previous), "issues": issues}
    report["pricing_rows"] = list(price_rows(report))
    report["body_part_overview"] = overview(report["pricing_rows"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls_csv", type=Path)
    parser.add_argument("--baseline", type=Path, help="A reviewed report containing the approved offers")
    parser.add_argument("--prices-csv", type=Path, help="Write numeric price observations as CSV")
    parser.add_argument("--overview-csv", type=Path, help="Write the branch, scan and contrast overview")
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["offers"] if args.baseline else []
        if not isinstance(baseline, list):
            raise ValueError("Baseline offers must be a list")
        outputs = [p.resolve() for p in (args.prices_csv, args.overview_csv) if p]
        if len(set(outputs)) < len(outputs) or set(outputs) & {p.resolve() for p in (args.urls_csv, args.baseline) if p}:
            raise ValueError("Choose separate paths for input and output files")
        report = run(args.urls_csv, baseline)
        prices = report["pricing_rows"]
        summary = overview_records(report["body_part_overview"], prices)
        for path, rows, fields in ((args.prices_csv, prices, FIELDS), (args.overview_csv, summary, OVERVIEW_FIELDS)):
            if path:
                with path.open("w", encoding="utf-8-sig", newline="") as output:
                    write_csv(output, rows, fields)
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return int(bool(report["issues"]))


if __name__ == "__main__":
    sys.exit(main())
