"""Build the data behind BTO Explorer.

Combines five official datasets into two files the website reads:
  data.json         towns (resale prices, BTO price history, MRT, hawkers), stations, hawker centres
  areas.geojson     simplified town boundaries for the map, tagged with their HDB town

Usage:  python analysis/build.py
Needs:  the files in data/ (run analysis/download.py first)
"""
import json
import pathlib
import re
import sqlite3
from collections import defaultdict

import pandas as pd
from shapely.geometry import Point, mapping, shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SQL = ROOT / "analysis" / "queries.sql"

# HDB towns that cover more than one URA planning area (all other towns share the planning area's name)
TOWN_AREAS = {
    "CENTRAL AREA": ["DOWNTOWN CORE", "OUTRAM", "ROCHOR"],
    "KALLANG/WHAMPOA": ["KALLANG", "NOVENA"],
}
# HDB's mature / non-mature estate classification (used for BTO launches before Oct 2024)
MATURE = {"ANG MO KIO", "BEDOK", "BISHAN", "BUKIT MERAH", "BUKIT TIMAH", "CENTRAL AREA", "CLEMENTI", "GEYLANG",
          "KALLANG/WHAMPOA", "MARINE PARADE", "PASIR RIS", "QUEENSTOWN", "SERANGOON", "TAMPINES", "TOA PAYOH"}
FLAT_TYPES = ["2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE"]


def title(s):
    return " ".join(w.capitalize() if w.isalpha() else "/".join(p.capitalize() for p in w.split("/")) for w in s.split())


# ---------------------------------------------------------------- resale prices (pandas + SQL)
def lease_to_years(value, lease_start, month):
    if isinstance(value, str):
        y = re.search(r"(\d+)\s*year", value)
        m = re.search(r"(\d+)\s*month", value)
        if y:
            return int(y.group(1)) + (int(m.group(1)) / 12 if m else 0)
    return 99 - (int(month[:4]) - lease_start)


def resale_stats():
    df = pd.read_csv(DATA / "resale-flat-prices.csv", dtype={"month": str, "remaining_lease": str})
    df["town"] = df["town"].str.upper().str.strip()
    df["flat_type"] = df["flat_type"].str.upper()
    df["lease_years"] = [lease_to_years(v, s, m) for v, s, m in zip(df["remaining_lease"], df["lease_commence_date"], df["month"])]
    df["price_psm"] = df["resale_price"] / df["floor_area_sqm"]
    df = df[(df["floor_area_sqm"] > 20) & (df["resale_price"] > 50_000)]
    con = sqlite3.connect(":memory:")
    df[["month", "town", "flat_type", "floor_area_sqm", "lease_years", "resale_price", "price_psm"]].to_sql("sales", con, index=False)
    blocks = re.split(r"^-- name: (\w+)\s*$", SQL.read_text(encoding="utf-8"), flags=re.M)
    q = {name: pd.read_sql_query(body, con) for name, body in zip(blocks[1::2], blocks[2::2])}
    con.close()
    print(f"Resale: {len(df):,} sales, {df['month'].min()} to {df['month'].max()}")
    return q, df["month"].min(), df["month"].max(), len(df)


# ---------------------------------------------------------------- BTO prices
def bto_prices():
    rows = json.loads((DATA / "bto-prices.json").read_text(encoding="utf-8"))["result"]["records"]
    out = defaultdict(list)
    for r in rows:
        town = r["town"].strip().upper()
        ftype = r["room_type"].strip().upper().replace("-", " ")
        num = lambda k: int(float(r[k])) if r[k] not in (None, "", "0", "-") else None  # noqa: E731
        out[town].append({
            "fy": int(r["financial_year"]), "type": ftype,
            "min": num("min_selling_price"), "max": num("max_selling_price"),
            "min_grant": num("min_selling_price_less_ahg_shg"), "max_grant": num("max_selling_price_less_ahg_shg"),
        })
    for town in out:
        out[town].sort(key=lambda x: (-x["fy"], FLAT_TYPES.index(x["type"]) if x["type"] in FLAT_TYPES else 9))
    print(f"BTO prices: {len(rows)} rows, {len(out)} towns, FY{min(r['fy'] for v in out.values() for r in v)}-FY{max(r['fy'] for v in out.values() for r in v)}")
    return dict(out)


# ---------------------------------------------------------------- map layers
def area_to_town():
    m = {}
    for town, areas in TOWN_AREAS.items():
        for a in areas:
            m[a] = town
    return m


