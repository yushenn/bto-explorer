"""Amenities near each block: nearest MRT station, and schools, clinics, parks and hawker
centres within 1 km. All distances are straight-line ("as the crow flies"), not walking routes.

Used by build.py.
"""
import csv
import json
import re
import time
import urllib.parse
import urllib.request

import numpy as np
from shapely.geometry import shape

RADIUS_M = 1000
SCHOOL_CACHE = "schools-geocoded.json"  # kept in analysis/ so monthly updates only look up new schools


def haversine_m(lat1, lng1, lat2, lng2):
    """Distance in metres between one point (lat1, lng1) and arrays of points (lat2, lng2)."""
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lng2 - lng1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def title(s):
    return " ".join(w.capitalize() for w in s.split())


def load_clinics(data_dir):
    g = json.loads((data_dir / "clinics.geojson").read_text(encoding="utf-8"))
    out = []
    for f in g["features"]:
        fields = dict(re.findall(r"<th>([^<]+)</th>\s*<td>([^<]*)</td>", f["properties"].get("Description", "")))
        lng, lat = f["geometry"]["coordinates"][:2]
        out.append({"name": fields.get("HCI_NAME", "Clinic").strip(), "lat": round(lat, 5), "lng": round(lng, 5)})
    return out


def load_parks(data_dir):
    g = json.loads((data_dir / "parks.geojson").read_text(encoding="utf-8"))
    out = []
    for f in g["features"]:
        geom = shape(f["geometry"])
        c = geom.representative_point()
        name = re.sub(r"\s+PG$", " Playground", f["properties"].get("NAME", "").strip())
        out.append({"name": title(name), "lat": round(c.y, 5), "lng": round(c.x, 5)})
    return out


def geocode_postal(postal):
    """Look up a Singapore postal code on OneMap (no API key needed for search)."""
    url = "https://www.onemap.gov.sg/api/common/elastic/search?" + urllib.parse.urlencode(
        {"searchVal": postal, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "bto-explorer-data-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r).get("results") or []
    for x in res:
        if x.get("POSTAL") == postal:
            return float(x["LATITUDE"]), float(x["LONGITUDE"])
    return None


def load_schools(data_dir, cache_dir):
    cache_path = cache_dir / SCHOOL_CACHE
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    rows = list(csv.DictReader(open(data_dir / "schools.csv", encoding="utf-8")))
    out, looked_up = [], 0
    for r in rows:
        postal = r["postal_code"].strip().zfill(6)
        if postal not in cache:
            cache[postal] = geocode_postal(postal)
            looked_up += 1
            time.sleep(0.25)  # be polite to OneMap
        if not cache[postal]:
            continue
        lat, lng = cache[postal]
        level = r["mainlevel_code"].strip().upper()
        kind = "primary" if level == "PRIMARY" or "P1" in level else "secondary"
        out.append({"name": title(r["school_name"].strip()), "level": title(level), "kind": kind, "lat": round(lat, 5), "lng": round(lng, 5)})
    cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")
    print(f"Schools: {len(out)} placed ({looked_up} new OneMap lookups)")
    return out


def enrich_blocks(blocks_by_town, stations, hawkers, schools, clinics, parks):
    """Add nearest MRT and counts of amenities within RADIUS_M to every block (in place)."""
    def arr(points):
        return np.array([p["lat"] for p in points]), np.array([p["lng"] for p in points])
    mrt = [s for s in stations if s["kind"] == "MRT"]
    mrt_lat, mrt_lng = arr(mrt)
    layers = {
        "primary": arr([s for s in schools if s["kind"] == "primary"]),
        "secondary": arr([s for s in schools if s["kind"] == "secondary"]),
        "clinic": arr(clinics),
        "hawker": arr(hawkers),
        "park": arr(parks),
    }
    for rows in blocks_by_town.values():
        for b in rows:
            d = haversine_m(b["lat"], b["lng"], mrt_lat, mrt_lng)
            i = int(d.argmin())
            b["mrt"] = [mrt[i]["name"], int(round(d[i] / 10) * 10)]
            b["near"] = {k: int((haversine_m(b["lat"], b["lng"], la, ln) <= RADIUS_M).sum()) for k, (la, ln) in layers.items()}
