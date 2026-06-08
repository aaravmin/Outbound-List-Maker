#!/usr/bin/env python3
"""Build a sourced, prioritized outbound contact list from seeds + a config.

Reads a YAML config and data/current_contacts.csv, cleans and validates the rows, then
writes usable rows to data/outbound_targets.csv, incomplete rows to
data/missing_data.csv, and regenerates the two docs.

Usage:
    python scripts/build_targets.py [config/outbound_list.yaml]
"""

import argparse
import csv
import sys
from collections import Counter, OrderedDict
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install it with: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent

# Fields that must be present for a row to be considered usable/exportable.
IMPORTANT_FIELDS = [
    "company",
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

PRIORITY_MAP = {
    "high": "High", "h": "High",
    "medium": "Medium", "med": "Medium", "m": "Medium",
    "low": "Low", "l": "Low",
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_seeds(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def norm(value):
    return (value or "").strip()


def standardize_category(value, categories):
    """Match a raw category to a config category, case/spacing-insensitive."""
    raw = norm(value)
    if not raw:
        return ""

    def key(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())

    lookup = {key(c): c for c in categories}
    return lookup.get(key(raw), raw)  # leave original if no match (gets flagged)


def standardize_priority(value):
    return PRIORITY_MAP.get(norm(value).lower(), "")


def main():
    parser = argparse.ArgumentParser(description="Build outbound target list.")
    parser.add_argument(
        "config",
        nargs="?",
        default=str(ROOT / "config" / "outbound_list.yaml"),
        help="Path to the YAML config (default: config/outbound_list.yaml)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    stats = build(config_path)

    # --- Console summary ---------------------------------------------------
    print(f"Config:           {config_path.name}")
    print(f"Rows reviewed:    {stats['total_reviewed']}")
    print(f"Rows exported:    {stats['exported']}  -> data/outbound_targets.csv")
    print(f"Incomplete rows:  {stats['incomplete']}  -> data/missing_data.csv")
    print(f"Duplicate contacts removed: {stats['duplicates_removed']}")
    print(f"Target count:     {stats['target_count']}")
    print("Docs written:     docs/priorities_and_specs.md, docs/research_log.md")


def build(config_path):
    """Run the full pipeline. Returns a stats dict. Used by CLI and web app."""
    config = load_config(config_path)
    output_columns = config["output_columns"]
    categories = config["scope"]["categories"]

    contacts_path = ROOT / "data" / "current_contacts.csv"
    if not contacts_path.exists():
        raise FileNotFoundError(f"Contacts file not found: {contacts_path}")

    rows, fieldnames = read_seeds(contacts_path)

    # --- Validate required columns -----------------------------------------
    missing_cols = [c for c in output_columns if c not in fieldnames]
    if missing_cols:
        raise ValueError(
            "current_contacts.csv is missing required columns: " + ", ".join(missing_cols)
        )

    total_reviewed = len(rows)

    # --- Clean, standardize, dedupe ----------------------------------------
    seen = {}
    duplicates_removed = []
    cleaned = []

    for row in rows:
        clean = {col: norm(row.get(col)) for col in output_columns}
        clean["category"] = standardize_category(clean["category"], categories)
        clean["priority"] = standardize_priority(clean["priority"])

        company_key = clean["company"].lower()
        contact_key = clean.get("contact_name", "").lower()
        title_key = clean.get("title", "").lower()
        if not company_key:
            clean["_missing"] = ["company"]  # will route to missing_data
            cleaned.append(clean)
            continue

        dedupe_key = (company_key, contact_key or title_key)
        if dedupe_key in seen:
            duplicates_removed.append(
                f"{clean.get('contact_name') or clean.get('title') or 'contact'} at {clean['company']}"
            )
            continue
        seen[dedupe_key] = True
        cleaned.append(clean)

    # --- Split usable vs incomplete ----------------------------------------
    usable = []
    incomplete = []

    for clean in cleaned:
        missing = clean.pop("_missing", [])
        missing += [f for f in IMPORTANT_FIELDS if not clean.get(f)]
        if missing:
            row_out = {col: clean.get(col, "") for col in output_columns}
            row_out["missing_fields"] = ", ".join(sorted(set(missing)))
            incomplete.append(row_out)
        else:
            usable.append({col: clean.get(col, "") for col in output_columns})

    # --- Write outputs -----------------------------------------------------
    write_csv(ROOT / "data" / "outbound_targets.csv", output_columns, usable)
    write_csv(
        ROOT / "data" / "missing_data.csv",
        output_columns + ["missing_fields"],
        incomplete,
    )

    write_priorities_and_specs(config)
    write_research_log(
        config=config,
        total_reviewed=total_reviewed,
        usable=usable,
        incomplete=incomplete,
        duplicates_removed=duplicates_removed,
    )

    return {
        "total_reviewed": total_reviewed,
        "exported": len(usable),
        "incomplete": len(incomplete),
        "duplicates_removed": len(duplicates_removed),
        "target_count": config["target_count"],
        "usable": usable,
        "incomplete_rows": incomplete,
        "output_columns": output_columns,
    }


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def write_priorities_and_specs(config):
    ctx = config["company_context"]
    scope = config["scope"]
    ps = config.get("priority_scoring", {})
    lines = []

    def bullets(items):
        return "\n".join(f"- {i}" for i in (items or []))

    lines.append(f"# {config['project_name']} — Priorities & Specs\n")

    lines.append("## 1. Objective")
    lines.append(
        f"Build a sourced, prioritized outbound contact list of {config['target_count']} "
        f"contacts in {scope['geography']} for {ctx['company']}.\n"
    )

    lines.append("## 2. Company context")
    lines.append(f"- **Company:** {ctx['company']}")
    lines.append(f"- **Product:** {ctx['product_description']}")
    lines.append(f"- **Core problem:** {ctx['core_problem']}")
    lines.append("- **Relevant workflows:**")
    lines.append("\n".join(f"  - {w}" for w in ctx.get("relevant_workflows", [])))
    lines.append("")

    lines.append("## 3. Scope")
    lines.append(f"- **Geography:** {scope['geography']}\n")

    lines.append("## 4. Target categories")
    targets = config.get("category_targets", {})
    for cat in scope["categories"]:
        n = targets.get(cat)
        lines.append(f"- {cat}" + (f" — {n}" if n is not None else ""))
    lines.append("")

    lines.append("## 5. Required specifications")
    lines.append(bullets(config["specifications"].get("required")))
    lines.append("")

    lines.append("## 6. Exclusions")
    lines.append(bullets(config["specifications"].get("excluded")))
    lines.append("")

    lines.append("## 7. Features captured")
    lines.append(bullets(config.get("features_to_capture")))
    lines.append("")

    lines.append("## 8. Priority logic")
    for level in ("high", "medium", "low"):
        if ps.get(level):
            lines.append(f"**{level.capitalize()}**")
            lines.append(bullets(ps[level]))
            lines.append("")

    lines.append("## 9. Final output columns")
    lines.append(bullets(config["output_columns"]))
    lines.append("")

    out = ROOT / "docs" / "priorities_and_specs.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def write_research_log(config, total_reviewed, usable, incomplete,
                       duplicates_removed):
    by_category = Counter(r.get("category", "") for r in usable)
    by_priority = Counter(r.get("priority", "") for r in usable)

    lines = []
    lines.append(f"# {config['project_name']} — Research Log\n")
    lines.append(f"- **Date:** {date.today().isoformat()}")
    lines.append(f"- **Project:** {config['project_name']}")
    lines.append(f"- **Target count:** {config['target_count']}")
    lines.append(f"- **Total rows reviewed:** {total_reviewed}")
    lines.append(f"- **Rows exported:** {len(usable)}")
    lines.append(f"- **Missing-data rows:** {len(incomplete)}")
    lines.append(f"- **Duplicate contacts removed:** {len(duplicates_removed)}")
    lines.append("")

    lines.append("## Counts by category")
    for cat in config["scope"]["categories"]:
        target = config.get("category_targets", {}).get(cat)
        got = by_category.get(cat, 0)
        suffix = f" / {target}" if target is not None else ""
        lines.append(f"- {cat}: {got}{suffix}")
    # Any categories outside the config scope (non-standardized)
    extra = [c for c in by_category if c not in config["scope"]["categories"]]
    for cat in extra:
        label = cat or "(blank)"
        lines.append(f"- {label}: {by_category[cat]}  (not in config scope)")
    lines.append("")

    lines.append("## Counts by priority")
    for level in ("High", "Medium", "Low"):
        lines.append(f"- {level}: {by_priority.get(level, 0)}")
    blank_pri = by_priority.get("", 0)
    if blank_pri:
        lines.append(f"- (blank): {blank_pri}")
    lines.append("")

    if duplicates_removed:
        lines.append("## Duplicate contacts removed")
        for name in duplicates_removed:
            lines.append(f"- {name}")
        lines.append("")

    lines.append("## Manual review checklist")
    lines.append("- [ ] Every exported row is a person, not just a company.")
    lines.append("- [ ] Every exported row has a person name and email.")
    lines.append("- [ ] Every exported row has a working source_url.")
    lines.append("- [ ] Every contact is relevant to the company's buying workflow.")
    lines.append("- [ ] No contact names or emails were invented or guessed.")
    lines.append("- [ ] why_relevant is one direct sentence, no filler.")
    lines.append("- [ ] Categories match the config scope.")
    lines.append("- [ ] Priorities reflect the scoring logic, not gut feel.")
    lines.append("- [ ] Category counts are close to category_targets.")
    lines.append("- [ ] Excluded-type firms were actually excluded.")
    lines.append("- [ ] Review data/missing_data.csv before discarding rows.")
    lines.append("")

    out = ROOT / "docs" / "research_log.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
