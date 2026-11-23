"""Clean the HDB resale data, run the SQL analysis, fit a price model,
and write everything the dashboard needs to data.json.

Usage:  python analysis/build.py
Needs:  data/resale-flat-prices.csv  (run analysis/download.py first)
"""
import json
import pathlib
import re
import sqlite3

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "resale-flat-prices.csv"
SQL = ROOT / "analysis" / "queries.sql"
OUT = ROOT / "data.json"

MAIN_TYPES = ["2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE"]


# ---------------------------------------------------------------- 1. clean
def lease_to_years(value, lease_start, month):
    """'61 years 04 months' -> 61.33. Older rows may hold a plain number, or nothing:
    then fall back to 99 years from the lease start date."""
    if isinstance(value, str):
        y = re.search(r"(\d+)\s*year", value)
        m = re.search(r"(\d+)\s*month", value)
        if y:
            return int(y.group(1)) + (int(m.group(1)) / 12 if m else 0)
        if value.strip().isdigit():
            return float(value)
    return 99 - (int(month[:4]) - lease_start)


def clean(df):
    df = df.copy()
    df["flat_type"] = df["flat_type"].str.upper().str.replace("MULTI GENERATION", "MULTI-GENERATION")
    df["town"] = df["town"].str.upper().str.strip()
    df["year"] = df["month"].str[:4].astype(int)
    df["quarter"] = df["year"].astype(str) + "-Q" + ((df["month"].str[5:7].astype(int) - 1) // 3 + 1).astype(str)
    lo_hi = df["storey_range"].str.extract(r"(\d+)\s*TO\s*(\d+)").astype(int)
    df["storey_mid"] = ((lo_hi[0] + lo_hi[1]) / 2).round().astype(int)
    df["lease_years"] = [lease_to_years(v, s, m) for v, s, m in zip(df["remaining_lease"], df["lease_commence_date"], df["month"])]
    df["price_psm"] = df["resale_price"] / df["floor_area_sqm"]
    before = len(df)
    df = df[(df["floor_area_sqm"] > 20) & (df["resale_price"] > 50_000) & df["lease_years"].between(1, 99)]
    print(f"Cleaned: kept {len(df):,} of {before:,} rows")
    return df[["month", "year", "quarter", "town", "flat_type", "floor_area_sqm", "storey_mid", "lease_years", "resale_price", "price_psm"]]


# ---------------------------------------------------------------- 2. SQL
def run_queries(df):
    con = sqlite3.connect(":memory:")
    df.to_sql("sales", con, index=False)
    con.execute("CREATE INDEX ix_month ON sales(month)")
    blocks = re.split(r"^-- name: (\w+)\s*$", SQL.read_text(encoding="utf-8"), flags=re.M)
    results = {}
    for name, body in zip(blocks[1::2], blocks[2::2]):
        results[name] = pd.read_sql_query(body, con)
        print(f"  query {name}: {len(results[name])} rows")
    con.close()
    return results


# ---------------------------------------------------------------- 3. model
def fit_model(df):
    """Linear regression on log(price), so each coefficient is roughly a % effect.

    log(price) = b0 + b1*log(area) + b2*storey + b3*lease + b4*lease^2
                 + town effect + flat-type effect + quarter effect
    """
    d = df[df["flat_type"].isin(MAIN_TYPES)].reset_index(drop=True)
    towns = sorted(d["town"].unique())
    types = MAIN_TYPES
    quarters = sorted(d["quarter"].unique())

    def design(frame):
        cols = [
            np.ones(len(frame)),
            np.log(frame["floor_area_sqm"].to_numpy()),
            frame["storey_mid"].to_numpy(dtype=float),
            frame["lease_years"].to_numpy(),
            frame["lease_years"].to_numpy() ** 2 / 100,
        ]
        # one-hot dummies; the first category of each group is the baseline
        cols += [(frame["town"] == t).to_numpy(dtype=float) for t in towns[1:]]
        cols += [(frame["flat_type"] == t).to_numpy(dtype=float) for t in types[1:]]
        cols += [(frame["quarter"] == q).to_numpy(dtype=float) for q in quarters[1:]]
        return np.column_stack(cols).astype(np.float32)

    # hold out 20% of sales to check the model on data it hasn't seen
    rng = np.random.default_rng(42)
    test = rng.random(len(d)) < 0.2
    X, y = design(d), np.log(d["resale_price"].to_numpy())
    beta, *_ = np.linalg.lstsq(X[~test], y[~test], rcond=None)

    pred = X[test] @ beta
    actual = y[test]
    r2 = 1 - np.sum((actual - pred) ** 2) / np.sum((actual - actual.mean()) ** 2)
    mape = float(np.median(np.abs(np.exp(pred) / np.exp(actual) - 1)) * 100)
    print(f"  model: R^2 on held-out sales = {r2:.3f}, median error = {mape:.1f}%")

    k = 5
    model = {
        "intercept": float(beta[0]),
        "log_area": float(beta[1]),
        "storey": float(beta[2]),
        "lease": float(beta[3]),
        "lease_sq": float(beta[4]),
        "town": {towns[0]: 0.0, **{t: float(b) for t, b in zip(towns[1:], beta[k:k + len(towns) - 1])}},
        "flat_type": {types[0]: 0.0, **{t: float(b) for t, b in zip(types[1:], beta[k + len(towns) - 1:k + len(towns) + len(types) - 2])}},
        "latest_quarter": quarters[-1],
        "quarter_effect": float(beta[-1]),
        "r2": round(float(r2), 3),
        "median_error_pct": round(mape, 1),
        "train_rows": int((~test).sum()),
        "test_rows": int(test.sum()),
    }
    # "same flat, different lease": predicted value at each remaining lease, as % of a 99-year lease,
    # holding town, size, floor and date fixed (they cancel out in the ratio)
    def lease_term(L):
        return beta[3] * L + beta[4] * L ** 2 / 100
    model["lease_curve"] = [{"lease": L, "pct_of_99": round(float(np.exp(lease_term(L) - lease_term(99)) * 100), 1)} for L in range(40, 100, 5)] + \
                           [{"lease": 99, "pct_of_99": 100.0}]
    # "same flat, different floor": value relative to floor 2, per storey band midpoint
    model["storey_curve"] = [{"band": b, "pct_of_low": round(float(np.exp(beta[2] * (mid - 2)) * 100), 1)}
                             for b, mid in [("01-03", 2), ("04-06", 5), ("07-09", 8), ("10-12", 11), ("13-15", 14), ("16-21", 18), ("22-30", 26), ("31+", 35)]]
    # typical inputs per flat type, to pre-fill the estimator
    typical = d[d["year"] >= d["year"].max() - 1].groupby("flat_type").agg(
        area=("floor_area_sqm", "median"), storey=("storey_mid", "median"), lease=("lease_years", "median"))
    model["typical"] = {t: {c: round(float(v), 0) for c, v in row.items()} for t, row in typical.iterrows() if t in types}
    return model


# ---------------------------------------------------------------- 4. findings
def findings(df, q, model):
    overall = q["monthly_overall"]
    last = overall["month"].iloc[-1]
    last12 = overall.tail(12)
    prev12 = overall.iloc[-24:-12]
    first12 = overall.head(12)
    by_type = q["monthly_median_by_type"]

    def type_median(t, months):
        return float(by_type[(by_type["flat_type"] == t) & (by_type["month"].isin(months))]["median_price"].median())

    m_last12, m_first12 = list(last12["month"]), list(first12["month"])
    four_now, four_then = type_median("4 ROOM", m_last12), type_median("4 ROOM", m_first12)
    towns = q["town_recent"].dropna(subset=["growth_pct"])
    towns = towns[towns["sales_now"] >= 100]
    lease = q["lease_decay"]
    storey = q["storey_premium"]
    floor_effect = (np.exp(model["storey"]) - 1) * 100

    return {
        "last_month": last,
        "first_month": overall["month"].iloc[0],
        "transactions": int(overall["sales"].sum()),
        "median_last12": float(df[df["month"].isin(m_last12)]["resale_price"].median()),
        "median_prev12": float(df[df["month"].isin(list(prev12["month"]))]["resale_price"].median()),
        "four_room_now": four_now,
        "four_room_then": four_then,
        "four_room_growth_pct": round((four_now / four_then - 1) * 100, 1),
        "million_last12": int(last12["million_sales"].sum()),
        "million_first12": int(first12["million_sales"].sum()),
        "sales_last12": int(last12["sales"].sum()),
        "top_growth_town": towns.sort_values("growth_pct", ascending=False).iloc[0][["town", "growth_pct"]].to_dict(),
        "low_growth_town": towns.sort_values("growth_pct").iloc[0][["town", "growth_pct"]].to_dict(),
        "priciest_town": towns.sort_values("psm_now", ascending=False).iloc[0][["town", "psm_now"]].to_dict(),
        "cheapest_town": towns.sort_values("psm_now").iloc[0][["town", "psm_now"]].to_dict(),
        "lease_high": lease.iloc[-1][["lease_band", "median_psm"]].to_dict(),
        "lease_low": lease.iloc[0][["lease_band", "median_psm"]].to_dict(),
        "storey_low": storey.iloc[0][["band", "median_psm"]].to_dict(),
        "storey_high": storey.iloc[-1][["band", "median_psm"]].to_dict(),
        "floor_effect_pct": round(float(floor_effect), 2),
        "four_room_yearly": {str(y): float(v) for y, v in df[df["flat_type"] == "4 ROOM"].groupby("year")["resale_price"].median().items()},
        "lease_60_pct_of_99": next(p["pct_of_99"] for p in model["lease_curve"] if p["lease"] == 60),
        "storey_raw_ratio": round(float(storey.iloc[-1]["median_psm"] / storey.iloc[0]["median_psm"]), 2),
    }


def records(frame, digits=0):
    return json.loads(frame.round(digits).to_json(orient="records"))


def main():
    if not CSV.exists():
        raise SystemExit("Missing data/resale-flat-prices.csv. Run: python analysis/download.py")
    raw = pd.read_csv(CSV, dtype={"month": str, "remaining_lease": str})
    print(f"Loaded {len(raw):,} rows")
    df = clean(raw)
    q = run_queries(df)
    model = fit_model(df)
    f = findings(df, q, model)

    out = {
        "source": "Resale flat prices based on registration date from Jan-2017 onwards, Housing & Development Board, data.gov.sg",
        "findings": f,
        "monthly_overall": records(q["monthly_overall"]),
        "monthly_by_type": records(q["monthly_median_by_type"]),
        "towns": records(q["town_recent"], 1),
        "lease_decay": records(q["lease_decay"]),
        "storey": records(q["storey_premium"]),
        "model": model,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(json.dumps(f, indent=2, default=str))


if __name__ == "__main__":
    main()
