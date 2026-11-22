"""Normalize review observations and link them to versioned directory entries."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal

from .providers import PRICE


def normalized(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def observation_rows(report):
    centers = {(c["location_id"], c["source_url"]): c for c in report["centers"]}
    for row in report["offers"]:
        center = centers[row["location_id"], row["source_url"]]
        yield "offer", center, row, row["scope"], row["amount_cents"], row["price_type"]
    for row in report.get("review_candidates", []):
        label, amount, kind = candidate_price(row)
        yield "candidate", {}, row, label, amount, kind


def candidate_price(row):
    label = row["exam"]
    evidence = row["evidence"].strip()
    amount = row.get("amount_cents")
    kind = row.get("price_type") or ("cash_column" if amount is not None else "unconfirmed")
    # Recover standalone exam/amount pairs from older reports. Keep fees,
    # exclusions and source wording available for the review decision.
    match = re.fullmatch(r"(?:/\s*)?([^$|:]{2,150}?)\s*(?:\||:)\s*(Starting at\s+)?(" + PRICE + r")", evidence, re.I)
    if match and re.search(r"\b(?:MRI|CT)\b", match[1], re.I):
        label = match[1].strip()
        amount = int(Decimal(match[3][1:].replace(",", "")) * 100)
        kind = "starting" if match[2] else "listed"
    return label, amount, kind


def stage(db, report, bindings=None):
    """Store one report atomically within the caller's transaction."""
    active = db.execute("SELECT value FROM settings WHERE key='active_batch'").fetchone()
    if not active:
        raise ValueError("Select a directory database with a published batch")
    batch = active["value"]
    by_address = defaultdict(list)
    keys = set()
    for record in db.execute("SELECT entry_key,payload FROM entries WHERE batch_id=?", (batch,)):
        key = record["entry_key"]
        keys.add(key)
        entry = json.loads(record["payload"])
        if entry.get("location_type") == "site" and len(entry.get("locations", [])) == 1:
            by_address[(entry.get("source"), normalized(entry.get("coverage", "")))].append(key)
    bindings = bindings or {}
    totals = Counter()
    for kind, center, row, exam, amount, price_type in observation_rows(report):
        url = row["source_url"]
        checked = row["checked_at"]
        evidence = row["evidence"]
        identity = json.dumps([batch, url, checked, kind, row, center], sort_keys=True)
        ident = hashlib.sha256(identity.encode()).hexdigest()
        address = center.get("address")
        matches = by_address.get((url, normalized(address)), []) if address else []
        key = matches[0] if len(matches) == 1 else None
        if ident in bindings:
            key = bindings[ident]
            if key not in keys:
                raise ValueError("Reviewed entry_key is absent from this directory batch")
        previous = db.execute("SELECT entry_key FROM scraper_observations WHERE observation_id=?", (ident,)).fetchone()
        if previous and previous["entry_key"] and key and previous["entry_key"] != key:
            raise ValueError("Observation is already linked to another center")
        db.execute("""INSERT INTO scraper_observations
            (observation_id,active_batch_id,entry_key,source_url,checked_at,record_kind,exam,
             amount_cents,price_type,evidence,source_record) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(observation_id) DO UPDATE SET entry_key=COALESCE(scraper_observations.entry_key,excluded.entry_key)""",
            (ident,batch,key,url,checked,kind,exam,amount,price_type,evidence,json.dumps({"center":center,"row":row})))
        totals["linked" if key or (previous and previous["entry_key"]) else "needs_center"] += 1
    return dict(totals)
