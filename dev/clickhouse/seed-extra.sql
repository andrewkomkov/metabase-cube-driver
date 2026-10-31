-- Data for the checks that came after the first seed. Each statement is safe to run again, so this runs on an old volume too.
CREATE DATABASE IF NOT EXISTS shop_second;
CREATE TABLE IF NOT EXISTS shop_second.tickets (id UInt32, priority String, minutes UInt32) ENGINE = MergeTree ORDER BY id;
INSERT INTO shop_second.tickets SELECT number + 1, ['low', 'medium', 'high'][number % 3 + 1], (number * 7) % 50 + 1 FROM numbers(300)
  WHERE (SELECT count() FROM shop_second.tickets) = 0;
CREATE TABLE IF NOT EXISTS shop.refresh_marks (id UInt32) ENGINE = MergeTree ORDER BY id;
-- Data of the metric catalog in dev/cube/model/metrics.yml. Each row of metric_edges is written by hand.
CREATE TABLE IF NOT EXISTS shop.metric_edges (id Int32, grp LowCardinality(String), day DateTime('UTC'), num Nullable(Float64), den Nullable(Float64)) ENGINE = MergeTree ORDER BY id;
INSERT INTO shop.metric_edges SELECT * FROM values('id Int32, grp String, day DateTime(\'UTC\'), num Nullable(Float64), den Nullable(Float64)',
  (1, 'plain', '2025-01-05 00:00:00', 10, 2), (2, 'plain', '2025-01-20 00:00:00', 30, 4), (3, 'plain', '2025-02-03 00:00:00', 5, 5),
  (4, 'zero_den', '2025-01-07 00:00:00', 10, 0), (5, 'zero_den', '2025-02-07 00:00:00', 20, 0),
  (6, 'null_den', '2025-01-09 00:00:00', 10, NULL), (7, 'null_den', '2025-02-09 00:00:00', 20, NULL),
  (8, 'null_num', '2025-01-11 00:00:00', NULL, 5), (9, 'null_num', '2025-02-11 00:00:00', NULL, 10),
  (10, 'neg_num', '2025-01-13 00:00:00', -10, 4), (11, 'neg_num', '2025-02-13 00:00:00', -20, 5),
  (12, 'neg_den', '2025-01-15 00:00:00', 12, -3), (13, 'neg_den', '2025-02-15 00:00:00', 24, -4),
  (14, 'both_neg', '2025-01-17 00:00:00', -6, -2), (15, 'both_neg', '2025-02-17 00:00:00', -9, -3),
  (16, 'cancels', '2025-01-19 00:00:00', 7, 5), (17, 'cancels', '2025-02-19 00:00:00', 7, -5),
  (18, 'zero_num', '2025-01-21 00:00:00', 0, 3), (19, 'zero_num', '2025-02-21 00:00:00', 0, 6),
  (20, 'mixed', '2025-03-23 00:00:00', NULL, NULL), (21, 'mixed', '2025-03-24 00:00:00', 0, 0), (22, 'mixed', '2025-03-25 00:00:00', 1, 1))
  WHERE (SELECT count() FROM shop.metric_edges) = 0;
-- One balance for each account and day, from 2025-01-01 to 2025-04-30. Each account has its own hour, so that an hour holds one account.
CREATE TABLE IF NOT EXISTS shop.metric_balances (id Int32, account_id Int32, region LowCardinality(String), ts DateTime('UTC'), balance Decimal(14, 2)) ENGINE = MergeTree ORDER BY id;
INSERT INTO shop.metric_balances SELECT toInt32(d * 6 + a), toInt32(a), ['east', 'west'][1 + a % 2], toDateTime('2025-01-01 00:00:00', 'UTC') + toIntervalDay(d) + toIntervalHour(a),
  toDecimal64(toInt64(1000 * a + (d * a * 37) % 500) - if(a = 6, 9000, 0), 2)
  FROM (SELECT toInt32(number) AS d FROM numbers(120)) AS days CROSS JOIN (SELECT toInt32(number + 1) AS a FROM numbers(6)) AS accounts
  WHERE (SELECT count() FROM shop.metric_balances) = 0;
-- Two years of sales, for a change from the prior year.
CREATE TABLE IF NOT EXISTS shop.metric_sales (id Int32, channel LowCardinality(String), sold_at DateTime('UTC'), amount Decimal(12, 2)) ENGINE = MergeTree ORDER BY id;
INSERT INTO shop.metric_sales SELECT i, ['web', 'store', 'phone'][1 + i % 3], toDateTime('2024-01-01 00:00:00', 'UTC') + toIntervalDay(i % 731) + toIntervalHour(i % 24),
  toDecimal64(10 + i % 37 + if(i % 731 > 366, 5, 0), 2)
  FROM (SELECT toInt32(number + 1) AS i FROM numbers(4000))
  WHERE (SELECT count() FROM shop.metric_sales) = 0;

-- A table that the checks of refresh_key change.
CREATE TABLE IF NOT EXISTS shop.pa_mutable (id Int64, kind LowCardinality(String), ts DateTime('UTC'), amount Decimal(14, 2), version Int64) ENGINE = MergeTree ORDER BY id;
