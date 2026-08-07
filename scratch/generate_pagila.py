"""Generate tests/eval/pagila_staff.csv if missing."""

import csv
import random
from pathlib import Path

DEST = Path("tests/eval/pagila_staff.csv")

FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles", "Rory", "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Zaentz", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson"]

if not DEST.exists():
    random.seed(20260721)
    rows = []
    for i in range(1, 1501):
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        email = f"{fn.lower()}.{ln.lower()}{i}@pagilastaff.org"
        rows.append({"staff_id": str(i), "first_name": fn, "last_name": ln, "email": email})

    with DEST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["staff_id", "first_name", "last_name", "email"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Created {DEST} with {len(rows)} rows.")
else:
    print(f"{DEST} already exists.")
