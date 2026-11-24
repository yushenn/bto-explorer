"""Download every official dataset BTO Explorer uses, from data.gov.sg.

Usage:  python analysis/download.py
Saves into data/:
  resale-flat-prices.csv   HDB resale transactions, Jan 2017 onwards (HDB)
  bto-prices.json          Price range of BTO flats offered, by town and flat type, FY2008+ (HDB)
  areas.geojson            Master Plan 2019 planning area boundaries (URA)
  mrt_exits.geojson        MRT and LRT station exits (LTA)
  hawkers.geojson          Hawker centres (NEA)
  property-info.csv        Every HDB block: completion year, storeys, homes, flat types (HDB)
  hdb-buildings.geojson    Location of every HDB building (HDB, about 60 MB)
  clinics.geojson          CHAS clinics (MOH)
  parks.geojson            Parks and nature reserves (NParks)
  schools.csv              General information of schools (MOE)
"""
import json
import pathlib
import time
import urllib.request

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
FILES = {
    "d_8b84c4ee58e3cfc0ece0d773c8ca6abc": "resale-flat-prices.csv",
    "d_4765db0e87b9c86336792efe8a1f7a66": "areas.geojson",
    "d_b39d3a0871985372d7e1637193335da5": "mrt_exits.geojson",
    "d_4a086da0a5553be1d89383cd90d07ecd": "hawkers.geojson",
    "d_17f5382f26140b1fdae0ba2ef6239d2f": "property-info.csv",
    "d_16b157c52ed637edd6ba1232e026258d": "hdb-buildings.geojson",
    "d_548c33ea2d99e29ec63a7cc9edcccedc": "clinics.geojson",
    "d_77d7ec97be83d44f61b85454f844382f": "parks.geojson",
    "d_688b934f82c1059ed0a6993d2a829089": "schools.csv",
}
BTO_PRICES = "d_2d493bdcc1d9a44828b6e71cb095b88d"  # small table, read through the datastore API


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def download_file(dataset_id, name):
    api = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}"
    get_json(f"{api}/initiate-download")
    for _ in range(20):  # files are prepared asynchronously, so poll until the link is ready
        url = (get_json(f"{api}/poll-download") or {}).get("data", {}).get("url")
        if url:
            break
        time.sleep(3)
    else:
        raise SystemExit(f"data.gov.sg did not return a download link for {name}; try again later.")
    urllib.request.urlretrieve(url, DATA / name)
    print(f"Saved {name} ({(DATA / name).stat().st_size / 1e6:.1f} MB)")


def main():
    DATA.mkdir(exist_ok=True)
    for dataset_id, name in FILES.items():
        download_file(dataset_id, name)
    rows = get_json(f"https://data.gov.sg/api/action/datastore_search?resource_id={BTO_PRICES}&limit=5000")
    (DATA / "bto-prices.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"Saved bto-prices.json ({len(rows['result']['records'])} rows)")


if __name__ == "__main__":
    main()
