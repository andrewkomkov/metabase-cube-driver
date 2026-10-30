-- Deterministic demo data. Every value derives from numbers(), so the
-- conformance suite can compute the truth directly from these tables.
CREATE TABLE shop.users (
  id Int32, name String, email String, city String, state String,
  latitude Float64, longitude Float64, is_vip Bool, created_at DateTime('UTC'))
ENGINE = MergeTree ORDER BY id;

CREATE TABLE shop.products (
  id Int32, name String, category String, price Decimal(10, 2))
ENGINE = MergeTree ORDER BY id;

CREATE TABLE shop.orders (
  id Int32, user_id Int32, product_id Int32, status LowCardinality(String),
  quantity Int32, amount Decimal(12, 2), created_at DateTime('UTC'))
ENGINE = MergeTree ORDER BY (created_at, id);

INSERT INTO shop.users
SELECT i, concat('User ', toString(i)), concat('user', toString(i), '@example.com'),
       ['Austin','Chicago','Seattle','Boston'][1 + i % 4],
       ['TX','IL','WA','MA'][1 + i % 4],
       [30.27,41.88,47.61,42.36][1 + i % 4], [-97.74,-87.63,-122.33,-71.06][1 + i % 4],
       i % 5 = 0, toDateTime('2024-01-01 00:00:00', 'UTC') + toIntervalDay(i)
FROM (SELECT toInt32(number + 1) AS i FROM numbers(200));

INSERT INTO shop.products
SELECT i, concat('Product ', toString(i)), ['Gadget','Widget','Gizmo'][1 + i % 3], toDecimal64(10 + i, 2)
FROM (SELECT toInt32(number + 1) AS i FROM numbers(30));

INSERT INTO shop.orders
SELECT i, 1 + i % 200, 1 + i % 30,
       ['completed','completed','shipped','processing','cancelled'][1 + i % 5],
       1 + i % 4,
       toDecimal64((1 + i % 4) * (11 + i % 30), 2),
       toDateTime('2025-01-01 00:00:00', 'UTC') + toIntervalDay(i % 365) + toIntervalHour(i % 24)
FROM (SELECT toInt32(number + 1) AS i FROM numbers(5000));