def load_areas(towns):
    a2t = area_to_town()
    g = json.loads((DATA / "areas.geojson").read_text(encoding="utf-8"))
    feats, shapes = [], []
    for f in g["features"]:
        name = f["properties"]["PLN_AREA_N"].strip().upper()
        town = a2t.get(name, name if name in towns else None)
        geom = shape(f["geometry"])
        shapes.append((geom, name, town))
        simple = geom.simplify(0.00035, preserve_topology=True)
        gj = json.loads(json.dumps(mapping(simple)), parse_float=lambda x: round(float(x), 5))
        feats.append({"type": "Feature", "properties": {"area": title(name), "town": town}, "geometry": gj})
    return {"type": "FeatureCollection", "features": feats}, shapes


def locate(lon, lat, shapes):
    p = Point(lon, lat)
    for geom, area, town in shapes:
        if geom.contains(p):
            return area, town
    return None, None


def stations(shapes):
    g = json.loads((DATA / "mrt_exits.geojson").read_text(encoding="utf-8"))
    pts = defaultdict(list)
    for f in g["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        pts[f["properties"]["STATION_NA"].strip().upper()].append((lon, lat))
    out = []
    for raw, xy in sorted(pts.items()):
        lon = sum(p[0] for p in xy) / len(xy)
        lat = sum(p[1] for p in xy) / len(xy)
        kind = "LRT" if "LRT" in raw else "MRT"
        name = title(re.sub(r"\s+(MRT|LRT)\s+STATION$", "", raw))
        area, town = locate(lon, lat, shapes)
        out.append({"name": name, "kind": kind, "lat": round(lat, 5), "lng": round(lon, 5), "exits": len(xy), "town": town})
    print(f"Stations: {len(out)} ({sum(s['kind'] == 'MRT' for s in out)} MRT, {sum(s['kind'] == 'LRT' for s in out)} LRT)")
    return out


def hawker_centres(shapes):
    g = json.loads((DATA / "hawkers.geojson").read_text(encoding="utf-8"))
    out = []
    for f in g["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        _, town = locate(lon, lat, shapes)
        stalls = p.get("NUMBER_OF_COOKED_FOOD_STALLS")
        out.append({"name": p.get("NAME", "").strip(), "lat": round(lat, 5), "lng": round(lon, 5),
                    "stalls": int(stalls) if str(stalls).isdigit() else None, "status": p.get("STATUS"), "town": town})
    print(f"Hawker centres: {len(out)}")
    return out


# ---------------------------------------------------------------- assemble
def main():
    q, first_month, last_month, n_sales = resale_stats()
    bto = bto_prices()
    psm = q["town_psm"].set_index("town")
    by_type = q["town_type_price"]
    towns = set(psm.index) | set(bto)
    geo, shapes = load_areas(towns)
    st = stations(shapes)
    hc = hawker_centres(shapes)

    order = psm.sort_values("psm_now", ascending=False).index.tolist()
    data_towns = {}
    for t in sorted(towns):
        row = psm.loc[t] if t in psm.index else None
        prices = {r.flat_type: {"median": round(r.median_price), "sales": int(r.sales), "area": r.avg_area, "lease": r.avg_lease}
                  for r in by_type[by_type["town"] == t].itertuples()}
        data_towns[t] = {
            "name": title(t),
            "mature": t in MATURE,
            "rank": order.index(t) + 1 if t in order else None,
            "psm": round(row["psm_now"]) if row is not None else None,
            "growth": None if row is None or pd.isna(row["growth_pct"]) else float(row["growth_pct"]),
            "sales": int(row["sales_now"]) if row is not None else 0,
            "prices": prices,
            "bto": bto.get(t, []),
            "mrt": sorted({s["name"] for s in st if s["town"] == t and s["kind"] == "MRT"}),
            "lrt": sorted({s["name"] for s in st if s["town"] == t and s["kind"] == "LRT"}),
            "hawkers": sum(1 for h in hc if h["town"] == t),
        }

    out = {
        "meta": {"resale_from": first_month, "resale_to": last_month, "resale_sales": n_sales,
                 "bto_latest_fy": max(r["fy"] for v in bto.values() for r in v), "towns_ranked": len(order)},
        "towns": data_towns,
        "stations": st,
        "hawkers": hc,
    }
    (ROOT / "data.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    (ROOT / "areas.geojson").write_text(json.dumps(geo, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote data.json ({(ROOT / 'data.json').stat().st_size / 1024:.0f} KB) and areas.geojson ({(ROOT / 'areas.geojson').stat().st_size / 1024:.0f} KB)")
    for t in order[:3] + ["..."] + order[-3:]:
        if t == "...":
            print("   ...")
            continue
        d = data_towns[t]
        print(f"  #{d['rank']:>2} {d['name']:<16} S${d['psm']:,}/m²  MRT {len(d['mrt'])}  hawkers {d['hawkers']}  BTO rows {len(d['bto'])}")


if __name__ == "__main__":
    main()
