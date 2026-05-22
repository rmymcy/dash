#!/usr/bin/env python3
"""
Build diets.html: a self-contained single-file searchable nutrition browser.

Pulls together two open datasets shipped under data/:
  - openfood.csv  : Open Food Facts export (per-100g nutrition)
  - products-3000.csv : OpenLabel community nutrition labels (per-serving)

Both are normalized to per-100g values and emitted into a single HTML file
that supports multi-criteria filtering with range sliders + quick chips,
plus a live insight panel.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = ROOT / "diets.html"


# --- Loaders -----------------------------------------------------------------

def _f(v: str) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_openfood(path: Path) -> list[dict]:
    """Open Food Facts export: nutrient values per 100g (sodium in g)."""
    items: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kcal = _f(r.get("energy-kcal_100g"))
            if kcal is None:
                continue
            name = (r.get("product_name") or "").strip()
            if not name:
                continue
            cat = (r.get("categories_en") or "").split(",")[0].strip() or "Uncategorized"
            # sodium in g/100g -> mg/100g; cholesterol same
            sodium_g = _f(r.get("sodium_100g"))
            chol_g = _f(r.get("cholesterol_100g"))
            items.append({
                "name": name.title()[:120],
                "brand": "",
                "cat": cat,
                "src": "OFF",
                "kcal": round(kcal, 1),
                "fat": _f(r.get("fat_100g")),
                "satfat": _f(r.get("saturated-fat_100g")),
                "transfat": _f(r.get("trans-fat_100g")),
                "chol": round(chol_g * 1000, 1) if chol_g is not None else None,
                "sodium": round(sodium_g * 1000, 1) if sodium_g is not None else None,
                "carbs": _f(r.get("carbohydrates_100g")),
                "sugar": _f(r.get("sugars_100g")),
                "fiber": _f(r.get("fiber_100g")),
                "protein": _f(r.get("proteins_100g")),
            })
    return items


def _parse_serving_grams(s: str) -> float | None:
    """Best-effort grams from a serving-size string like '28g', '2 tbsp (30g)', '1 cup (240ml)'."""
    if not s:
        return None
    s = s.lower()
    m = re.search(r"([\d.]+)\s*(g|ml)\b", s)
    if m:
        return float(m.group(1))  # treat ml ~ g for liquids (rough)
    m = re.search(r"([\d.]+)\s*oz\b", s)
    if m:
        return float(m.group(1)) * 28.3495
    return None


def load_openlabel(path: Path) -> list[dict]:
    """OpenLabel CSV: per-serving values. Convert to per-100g."""
    items: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kcal = _f(r.get("Calories"))
            if kcal is None:
                continue
            grams = _parse_serving_grams(r.get("Serving Size") or r.get("Size") or "")
            if not grams or grams < 5:
                continue
            scale = 100.0 / grams

            name = (r.get("Name") or "").strip()
            brand = (r.get("Brand Name") or "").strip()
            if not name:
                continue

            def s(k):
                v = _f(r.get(k))
                return round(v * scale, 2) if v is not None else None

            items.append({
                "name": name[:120],
                "brand": brand,
                "cat": "Branded Food",
                "src": "OL",
                "kcal": round(kcal * scale, 1),
                "fat": s("Fat (g)"),
                "satfat": s("Saturated Fat (g)"),
                "transfat": s("Trans Fat (g)"),
                "chol": s("Cholesterol (mg)"),
                "sodium": s("Sodium (mg)"),
                "carbs": s("Carbohydrate (g)"),
                "sugar": s("Sugars (g)"),
                "fiber": s("Fiber (g)"),
                "protein": s("Protein (g)"),
            })
    return items


# --- Dedup / cleanup ---------------------------------------------------------

def dedupe(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for it in items:
        key = (it["name"].lower(), it.get("brand", "").lower())
        # Drop wildly implausible kcal
        if it["kcal"] is None or it["kcal"] > 900 or it["kcal"] < 0:
            continue
        existing = seen.get(key)
        if not existing:
            seen[key] = it
        else:
            # Prefer the row with more populated nutrients
            def score(x):
                return sum(1 for k in ("fat","satfat","sodium","sugar","fiber","protein") if x.get(k) is not None)
            if score(it) > score(existing):
                seen[key] = it
    return list(seen.values())


# --- HTML --------------------------------------------------------------------

# FDA Daily Values (2020 update). Used for the insight panel.
DAILY_VALUES = {
    "kcal":     {"label": "Calories",       "unit": "kcal", "dv": 2000, "direction": "limit",
                 "note": "FDA Daily Value: 2,000 kcal. Reference only; needs vary by body size and activity."},
    "fat":      {"label": "Total Fat",      "unit": "g",   "dv": 78,   "direction": "limit",
                 "note": "Daily Value 78 g (35% of 2000 kcal). Type matters more than quantity — favor unsaturated."},
    "satfat":   {"label": "Saturated Fat",  "unit": "g",   "dv": 20,   "direction": "limit",
                 "note": "Limit to <10% of calories (~20 g). Raises LDL cholesterol."},
    "transfat": {"label": "Trans Fat",      "unit": "g",   "dv": 0,    "direction": "avoid",
                 "note": "No safe level. FDA banned added trans fats in 2018; trace amounts still appear."},
    "chol":     {"label": "Cholesterol",    "unit": "mg",  "dv": 300,  "direction": "limit",
                 "note": "Found in animal products. Daily Value 300 mg. Dietary impact on blood cholesterol varies."},
    "sodium":   {"label": "Sodium",         "unit": "mg",  "dv": 2300, "direction": "limit",
                 "note": "Daily Value 2,300 mg. Excess sodium raises blood pressure. Average US intake ~3,400 mg."},
    "carbs":    {"label": "Total Carbs",    "unit": "g",   "dv": 275,  "direction": "limit",
                 "note": "Daily Value 275 g. Quality (fiber, whole grains) matters more than total."},
    "sugar":    {"label": "Total Sugars",   "unit": "g",   "dv": 50,   "direction": "limit",
                 "note": "Added-sugars DV is 50 g (10% of calories). Total sugars includes naturally-occurring (e.g. milk, fruit)."},
    "fiber":    {"label": "Fiber",          "unit": "g",   "dv": 28,   "direction": "min",
                 "note": "Daily Value 28 g. Average US intake is only ~15 g — aim higher."},
    "protein":  {"label": "Protein",        "unit": "g",   "dv": 50,   "direction": "min",
                 "note": "Daily Value 50 g. RDA is 0.8 g/kg body weight — ~56 g for a 70 kg adult."},
}


def write_html(items: list[dict]) -> None:
    payload = {
        "items": items,
        "nutrients": DAILY_VALUES,
        "generated": len(items),
    }
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    html = HTML_TEMPLATE.replace("__DATA__", data_json)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size/1024:.1f} KB, {len(items)} items)")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diets — nutrition filter</title>
<style>
  :root {
    --bg: #fafaf7;
    --panel: #ffffff;
    --ink: #1a1a1a;
    --muted: #6b6b6b;
    --line: #e3e3df;
    --accent: #2f7a4a;
    --accent-soft: #e6f1ea;
    --warn: #b65b00;
    --warn-soft: #fdeede;
    --chip-on: #2f7a4a;
    --chip-off: #f0f0ec;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { display: grid; grid-template-columns: 320px 1fr 340px;
    grid-template-areas: "head head head" "filt main insi";
    min-height: 100vh; }
  @media (max-width: 1100px) {
    body { grid-template-columns: 280px 1fr; grid-template-areas: "head head" "filt main"; }
    #insight { display: none; }
  }
  @media (max-width: 760px) {
    body { grid-template-columns: 1fr; grid-template-areas: "head" "filt" "main"; }
    #filters { position: static; max-height: none; border-right: none;
      border-bottom: 1px solid var(--line); }
    #toggleFilters { display: inline-block; }
  }
  header { grid-area: head; }
  #filters { grid-area: filt; }
  #main    { grid-area: main; }
  #insight { grid-area: insi; }

  header { background: var(--panel); border-bottom: 1px solid var(--line);
    padding: 12px 18px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; letter-spacing: 0.2px; }
  header .meta { color: var(--muted); font-size: 12px; }
  header .search { flex: 1; min-width: 180px; max-width: 520px; }
  header input[type=search] { width: 100%; padding: 7px 10px; border: 1px solid var(--line);
    border-radius: 6px; background: #fff; font-size: 14px; }
  #toggleFilters { display: none; padding: 6px 12px; background: var(--accent);
    color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 13px;
    font-weight: 500; }
  #toggleFilters.active { background: var(--ink); }
  @media (max-width: 760px) {
    #filters.collapsed { display: none; }
  }

  #filters { background: var(--panel); border-right: 1px solid var(--line); padding: 14px;
    position: sticky; top: 49px; max-height: calc(100vh - 49px); overflow-y: auto; }
  #filters h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px;
    color: var(--muted); margin: 16px 0 6px; font-weight: 600; }
  #filters h2:first-child { margin-top: 0; }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { padding: 4px 10px; border-radius: 999px; background: var(--chip-off);
    border: 1px solid var(--line); cursor: pointer; font-size: 12px; user-select: none; }
  .chip.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  .chip[data-kind=warn].on { background: var(--warn); border-color: var(--warn); }

  .slider { margin: 8px 0 14px; }
  .slider .row { display: flex; justify-content: space-between; align-items: baseline; }
  .slider label { font-weight: 600; font-size: 13px; }
  .slider .val { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .slider .dv { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .slider input[type=range] { width: 100%; margin: 4px 0 0; accent-color: var(--accent); }
  .slider .dual { position: relative; height: 28px; }
  .slider .dual input { position: absolute; left: 0; right: 0; top: 6px;
    pointer-events: none; -webkit-appearance: none; background: transparent; }
  .slider .dual input::-webkit-slider-thumb { pointer-events: auto; -webkit-appearance: none;
    width: 16px; height: 16px; border-radius: 50%; background: var(--accent); cursor: pointer;
    border: 2px solid #fff; box-shadow: 0 0 0 1px var(--accent); }
  .slider .dual input::-moz-range-thumb { pointer-events: auto; width: 14px; height: 14px;
    border-radius: 50%; background: var(--accent); cursor: pointer; border: 2px solid #fff; }
  .slider .dual .track { position: absolute; left: 0; right: 0; top: 13px; height: 4px;
    background: var(--line); border-radius: 2px; }
  .slider .dual .fill { position: absolute; top: 13px; height: 4px;
    background: var(--accent); border-radius: 2px; }

  #main { padding: 14px 18px; min-width: 0; }
  #summary { display: flex; gap: 16px; align-items: baseline; margin-bottom: 10px; flex-wrap: wrap; }
  #summary .count { font-size: 18px; font-weight: 600; }
  #summary .of { color: var(--muted); }
  #summary select { padding: 4px 6px; border: 1px solid var(--line); border-radius: 4px; }
  #summary button { padding: 4px 10px; border: 1px solid var(--line); background: #fff;
    border-radius: 4px; cursor: pointer; font-size: 12px; }
  #summary button:hover { background: var(--accent-soft); border-color: var(--accent); }

  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  #cards { display: none; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px; }
  .card .name { font-weight: 600; font-size: 14px; margin-bottom: 2px; }
  .card .meta { color: var(--muted); font-size: 11px; margin-bottom: 6px; }
  .card .grid { display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 4px 12px; font-size: 12px; }
  .card .grid .k { color: var(--muted); }
  .card .grid .v { font-variant-numeric: tabular-nums; text-align: right; }
  .card .grid .v.zero { color: var(--accent); font-weight: 500; }
  .card .grid .v.over { color: var(--warn); }
  .card .grid .row { display: flex; justify-content: space-between;
    padding: 2px 0; border-bottom: 1px dotted var(--line); }

  table { width: 100%; border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  thead th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--muted); padding: 8px 10px; background: #f6f6f3; border-bottom: 1px solid var(--line);
    cursor: pointer; user-select: none; font-weight: 600; white-space: nowrap; }
  thead th.num { text-align: right; }
  thead th:hover { background: #efefea; }
  thead th .arrow { color: var(--accent); margin-left: 2px; }
  tbody td { padding: 7px 10px; border-bottom: 1px solid #f0f0ec; font-size: 13px;
    font-variant-numeric: tabular-nums; }
  tbody td.num { text-align: right; }
  tbody td.zero { color: var(--accent); font-weight: 500; }
  tbody td.over { color: var(--warn); }
  tbody tr:hover { background: var(--accent-soft); }
  tbody td.name { font-weight: 500; max-width: 360px; overflow: hidden;
    white-space: nowrap; text-overflow: ellipsis; }
  tbody td.brand { color: var(--muted); font-size: 12px; }
  tbody td.cat { color: var(--muted); font-size: 11px; }

  #pager { padding: 10px 0; display: flex; gap: 8px; align-items: center; color: var(--muted); font-size: 12px; }
  #pager button { padding: 4px 10px; border: 1px solid var(--line); background: #fff;
    border-radius: 4px; cursor: pointer; }
  #pager button:disabled { opacity: 0.4; cursor: default; }

  #insight { background: var(--panel); border-left: 1px solid var(--line); padding: 14px;
    position: sticky; top: 49px; max-height: calc(100vh - 49px); overflow-y: auto; }
  #insight h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px;
    color: var(--muted); margin: 16px 0 6px; font-weight: 600; }
  #insight h2:first-child { margin-top: 0; }
  .stat { display: flex; justify-content: space-between; padding: 4px 0;
    border-bottom: 1px dotted var(--line); font-size: 13px; }
  .stat .k { color: var(--muted); }
  .stat .v { font-variant-numeric: tabular-nums; }
  .nutrient-card { padding: 10px; margin-bottom: 8px; background: #fbfbf8;
    border: 1px solid var(--line); border-radius: 6px; }
  .nutrient-card h3 { margin: 0 0 4px; font-size: 13px; font-weight: 600;
    display: flex; justify-content: space-between; }
  .nutrient-card .dv-line { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .nutrient-card .quartile { display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 4px; font-size: 10px; text-align: center; color: var(--muted); margin-bottom: 4px; }
  .nutrient-card .quartile div { padding: 4px 2px; background: #fff;
    border: 1px solid var(--line); border-radius: 3px; }
  .nutrient-card .quartile .num { font-size: 11px; font-weight: 600; color: var(--ink);
    font-variant-numeric: tabular-nums; }
  .nutrient-card .note { font-size: 11px; color: var(--muted); line-height: 1.4; }

  .legend { font-size: 11px; color: var(--muted); padding: 8px 0; }
  .legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); margin: 0 4px 0 8px; vertical-align: middle; }
  .legend .dot.warn { background: var(--warn); }

  /* Responsive overrides — kept last so they win the cascade */
  @media (max-width: 760px) {
    .table-wrap { display: none; }
    #cards { display: block; }
    #toggleFilters { display: inline-block; }
    #filters.collapsed { display: none; }
    #main { padding: 10px 12px; }
    header { padding: 10px 12px; gap: 8px; }
    header h1 { font-size: 15px; }
    header .meta { display: none; }
  }
</style>
</head>
<body>

<header>
  <h1>Diets</h1>
  <span class="meta" title="Open Food Facts + OpenLabel grocery items, common US-retail brands (Walmart, Kroger, Target). All values per 100 g.">
    <span id="totalCount">—</span> items · per 100 g
  </span>
  <div class="search">
    <input id="search" type="search" placeholder="Search by name, brand, category…">
  </div>
  <button id="toggleFilters">Filters</button>
</header>

<aside id="filters">
  <h2>Quick filters</h2>
  <div class="chips" id="quickChips">
    <span class="chip" data-kind="warn" data-quick="zerochol">Zero cholesterol</span>
    <span class="chip" data-kind="warn" data-quick="zerosugar">Zero sugar</span>
    <span class="chip" data-kind="warn" data-quick="zerotrans">Trans-fat free</span>
    <span class="chip" data-kind="warn" data-quick="lowsodium">Low sodium</span>
    <span class="chip" data-kind="warn" data-quick="lowfat">Low fat</span>
    <span class="chip" data-kind="warn" data-quick="lowsatfat">Low sat. fat</span>
    <span class="chip" data-quick="highprotein">High protein</span>
    <span class="chip" data-quick="highfiber">High fiber</span>
    <span class="chip" data-quick="lowcal">Low calorie</span>
  </div>

  <h2>Nutrient ranges <small style="color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(per 100g)</small></h2>
  <div id="sliders"></div>

  <h2>Category</h2>
  <select id="catFilter" style="width:100%;padding:5px;border:1px solid var(--line);border-radius:4px"></select>

  <h2> </h2>
  <button id="reset" style="width:100%;padding:7px;border:1px solid var(--line);background:#fff;border-radius:4px;cursor:pointer">Reset all filters</button>
  <button id="applyFilters" style="display:none;width:100%;padding:11px;margin-top:8px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px">Show <span id="applyCount">—</span> results</button>
</aside>

<section id="main">
  <div id="summary">
    <span><span class="count" id="resultCount">—</span> <span class="of">of <span id="totalCount2">—</span> items</span></span>
    <span>Sort: <select id="sort"></select></span>
    <button id="exportBtn">Export visible as CSV</button>
  </div>
  <div class="legend"><span class="dot"></span>= 0 · <span class="dot warn"></span>= over Daily Value</div>
  <div class="table-wrap">
    <table>
      <thead><tr id="tableHead"></tr></thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
  <div id="cards"></div>
  <div id="pager">
    <button id="prev">← prev</button>
    <span id="pageInfo"></span>
    <button id="next">next →</button>
  </div>
</section>

<aside id="insight">
  <h2>Insight — current selection</h2>
  <div class="stat"><span class="k">Items matched</span><span class="v" id="iCount">—</span></div>
  <div class="stat"><span class="k">Median calories</span><span class="v" id="iMedKcal">—</span></div>
  <div class="stat"><span class="k">Median sodium</span><span class="v" id="iMedSodium">—</span></div>
  <div class="stat"><span class="k">Median sugar</span><span class="v" id="iMedSugar">—</span></div>

  <h2>Nutrient detail</h2>
  <div id="nutrientCards"></div>
</aside>

<script>
const DATA = __DATA__;
const ITEMS = DATA.items;
const NUTRIENTS = DATA.nutrients;
const NUT_KEYS = Object.keys(NUTRIENTS);

// Per-key thresholds for the "low / high" quick chips (per 100g)
const QUICK = {
  zerochol:   it => it.chol === 0 || it.chol === null && false,
  zerosugar:  it => it.sugar === 0,
  zerotrans:  it => (it.transfat ?? 0) === 0,
  lowsodium:  it => it.sodium != null && it.sodium <= 140,    // FDA "low sodium" definition
  lowfat:     it => it.fat != null && it.fat <= 3,            // FDA "low fat"
  lowsatfat:  it => it.satfat != null && it.satfat <= 1,      // FDA "low saturated fat"
  lowcal:     it => it.kcal != null && it.kcal <= 40,         // FDA "low calorie"
  highprotein:it => it.protein != null && it.protein >= 10,   // FDA "high"/"good source"
  highfiber:  it => it.fiber != null && it.fiber >= 5,        // FDA "high fiber"
};

// Compute global max for each nutrient (for slider bounds)
function maxOf(key) {
  let m = 0;
  for (const it of ITEMS) {
    const v = it[key];
    if (v != null && v > m) m = v;
  }
  // round up to a sensible bound
  if (m < 10) return Math.ceil(m);
  if (m < 100) return Math.ceil(m/10)*10;
  return Math.ceil(m/100)*100;
}
const RANGES = Object.fromEntries(NUT_KEYS.map(k => [k, { min: 0, max: maxOf(k) }]));

// State
const state = {
  search: "",
  cat: "",
  quick: new Set(),
  range: Object.fromEntries(NUT_KEYS.map(k => [k, [0, RANGES[k].max]])),
  sort: "kcal",
  sortDir: "asc",
  page: 0,
  perPage: 50,
};

const $ = sel => document.querySelector(sel);
const el = (tag, props={}, kids=[]) => {
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids) e.append(kid);
  return e;
};

// --- Filter / sort -----------------------------------------------------------
function visible() {
  const q = state.search.toLowerCase();
  return ITEMS.filter(it => {
    if (q) {
      const hay = (it.name + " " + (it.brand||"") + " " + (it.cat||"")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (state.cat && it.cat !== state.cat) return false;
    for (const k of state.quick) if (!QUICK[k](it)) return false;
    for (const k of NUT_KEYS) {
      const [lo, hi] = state.range[k];
      const v = it[k];
      if (v == null) {
        // If user has tightened either bound, exclude unknowns
        if (lo > 0 || hi < RANGES[k].max) return false;
        continue;
      }
      if (v < lo || v > hi) return false;
    }
    return true;
  });
}

function sorted(arr) {
  const k = state.sort;
  const dir = state.sortDir === "asc" ? 1 : -1;
  return arr.slice().sort((a,b) => {
    if (k === "name") return dir * (a.name||"").localeCompare(b.name||"");
    const av = a[k], bv = b[k];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return dir * (av - bv);
  });
}

// --- Stats -------------------------------------------------------------------
function pct(arr, p) {
  if (!arr.length) return null;
  const sorted = arr.slice().sort((a,b) => a-b);
  const i = (sorted.length - 1) * p;
  const lo = Math.floor(i), hi = Math.ceil(i);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi]-sorted[lo]) * (i-lo);
}

function statsFor(key, arr) {
  const vals = arr.map(it => it[key]).filter(v => v != null);
  if (!vals.length) return null;
  return { n: vals.length, p25: pct(vals,0.25), p50: pct(vals,0.5), p75: pct(vals,0.75),
           min: Math.min(...vals), max: Math.max(...vals) };
}

function fmt(v, unit, digits) {
  if (v == null) return "—";
  const d = digits ?? (v >= 100 ? 0 : v >= 10 ? 1 : 2);
  return v.toFixed(d) + " " + unit;
}

// --- Renderers --------------------------------------------------------------
function renderSliders() {
  const root = $("#sliders");
  root.innerHTML = "";
  for (const k of NUT_KEYS) {
    const n = NUTRIENTS[k];
    const range = RANGES[k];
    const wrap = el("div", {class:"slider"});
    const row = el("div", {class:"row"});
    row.append(el("label", {html: n.label}));
    const val = el("span", {class:"val", id:`val-${k}`});
    row.append(val);
    wrap.append(row);

    const dual = el("div", {class:"dual"});
    dual.append(el("div", {class:"track"}));
    const fill = el("div", {class:"fill", id:`fill-${k}`});
    dual.append(fill);
    const lo = el("input", {type:"range", min:0, max:range.max, value:0, step: range.max <= 10 ? 0.1 : 1, id:`lo-${k}`});
    const hi = el("input", {type:"range", min:0, max:range.max, value:range.max, step: range.max <= 10 ? 0.1 : 1, id:`hi-${k}`});
    lo.addEventListener("input", () => onSlider(k));
    hi.addEventListener("input", () => onSlider(k));
    dual.append(lo); dual.append(hi);
    wrap.append(dual);
    wrap.append(el("div", {class:"dv", html:`Daily Value: ${n.dv} ${n.unit}`}));
    root.append(wrap);
    updateSliderLabel(k);
  }
}

function onSlider(k) {
  const lo = +$(`#lo-${k}`).value;
  const hi = +$(`#hi-${k}`).value;
  if (lo > hi) {
    if (document.activeElement === $(`#lo-${k}`)) $(`#hi-${k}`).value = lo;
    else $(`#lo-${k}`).value = hi;
  }
  state.range[k] = [+$(`#lo-${k}`).value, +$(`#hi-${k}`).value];
  state.page = 0;
  updateSliderLabel(k);
  rerender();
}

function updateSliderLabel(k) {
  const n = NUTRIENTS[k];
  const [lo, hi] = state.range[k];
  const max = RANGES[k].max;
  $(`#val-${k}`).textContent = `${fmt(lo, n.unit, n.unit==='mg'?0:1)} – ${fmt(hi, n.unit, n.unit==='mg'?0:1)}${hi>=max?'+':''}`;
  $(`#fill-${k}`).style.left = (100 * lo / max) + "%";
  $(`#fill-${k}`).style.right = (100 * (1 - hi / max)) + "%";
}

function bindChips() {
  for (const c of document.querySelectorAll("#quickChips .chip")) {
    c.addEventListener("click", () => {
      const k = c.dataset.quick;
      if (state.quick.has(k)) state.quick.delete(k); else state.quick.add(k);
      state.page = 0;
      renderChips();
      rerender();
    });
  }
}

function renderChips() {
  for (const c of document.querySelectorAll("#quickChips .chip")) {
    c.classList.toggle("on", state.quick.has(c.dataset.quick));
  }
}

function renderHead() {
  const cols = [
    {k:"name", label:"Name"},
    {k:"brand", label:"Brand"},
    {k:"cat", label:"Category"},
  ].concat(NUT_KEYS.map(k => ({k, label: NUTRIENTS[k].label + " (" + NUTRIENTS[k].unit + ")", num:true})));

  const tr = $("#tableHead");
  tr.innerHTML = "";
  for (const c of cols) {
    const th = el("th", {class: c.num ? "num" : ""});
    th.textContent = c.label;
    if (state.sort === c.k) {
      th.append(el("span", {class:"arrow", html: state.sortDir === "asc" ? " ↑" : " ↓"}));
    }
    th.addEventListener("click", () => {
      if (state.sort === c.k) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else { state.sort = c.k; state.sortDir = c.num ? "desc" : "asc"; }
      state.page = 0;
      rerender();
    });
    tr.append(th);
  }
}

function fmtCell(v) {
  if (v == null) return "—";
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function cellClass(v, n) {
  if (v === 0) return " zero";
  if (v != null && n.dv > 0 && v > n.dv) return " over";
  return "";
}

function renderRows(vis) {
  const sortedRows = sorted(vis);
  const start = state.page * state.perPage;
  const slice = sortedRows.slice(start, start + state.perPage);

  // Table (desktop / tablet)
  const body = $("#tableBody");
  body.innerHTML = "";
  for (const it of slice) {
    const tr = el("tr");
    tr.append(el("td", {class:"name", title:it.name, html: it.name}));
    tr.append(el("td", {class:"brand", html: it.brand || ""}));
    tr.append(el("td", {class:"cat", html: it.cat || ""}));
    for (const k of NUT_KEYS) {
      const n = NUTRIENTS[k];
      const v = it[k];
      tr.append(el("td", {class: "num" + cellClass(v, n), html: fmtCell(v)}));
    }
    body.append(tr);
  }

  // Cards (mobile)
  const cards = $("#cards");
  cards.innerHTML = "";
  for (const it of slice) {
    const card = el("div", {class:"card"});
    card.append(el("div", {class:"name", html: it.name}));
    const meta = [it.brand, it.cat].filter(Boolean).join(" · ");
    if (meta) card.append(el("div", {class:"meta", html: meta}));
    const grid = el("div", {class:"grid"});
    for (const k of NUT_KEYS) {
      const n = NUTRIENTS[k];
      const v = it[k];
      const row = el("div", {class:"row"});
      row.append(el("span", {class:"k", html: n.label}));
      row.append(el("span", {class:"v" + cellClass(v, n),
        html: `${fmtCell(v)} <small style="color:var(--muted)">${n.unit}</small>`}));
      grid.append(row);
    }
    card.append(grid);
    cards.append(card);
  }

  $("#resultCount").textContent = sortedRows.length;
  $("#applyCount").textContent = sortedRows.length;
  $("#pageInfo").textContent = sortedRows.length === 0
    ? "no matches" : `${start+1}–${Math.min(start+state.perPage, sortedRows.length)} of ${sortedRows.length}`;
  $("#prev").disabled = state.page === 0;
  $("#next").disabled = (state.page+1) * state.perPage >= sortedRows.length;
}

function renderInsight(vis) {
  $("#iCount").textContent = vis.length;
  const sk = statsFor("kcal", vis);
  const ss = statsFor("sodium", vis);
  const su = statsFor("sugar", vis);
  $("#iMedKcal").textContent = sk ? fmt(sk.p50, "kcal", 0) : "—";
  $("#iMedSodium").textContent = ss ? fmt(ss.p50, "mg", 0) : "—";
  $("#iMedSugar").textContent = su ? fmt(su.p50, "g", 1) : "—";

  const root = $("#nutrientCards");
  root.innerHTML = "";
  for (const k of NUT_KEYS) {
    const n = NUTRIENTS[k];
    const s = statsFor(k, vis);
    const card = el("div", {class:"nutrient-card"});
    const head = el("h3");
    head.append(el("span", {html: n.label}));
    head.append(el("span", {class:"v", style:"color:var(--muted);font-weight:400;font-size:11px",
      html: `n=${s ? s.n : 0}`}));
    card.append(head);
    card.append(el("div", {class:"dv-line",
      html: `Daily Value <b>${n.dv} ${n.unit}</b>${n.direction === "limit" ? " (limit)" : n.direction === "avoid" ? " (avoid)" : " (minimum)"}`}));
    const q = el("div", {class:"quartile"});
    const fmtv = v => v == null ? "—" : (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));
    for (const [label, val] of [["min", s?.min], ["p25", s?.p25], ["median", s?.p50], ["p75", s?.p75]]) {
      const cell = el("div");
      cell.append(el("div", {class:"num", html: fmtv(val)}));
      cell.append(el("div", {html: label}));
      q.append(cell);
    }
    card.append(q);
    card.append(el("div", {class:"note", html: n.note}));
    root.append(card);
  }
}

function rerender() {
  const vis = visible();
  renderHead();
  renderRows(vis);
  renderInsight(vis);
}

// --- Init -------------------------------------------------------------------
function buildCategoryFilter() {
  const cats = Array.from(new Set(ITEMS.map(it => it.cat))).filter(Boolean).sort();
  const sel = $("#catFilter");
  sel.append(el("option", {value:"", html:"(all)"}));
  for (const c of cats) sel.append(el("option", {value:c, html:c}));
  sel.addEventListener("change", () => { state.cat = sel.value; state.page = 0; rerender(); });
}

function buildSort() {
  const sel = $("#sort");
  for (const k of ["kcal","fat","satfat","chol","sodium","sugar","fiber","protein","name"]) {
    const label = k === "name" ? "Name" : NUTRIENTS[k].label;
    sel.append(el("option", {value:k, html:label}));
  }
  sel.value = state.sort;
  sel.addEventListener("change", () => { state.sort = sel.value; state.page = 0; rerender(); });
}

function exportCSV() {
  const vis = sorted(visible());
  const cols = ["name","brand","cat", ...NUT_KEYS];
  const lines = [cols.join(",")];
  for (const it of vis) {
    lines.push(cols.map(c => {
      const v = it[c];
      if (v == null) return "";
      const s = String(v);
      return s.includes(",") || s.includes('"') ? '"' + s.replace(/"/g,'""') + '"' : s;
    }).join(","));
  }
  const blob = new Blob([lines.join("\n")], {type:"text/csv"});
  const a = el("a", {href: URL.createObjectURL(blob), download: "diets-export.csv"});
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Mobile: collapse filters by default; toggle button shows/hides
const filtersEl = $("#filters");
const toggleBtn = $("#toggleFilters");
const applyBtn = $("#applyFilters");
const isMobile = () => window.matchMedia("(max-width: 760px)").matches;
if (isMobile()) {
  filtersEl.classList.add("collapsed");
  applyBtn.style.display = "block";
}
toggleBtn.addEventListener("click", () => {
  filtersEl.classList.toggle("collapsed");
  toggleBtn.classList.toggle("active", !filtersEl.classList.contains("collapsed"));
});
applyBtn.addEventListener("click", () => {
  filtersEl.classList.add("collapsed");
  toggleBtn.classList.remove("active");
  window.scrollTo({top: 0, behavior: "smooth"});
});

$("#search").addEventListener("input", e => { state.search = e.target.value; state.page = 0; rerender(); });
$("#prev").addEventListener("click", () => { state.page = Math.max(0, state.page-1); rerender(); });
$("#next").addEventListener("click", () => { state.page++; rerender(); });
$("#exportBtn").addEventListener("click", exportCSV);
$("#reset").addEventListener("click", () => {
  state.search = ""; state.cat = ""; state.quick = new Set(); state.page = 0;
  for (const k of NUT_KEYS) state.range[k] = [0, RANGES[k].max];
  $("#search").value = ""; $("#catFilter").value = "";
  renderSliders(); renderChips(); rerender();
});

$("#totalCount").textContent = ITEMS.length;
$("#totalCount2").textContent = ITEMS.length;
renderSliders();
bindChips();
renderChips();
buildCategoryFilter();
buildSort();
rerender();
</script>
</body>
</html>
"""


def main() -> None:
    off = load_openfood(DATA / "openfood.csv") if (DATA / "openfood.csv").exists() else []
    ol = load_openlabel(DATA / "products-3000.csv") if (DATA / "products-3000.csv").exists() else []
    print(f"Loaded: openfood={len(off)}, openlabel={len(ol)}")
    items = dedupe(off + ol)
    print(f"After dedupe: {len(items)}")
    # Sort by name for stable ordering in HTML
    items.sort(key=lambda it: (it["name"].lower(), it.get("brand","").lower()))
    write_html(items)


if __name__ == "__main__":
    main()
