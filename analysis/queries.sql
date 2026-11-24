-- Town-level price statistics for BTO Explorer.
-- build.py loads the cleaned resale data into a SQLite table called `sales`, then runs
-- each block below. Blocks are separated by "-- name: <key>" lines.
--
-- sales(month TEXT 'YYYY-MM', town TEXT, flat_type TEXT, floor_area_sqm REAL,
--       lease_years REAL, resale_price REAL, price_psm REAL)
--
-- SQLite has no MEDIAN(), so medians use window functions: number the rows in each
-- group, then average the middle one (odd count) or the middle two (even count).

-- name: town_psm
-- Median price per square metre in each town over the last 12 months, compared with
-- the same 12 months five years earlier. This drives the "most to least expensive" map.
WITH bounds AS (
  SELECT MAX(month) AS last_month FROM sales
),
tagged AS (
  SELECT s.town, s.price_psm,
         CASE
           WHEN s.month >  strftime('%Y-%m', date(b.last_month || '-01', '-12 months')) THEN 'now'
           WHEN s.month >  strftime('%Y-%m', date(b.last_month || '-01', '-72 months'))
            AND s.month <= strftime('%Y-%m', date(b.last_month || '-01', '-60 months')) THEN 'then'
         END AS period
  FROM sales s, bounds b
),
ranked AS (
  SELECT town, period, price_psm,
         ROW_NUMBER() OVER (PARTITION BY town, period ORDER BY price_psm) AS rn,
         COUNT(*)     OVER (PARTITION BY town, period)                    AS n
  FROM tagged WHERE period IS NOT NULL
),
med AS (
  SELECT town, period, AVG(price_psm) AS median_psm, MAX(n) AS n
  FROM ranked WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
  GROUP BY town, period
)
SELECT now.town,
       now.median_psm                                             AS psm_now,
       now.n                                                      AS sales_now,
       then_.median_psm                                           AS psm_5y_ago,
       ROUND(100.0 * (now.median_psm / then_.median_psm - 1), 1) AS growth_pct
FROM med now
LEFT JOIN med then_ ON then_.town = now.town AND then_.period = 'then'
WHERE now.period = 'now'
ORDER BY psm_now DESC;

-- name: town_type_price
-- Median resale price and floor area by town and flat type, last 12 months.
WITH recent AS (
  SELECT town, flat_type, resale_price, floor_area_sqm, lease_years
  FROM sales, (SELECT MAX(month) AS last_month FROM sales) b
  WHERE month > strftime('%Y-%m', date(b.last_month || '-01', '-12 months'))
    AND flat_type IN ('2 ROOM', '3 ROOM', '4 ROOM', '5 ROOM', 'EXECUTIVE')
),
ranked AS (
  SELECT town, flat_type, resale_price,
         ROW_NUMBER() OVER (PARTITION BY town, flat_type ORDER BY resale_price) AS rn,
         COUNT(*)     OVER (PARTITION BY town, flat_type)                       AS n
  FROM recent
)
SELECT r.town, r.flat_type, AVG(r.resale_price) AS median_price, MAX(r.n) AS sales,
       (SELECT ROUND(AVG(floor_area_sqm)) FROM recent x WHERE x.town = r.town AND x.flat_type = r.flat_type) AS avg_area,
       (SELECT ROUND(AVG(lease_years))    FROM recent x WHERE x.town = r.town AND x.flat_type = r.flat_type) AS avg_lease
FROM ranked r
WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY r.town, r.flat_type
HAVING MAX(r.n) >= 10
ORDER BY r.town, r.flat_type;
