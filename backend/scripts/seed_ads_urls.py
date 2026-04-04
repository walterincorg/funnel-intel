"""One-time script to populate ads_library_url on competitors from the CSV.

Usage:
    python -m backend.scripts.seed_ads_urls
"""

import csv
from pathlib import Path

from backend.db import get_db

CSV_PATH = Path(__file__).parent.parent.parent / "TOP COMPETITORS kopija - Sheet1.csv"

# Map of CSV project names to the slug/name we'd expect in the DB.
# The match is case-insensitive on the competitor name.


def main():
    db = get_db()

    # Load all competitors from DB
    comps = db.table("competitors").select("id, name, ads_library_url").execute().data
    comp_by_name = {c["name"].strip().lower(): c for c in comps}

    # Parse CSV
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        updated = 0
        skipped = 0

        for row in reader:
            if len(row) < 2:
                continue
            project_name = row[0].strip()
            ads_url = row[1].strip()

            if not project_name or not ads_url:
                continue

            # Try to match by name (case-insensitive)
            comp = comp_by_name.get(project_name.lower())
            if not comp:
                print(f"  SKIP (not in DB): {project_name}")
                skipped += 1
                continue

            if comp.get("ads_library_url"):
                print(f"  SKIP (already set): {comp['name']}")
                skipped += 1
                continue

            db.table("competitors").update({
                "ads_library_url": ads_url,
            }).eq("id", comp["id"]).execute()

            print(f"  SET: {comp['name']}")
            updated += 1

    print(f"\nDone. Updated: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
