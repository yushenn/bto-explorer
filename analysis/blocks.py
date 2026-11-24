"""Locate every HDB block and attach its completion year, size and recent resale prices.

HDB publishes block details (street name, completion year, storeys, homes) and building
locations (with a street *code* and postal code) as two separate datasets with no shared key.
We join them by street: within the same town, a street code and a street name that list the
same block numbers are the same street.

Used by build.py.
"""
import collections
import json
import re

import pandas as pd
from shapely.geometry import shape

# HDB's town codes in the property dataset
TOWN_CODES = {
    "AMK": "ANG MO KIO", "BB": "BUKIT BATOK", "BD": "BEDOK", "BH": "BISHAN", "BM": "BUKIT MERAH", "BP": "BUKIT PANJANG",
    "BT": "BUKIT TIMAH", "CCK": "CHOA CHU KANG", "CL": "CLEMENTI", "CT": "CENTRAL AREA", "GL": "GEYLANG", "HG": "HOUGANG",
    "JE": "JURONG EAST", "JW": "JURONG WEST", "KWN": "KALLANG/WHAMPOA", "MP": "MARINE PARADE", "PG": "PUNGGOL",
    "PRC": "PASIR RIS", "QT": "QUEENSTOWN", "SB": "SEMBAWANG", "SGN": "SERANGOON", "SK": "SENGKANG", "TAP": "TAMPINES",
    "TG": "TENGAH", "TP": "TOA PAYOH", "WL": "WOODLANDS", "YS": "YISHUN",
}
FLAT_COLS = [("2room_sold", "2 ROOM"), ("3room_sold", "3 ROOM"), ("4room_sold", "4 ROOM"), ("5room_sold", "5 ROOM"), ("exec_sold", "EXECUTIVE")]


def norm_blk(b):
    return str(b).strip().upper()


def match_streets(buildings, props):
    """Map each building street code to a street name. Returns {code: (street, town, score)}."""
    code_blocks = collections.defaultdict(set)
    code_towns = collections.defaultdict(collections.Counter)
    for b in buildings:
        code_blocks[b["code"]].add(b["blk"])
        if b["area_town"]:
            code_towns[b["code"]][b["area_town"]] += 1
    street_blocks = collections.defaultdict(set)
    street_town = {}
    for r in props.itertuples():
        street_blocks[r.street].add(r.blk)
        street_town[r.street] = r.town
    out = {}
    for code, blocks in code_blocks.items():
        towns = set(code_towns[code])  # towns this street's buildings sit in (by map location)
        best = None
        for street, sb in street_blocks.items():
            inter = len(blocks & sb)
            if not inter:
                continue
            score = inter / len(blocks | sb)
            same_town = street_town[street] in towns
            if not same_town:
                score -= 0.35  # strongly prefer streets in the same town
            if best is None or score > best[2]:
                best = (street, street_town[street], score)
        if best and best[2] >= 0.3:
            out[code] = best
    return out


def build_blocks(data_dir, locate, resale):
    """Return {town: [block, ...]} for the map."""
    props = pd.read_csv(data_dir / "property-info.csv", dtype=str)
    props = props[props["residential"] == "Y"].copy()
    props["blk"] = props["blk_no"].map(norm_blk)
    props["street"] = props["street"].str.strip().str.upper()
    props["town"] = props["bldg_contract_town"].map(TOWN_CODES)
    props["flat_types"] = props.apply(lambda r: [label for col, label in FLAT_COLS if str(r[col]).strip() not in ("0", "", "nan")], axis=1)

    g = json.loads((data_dir / "hdb-buildings.geojson").read_text(encoding="utf-8"))
    buildings = []
    for f in g["features"]:
        p = f["properties"]
        c = shape(f["geometry"]).representative_point()
        _, town = locate(c.x, c.y)
        buildings.append({"blk": norm_blk(p["BLK_NO"]), "code": p["ST_COD"], "postal": str(p.get("POSTAL_COD") or ""),
                          "lat": round(c.y, 6), "lng": round(c.x, 6), "area_town": town})

    streets = match_streets(buildings, props)
    info = {(r.street, r.blk): r for r in props.itertuples()}

    # recent resale prices per block (last 24 months)
    last = resale["month"].max()
    cutoff = (pd.Period(last, "M") - 24).strftime("%Y-%m")
    recent = resale[resale["month"] > cutoff]
    recent = recent.assign(street=recent["street_name"].str.strip().str.upper(), blk=recent["block"].map(norm_blk))
    prices = {}
    for (street, blk), grp in recent.groupby(["street", "blk"]):
        by_type = {}
        for ftype, g2 in grp.groupby("flat_type"):
            by_type[ftype] = {"median": int(g2["resale_price"].median()), "min": int(g2["resale_price"].min()),
                              "max": int(g2["resale_price"].max()), "n": int(len(g2)), "last": g2["month"].max()}
        prices[(street, blk)] = by_type
    lease_start = resale.assign(street=resale["street_name"].str.strip().str.upper(), blk=resale["block"].map(norm_blk)) \
        .groupby(["street", "blk"])["lease_commence_date"].min().to_dict()

    out = collections.defaultdict(list)
    seen = set()
    matched = 0
    for b in buildings:
        m = streets.get(b["code"])
        if not m:
            continue
        street = m[0]
        row = info.get((street, b["blk"]))
        if row is None:
            continue
        key = (street, b["blk"])
        if key in seen:  # some blocks are drawn as several building shapes; keep one marker per block
            continue
        seen.add(key)
        matched += 1
        town = row.town or b["area_town"]
        types = row.flat_types
        out[town].append({
            "blk": b["blk"], "street": street, "postal": b["postal"], "lat": b["lat"], "lng": b["lng"],
            "year": int(row.year_completed) if str(row.year_completed).isdigit() else None,
            "lease": int(lease_start[key]) if key in lease_start else None,
            "floors": int(row.max_floor_lvl) if str(row.max_floor_lvl).isdigit() else None,
            "units": int(row.total_dwelling_units) if str(row.total_dwelling_units).isdigit() else None,
            "types": types,
            "prices": prices.get(key, {}),
        })
    print(f"Blocks: matched {matched:,} residential blocks to map locations ({len(streets)} of {len({b['code'] for b in buildings})} street codes)")
    return dict(out), streets, buildings
