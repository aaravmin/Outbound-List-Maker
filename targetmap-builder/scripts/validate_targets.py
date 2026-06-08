#!/usr/bin/env python3
"""Validate data/current_contacts.csv against a config and print a concise report.

This does not modify any files. Run it before build_targets.py to catch
problems early.

Usage:
    python scripts/validate_targets.py [config/outbound_list.yaml]
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install it with: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_NON_EMPTY = [
    "website",
    "geography_relevance",
    "contact_name",
    "contact_email",
    "title",
    "firm_type",
    "why_relevant",
    "priority",
    "source_url",
]

VALID_PRIORITIES = {"high", "medium", "low"}

# generic first.last@domain style address — a common sign of a guessed email
GUESSED_EMAIL = re.compile(r"^[a-z]+[._][a-z]+@", re.IGNORECASE)


def norm(v):
    return (v or "").strip()


def cat_key(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def main():
    parser = argparse.ArgumentParser(description="Validate current_contacts.csv.")
    parser.add_argument(
        "config",
        nargs="?",
        default=str(ROOT / "config" / "outbound_list.yaml"),
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    output_columns = config["output_columns"]
    valid_cat_keys = {cat_key(c): c for c in config["scope"]["categories"]}

    contacts_path = ROOT / "data" / "current_contacts.csv"
    if not contacts_path.exists():
        sys.exit(f"Contacts file not found: {contacts_path}")

    with open(contacts_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        fieldnames = reader.fieldnames or []

    errors = []
    warnings = []

    # --- Columns -----------------------------------------------------------
    missing_cols = [c for c in output_columns if c not in fieldnames]
    if missing_cols:
        errors.append("Missing required columns: " + ", ".join(missing_cols))
        _report(config, len(rows), errors, warnings)
        sys.exit(1)

    # --- Per-row checks ----------------------------------------------------
    seen = {}
    duplicates = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        company = norm(row.get("company"))
        label = company or f"row {i}"

        if not company:
            errors.append(f"row {i}: blank company")
        else:
            contact = norm(row.get("contact_name")) or norm(row.get("title")) or "contact"
            ck = (company.lower(), contact.lower())
            if ck in seen:
                duplicates.append(f"{contact} at {company}")
            seen[ck] = True

        # category
        cat = norm(row.get("category"))
        if cat and cat_key(cat) not in valid_cat_keys:
            errors.append(f"{label}: category '{cat}' not in config scope")

        # priority
        pri = norm(row.get("priority")).lower()
        if pri and pri not in VALID_PRIORITIES:
            errors.append(f"{label}: priority '{row.get('priority')}' invalid")

        # required non-empty fields
        for field in REQUIRED_NON_EMPTY:
            if not norm(row.get(field)):
                warnings.append(f"{label}: missing {field}")

        # likely-guessed email (generic pattern + no supporting note)
        email = norm(row.get("contact_email"))
        notes = norm(row.get("notes")).lower()
        if email and GUESSED_EMAIL.match(email) and "source" not in notes:
            warnings.append(
                f"{label}: email '{email}' looks guessed (generic pattern, "
                "no source note)"
            )

    for name in duplicates:
        errors.append(f"duplicate contact: {name}")

    _report(config, len(rows), errors, warnings)
    sys.exit(1 if errors else 0)


def _report(config, total, errors, warnings):
    print(f"Validation report — {config['project_name']}")
    print(f"Config rows reviewed: {total}")
    print("-" * 60)

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    else:
        print("ERRORS: none")

    print()
    if warnings:
        warn_counts = Counter(w.split(":", 1)[-1].strip().split("(")[0].strip()
                              for w in warnings)
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
        print()
        print("  Warning summary:")
        for kind, n in warn_counts.most_common():
            print(f"    {n}x  {kind}")
    else:
        print("WARNINGS: none")

    print("-" * 60)
    print("PASS" if not errors else "FAIL")


if __name__ == "__main__":
    main()
