"""Build the body-part view using only the individual offer's exam label."""
import re


# Stable spreadsheet columns. Unlisted exams remain available in Other exams.
PARTS = {
    "Knee": r"knees?",
    "Brain": r"brain",
    "Lumbar spine": r"lumbar(?: spine)?|l[- ]spine",
    "Shoulder": r"shoulders?",
    "Abdomen & pelvis": r"abdomen\s*(?:and|&|/)\s*pelvis|abdominopelvic",
    "Breast": r"breasts?",
    "Lung": r"lungs?",
    "Prostate": r"prostate",
    "Whole body": r"(?:whole|full)[ -]body",
}
GENERAL = "General"
OTHER = "Other exams"
COLUMNS = (GENERAL, *PARTS, OTHER)
PART_PATTERNS = {name: re.compile(rf"\b(?:{pattern})\b", re.I) for name, pattern in PARTS.items()}
OVERVIEW_FIELDS = ("center", "location_id", "address", "modality", "contrast", "source_url", *COLUMNS, "cell_details")
EXCLUSION = re.compile(r"\b(?:exclud(?:es?|ing)|except(?: for)?|other than|not including)\b", re.I)
CONTRAST = re.compile(
    r"\b(?:(?:with(?:out)?|w/o|w/)(?:\s*(?:and|&|/)\s*(?:with(?:out)?|w/o|w/))?\s+contrast|non[- ]?contrast)\b",
    re.I)
SCAN = re.compile(r"\b(?:PET\s*/\s*CT|MRI|CT|scans?|general|standard|of|the)\b", re.I)
QUALIFIER = re.compile(r"\b(?:left|right|bilateral|unilateral|screening|diagnostic|arthrogram|"
                       r"plus|with|3d|rendering|neuroquant|guided|biopsy|joint|low|dose|cancer)\b", re.I)


def split_exclusions(exam):
    # A parenthetical exception may precede the included anatomy or protocol.
    removed = []
    def remove(match):
        removed.append(match[1].strip())
        return " "
    positive = re.sub(r"\(\s*" + EXCLUSION.pattern + r"\s+([^()]*)\)", remove, exam, flags=re.I)
    parts = EXCLUSION.split(positive, maxsplit=1)
    if len(parts) == 2:
        removed.append(parts[1].strip(" ().,;"))
    return " ".join(parts[0].strip(" ().,;").split()), "; ".join(removed)


def exam_scope(exam):
    """Allow only an explicit, single region. Keep unknown/composite labels intact."""
    label, exclusion = split_exclusions(exam)
    rest = SCAN.sub(" ", CONTRAST.sub(" ", label))
    body = ""
    column = OTHER
    if not re.search(r"\w", rest) and label:
        column = GENERAL
    else:
        for name, pattern in PART_PATTERNS.items():
            if pattern.search(rest) and not re.search(r"\w", QUALIFIER.sub(" ", pattern.sub(" ", rest))):
                body = column = name
                break
    return {"body_part": body, "exam_variant": label, "exclusions": exclusion, "body_part_column": column}


def overview(rows):
    groups = {}
    seen = set()
    for index, row in enumerate(rows):
        # An unresolved chain page has no established branch, even if its title
        # happens to match another page. Never group it with a resolved location.
        site = ("branch", row["location_id"]) if row["location_id"] else ("page", row["source_url"])
        key = (site, row["modality"], row["contrast"])
        identity = (key, row["exam"], row["price_type"], row["price_usd"], row["max_price_usd"],
                    row["payment_terms"], row["source_url"], row["evidence"])
        if identity in seen:
            continue
        seen.add(identity)
        if key not in groups:
            groups[key] = {k: row[k] for k in (
                "center", "location_id", "address", "modality", "contrast", "source_url")}
            groups[key]["cells"] = {}
        groups[key]["cells"].setdefault(row["body_part_column"], []).append(index)
    return {"columns": list(COLUMNS), "rows": list(groups.values())}


def overview_records(view, offers):
    """Numeric cells for unique observations. Retain qualifiers and collisions beside them."""
    for group in view["rows"]:
        record = {k: v for k, v in group.items() if k != "cells"}
        notes = []
        for column, indexes in group["cells"].items():
            if len(indexes) == 1:
                record[column] = offers[indexes[0]]["price_usd"]
            details = []
            for index in indexes:
                row = offers[index]
                amount = f'{row["price_usd"]:.2f}'
                if row["max_price_usd"] is not None:
                    amount += f'–{row["max_price_usd"]:.2f}'
                details.append(f'{row["exam"]} ({row["price_type"]}, USD {amount})')
            notes.append(f'{column}: ' + ' | '.join(details))
        record["cell_details"] = ' / '.join(notes)
        yield record
