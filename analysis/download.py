"""Download the HDB resale flat prices dataset (Jan 2017 onwards) from data.gov.sg.

Usage:  python analysis/download.py
Saves:  data/resale-flat-prices.csv
"""
import json
import pathlib
import time
import urllib.request

DATASET_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
API = f"https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET_ID}"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "resale-flat-prices.csv"


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def main():
    get_json(f"{API}/initiate-download")
    for _ in range(20):  # the file is prepared asynchronously, so poll until it's ready
        url = get_json(f"{API}/poll-download").get("data", {}).get("url")
        if url:
            break
        time.sleep(3)
    else:
        raise SystemExit("data.gov.sg did not return a download link; try again later.")
    OUT.parent.mkdir(exist_ok=True)
    urllib.request.urlretrieve(url, OUT)
    print(f"Saved {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
