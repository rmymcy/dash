#!/usr/bin/env python3
"""Preprocess USDA SR28 raw data into a compact CSV for the diets build."""
import csv, re
from pathlib import Path

SRC = Path("/tmp")
OUT = Path("data/usda_sr28.csv")

# Nutrient IDs we care about, mapped to our column names
NUTRIENTS = {
    "208": "kcal",       # Energy (kcal)
    "204": "fat",        # Total lipid (g)
    "606": "satfat",     # Saturated fat (g)
    "605": "transfat",   # Trans fat (g)
    "601": "chol",       # Cholesterol (mg)
    "307": "sodium",     # Sodium (mg)
    "205": "carbs",      # Carbohydrate (g)
    "269": "sugar",      # Sugars total (g)
    "291": "fiber",      # Fiber total (g)
    "203": "protein",    # Protein (g)
}

def parse_line(line):
    """SR28 format: ~field~^~field~^numeric^..."""
    # Field separator: ^ (between fields). Strings are wrapped in ~..~.
    return [f.strip("~") for f in line.rstrip("\r\n").split("^")]


# 1. Load food descriptions
foods = {}
with open(SRC / "FOOD_DES.txt", encoding="latin-1") as f:
    for line in f:
        fields = parse_line(line)
        ndb = fields[0]
        long_desc = fields[2]
        # Long desc is human-readable; trim
        foods[ndb] = {"name": long_desc, "fdgrp": fields[1]}
print(f"Loaded {len(foods)} food descriptions")

# 2. Stream nutrient data and pick out the ones we care about
for ndb, food in foods.items():
    food["nut"] = {}

with open(SRC / "NUT_DATA.txt", encoding="latin-1") as f:
    for line in f:
        # Quick filter before parsing
        fields = parse_line(line)
        ndb, nutr_no, val = fields[0], fields[1], fields[2]
        if nutr_no not in NUTRIENTS:
            continue
        if ndb not in foods:
            continue
        try:
            foods[ndb]["nut"][NUTRIENTS[nutr_no]] = float(val)
        except ValueError:
            pass

# 3. Food group lookup (categories)
fdgrp = {}
fdgrp_path = SRC / "FD_GROUP.txt"
if fdgrp_path.exists():
    with open(fdgrp_path, encoding="latin-1") as f:
        for line in f:
            fields = parse_line(line)
            fdgrp[fields[0]] = fields[1]


# 4. Emit
COLS = ["ndb", "name", "category", "kcal", "fat", "satfat", "transfat", "chol",
        "sodium", "carbs", "sugar", "fiber", "protein"]
written = 0
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(COLS)
    for ndb, food in foods.items():
        if "kcal" not in food["nut"]:
            continue
        cat = fdgrp.get(food["fdgrp"], "Generic Food")
        row = [ndb, food["name"], cat] + [food["nut"].get(c, "") for c in COLS[3:]]
        w.writerow(row)
        written += 1
print(f"Wrote {written} foods to {OUT}")
