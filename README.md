# BTO Explorer

**Find a town that fits your budget.** An independent, interactive map that helps young couples in Singapore compare HDB towns from most to least expensive, then explore **every HDB block**: where it is, when it was completed, and what flats there recently sold for. It also shows past BTO launch prices, MRT stations and hawker centres. Built entirely from official open data. Not affiliated with HDB.

**Live site:** https://yushenn.github.io/bto-explorer/

[![Monthly data update](https://github.com/yushenn/bto-explorer/actions/workflows/update-data.yml/badge.svg)](https://github.com/yushenn/bto-explorer/actions/workflows/update-data.yml)

![BTO Explorer showing Woodlands: block icons on the map, a block popup with its completion year and resale prices, and BTO launch prices](images/bto-explorer.jpg)

## What you can do

- **See every town ranked** from most to least expensive by resale price per m² (last 12 months), coloured on the map.
- **Set your budget and flat type** to see where a typical resale flat fits, and where only a recent BTO launch (FY2020 onwards) fits.
- **Click a town to see every block on the map** as a house icon, coloured by age (new, 2000s–2010s, older). Click a block for its address, completion year, storeys, number of homes, lease left, flat types, resale prices in the last 24 months, **the nearest MRT station and how far it is**, and **the schools, CHAS clinics, parks and hawker centres within 1 km**.
- **The town panel** shows:
  - its BTO launch price ranges, including the price after grants, and how they've changed since FY2008
  - typical resale prices by flat type, with size and lease left
  - the gap between BTO and resale, for example *4-room in Woodlands: BTO from S$329k vs resale S$548k*
  - its MRT/LRT stations, hawker centres, and whether it's a mature estate
- **Turn on map layers** for MRT and LRT stations, hawker centres, schools, CHAS clinics and parks.
- **Check upcoming launches:** the "Upcoming BTO launches" button opens HDB's official Flat Portal, since there's no open dataset of future launches.

## Data

All from [data.gov.sg](https://data.gov.sg), under the Singapore Open Data Licence v1.0:

| Dataset | Agency | Used for |
| --- | --- | --- |
| Resale flat prices (Jan 2017 onwards) | HDB | Town ranking, typical prices |
| Price Range of HDB Flats Offered (FY2008–FY2024) | HDB | BTO launch prices |
| Master Plan 2019 Planning Area Boundary | URA | Town shapes on the map |
| MRT Station Exit | LTA | Station locations |
| Hawker Centres | NEA | Hawker centre locations |
| HDB Property Information | HDB | Each block's completion year, storeys, homes and flat types |
| HDB Existing Building | HDB | Each block's location |
| General information of schools | MOE | Schools (placed using OneMap's postal code search) |
| CHAS Clinics | MOH | Clinic locations |
| NParks Parks and Nature Reserves | NParks | Park locations |

Map tiles © [OneMap](https://www.onemap.gov.sg/) / Singapore Land Authority.

## How it works

| Step | Tool | What happens |
| --- | --- | --- |
| 1. Download | Python | `analysis/download.py` fetches all ten datasets from the data.gov.sg API |
| 2. Prices | pandas + SQL (SQLite) | `analysis/queries.sql` calculates medians by town and flat type, using window functions because SQLite has no `MEDIAN()` |
| 3. Geography | Shapely | Simplifies town boundaries for the web, groups 613 station exits into 188 stations, and places stations and hawker centres in towns |
| 4. Blocks | pandas + Shapely | `analysis/blocks.py` places 10,802 residential blocks on the map. HDB's block details and building locations don't share an ID, so it matches each street code to a street name by comparing which block numbers they share within the same town. A random sample of 40 blocks matched OneMap's official addresses. |
| 5. Nearby | NumPy | `analysis/amenities.py` finds each block's nearest MRT station and counts the schools, clinics, parks and hawker centres within 1 km, using vectorised haversine distances. School postal codes are looked up on OneMap once and cached in `analysis/schools-geocoded.json`. |
| 6. Present | Leaflet, JavaScript | `index.html` reads `data.json`, `areas.geojson`, `amenities.json` and one `blocks/<town>.json` file per town (loaded only when you open that town) |

**Monthly updates:** a GitHub Actions workflow (`.github/workflows/update-data.yml`) runs on the 3rd of every month. It downloads the latest data, rebuilds the files and commits them if anything changed, and GitHub Pages then republishes the site. It can also be run by hand from the Actions tab.

Notes and limits:
- HDB's BTO price table only covers the towns that had launches in each financial year, so not every town has BTO prices.
- "After grants" is the price less the AHG/SHG grants, as HDB published it. Today's grants are different.
- Central Area and Kallang/Whampoa span more than one URA planning area.
- Tengah is new and has no resale flats yet, so it isn't ranked.
- Future BTO projects aren't in any open dataset, so the "Upcoming BTO launches" button links to HDB's Flat Portal.
- Distances to MRT stations and amenities are straight-line, not walking routes. MOE measures home-to-school distance for Primary 1 registration its own way.
- There's no open dataset of supermarket locations, so supermarkets aren't included.
- "Lease left" is estimated from the year the lease started (or the completion year), assuming a 99-year lease.

## Run it yourself

```bash
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python analysis/download.py
python analysis/build.py       # writes data.json and areas.geojson
python -m http.server          # open http://localhost:8000
```

This is an independent student project, not financial advice. Always confirm with HDB.
