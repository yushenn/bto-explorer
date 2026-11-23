-- Analysis queries for HDB Resale Insights.
-- build.py loads the cleaned data into a SQLite table called `sales`, then runs
-- each block below. Blocks are separated by "-- name: <key>" lines.
--
-- sales(month TEXT 'YYYY-MM', year INT, quarter TEXT 'YYYY-Qn', town TEXT,
--       flat_type TEXT, floor_area_sqm REAL, storey_mid INT,
--       lease_years REAL, resale_price REAL, price_psm REAL)
--
-- SQLite has no MEDIAN(), so medians use window functions: number the rows in
-- each group, then average the middle one (odd count) or middle two (even count).

-- name: monthly_median_by_type
-- Median resale price per month for the four most common flat types.
WITH ranked AS (
  SELECT month, flat_type, resale_price,
         ROW_NUMBER() OVER (PARTITION BY month, flat_type ORDER BY resale_price) AS rn,
         COUNT(*)     OVER (PARTITION BY month, flat_type)                       AS n
  FROM sales
  WHERE flat_type IN ('3 ROOM', '4 ROOM', '5 ROOM', 'EXECUTIVE')
)
SELECT month, flat_type, AVG(resale_price) AS median_price, MAX(n) AS sales
FROM ranked
WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY month, flat_type
ORDER BY month, flat_type;

-- name: monthly_overall
-- Median price, transactions and million-dollar sales per month (all flat types).
WITH ranked AS (
  SELECT month, resale_price,
         ROW_NUMBER() OVER (PARTITION BY month ORDER BY resale_price) AS rn,
         COUNT(*)     OVER (PARTITION BY month)                       AS n
  FROM sales
),
med AS (
  SELECT month, AVG(resale_price) AS median_price
  FROM ranked WHERE rn IN ((n + 1) / 2, (n + 2) / 2) GROUP BY month
)
SELECT s.month,
       m.median_price,
       COUNT(*)                                            AS sales,
       SUM(CASE WHEN s.resale_price >= 1000000 THEN 1 ELSE 0 END) AS million_sales
FROM sales s JOIN med m USING (month)
GROUP BY s.month
ORDER BY s.month;

-- name: town_recent
-- Median price per square metre by town over the last 12 months of data,
-- compared with the same 12 months five years earlier.
WITH bounds AS (
  SELECT MAX(month) AS last_month FROM sales
),
tagged AS (
  SELECT s.town, s.price_psm, s.resale_price,
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
       now.median_psm                                   AS psm_now,
       now.n                                            AS sales_now,
       then_.median_psm                                 AS psm_5y_ago,
       ROUND(100.0 * (now.median_psm / then_.median_psm - 1), 1) AS growth_pct
FROM med now
LEFT JOIN med then_ ON then_.town = now.town AND then_.period = 'then'
WHERE now.period = 'now'
ORDER BY psm_now DESC;

-- name: lease_decay
-- How price per square metre changes with remaining lease (last 24 months only,
-- so the comparison isn't distorted by the overall price rise).
WITH recent AS (
  SELECT price_psm, CAST(lease_years / 5 AS INT) * 5 AS lease_band
  FROM sales, (SELECT MAX(month) AS last_month FROM sales) b
  WHERE month > strftime('%Y-%m', date(b.last_month || '-01', '-24 months'))
),
ranked AS (
  SELECT lease_band, price_psm,
         ROW_NUMBER() OVER (PARTITION BY lease_band ORDER BY price_psm) AS rn,
         COUNT(*)     OVER (PARTITION BY lease_band)                    AS n
  FROM recent
)
SELECT lease_band, AVG(price_psm) AS median_psm, MAX(n) AS sales
FROM ranked WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY lease_band
HAVING MAX(n) >= 200
ORDER BY lease_band;

-- name: storey_premium
-- Median price per square metre by storey band (last 24 months).
WITH recent AS (
  SELECT price_psm,
         CASE WHEN storey_mid <= 3 THEN '01-03'
              WHEN storey_mid <= 6 THEN '04-06'
              WHEN storey_mid <= 9 THEN '07-09'
              WHEN storey_mid <= 12 THEN '10-12'
              WHEN storey_mid <= 15 THEN '13-15'
              WHEN storey_mid <= 21 THEN '16-21'
              WHEN storey_mid <= 30 THEN '22-30'
              ELSE '31+' END AS band
  FROM sales, (SELECT MAX(month) AS last_month FROM sales) b
  WHERE month > strftime('%Y-%m', date(b.last_month || '-01', '-24 months'))
),
ranked AS (
  SELECT band, price_psm,
         ROW_NUMBER() OVER (PARTITION BY band ORDER BY price_psm) AS rn,
         COUNT(*)     OVER (PARTITION BY band)                    AS n
  FROM recent
)
SELECT band, AVG(price_psm) AS median_psm, MAX(n) AS sales
FROM ranked WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY band
ORDER BY band;
