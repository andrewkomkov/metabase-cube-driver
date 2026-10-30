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

-- appended: ClickHouse types as Cube sees them. 1000 rows, one per hour from 2025-03-01 UTC, so that
-- ts_ny crosses the US daylight saving change of 2025-03-09.
CREATE TABLE IF NOT EXISTS shop.typed_events (
  id Int64,
  dec_amount Decimal(18, 4),
  dec_big Decimal(38, 8),
  ui64 UInt64,
  f32 Float32,
  ts_utc DateTime('UTC'),
  ts_ny DateTime('America/New_York'),
  ts64_berlin DateTime64(3, 'Europe/Berlin'),
  day Date,
  day32 Date32,
  n_int Nullable(Int32),
  n_str Nullable(String),
  n_dec Nullable(Decimal(12, 2)),
  n_ts Nullable(DateTime('UTC')),
  n_bool Nullable(Bool),
  lc LowCardinality(String),
  lc_n LowCardinality(Nullable(String)),
  flag Bool,
  en Enum8('red' = 1, 'green' = 2),
  uid UUID,
  tags Array(String),
  nums Array(Int32))
ENGINE = MergeTree ORDER BY id;

INSERT INTO shop.typed_events
SELECT i,
       toDecimal64(i, 4) / 8,
       toDecimal128(i, 8) * toDecimal128('1000000000000.00000001', 8) / 1,
       toUInt64(9007199254740993) + i,
       toFloat32(i) / 4,
       toDateTime('2025-03-01 00:00:00', 'UTC') + toIntervalHour(i),
       toDateTime('2025-03-01 00:00:00', 'UTC') + toIntervalHour(i),
       toDateTime64('2025-03-01 00:00:00', 3, 'UTC') + toIntervalHour(i) + toIntervalMillisecond(i % 1000),
       toDate(toDateTime('2025-03-01 00:00:00', 'UTC') + toIntervalHour(i)),
       toDate32(toDateTime('2025-03-01 00:00:00', 'UTC') + toIntervalHour(i)),
       if(i % 3 = 0, NULL, toInt32(i % 10)),
       if(i % 4 = 0, NULL, concat('s', toString(i % 5))),
       if(i % 5 = 0, NULL, toDecimal64(i, 2) / 4),
       if(i % 6 = 0, NULL, toDateTime('2025-03-01 00:00:00', 'UTC') + toIntervalDay(i % 30)),
       if(i % 7 = 0, NULL, i % 2 = 0),
       ['alpha', 'beta', 'gamma'][1 + i % 3],
       if(i % 2 = 0, NULL, ['low', 'high'][1 + i % 4 % 2]),
       i % 2 = 0,
       ['red', 'green'][1 + i % 2],
       toUUID(concat('00000000-0000-0000-0000-', leftPad(toString(i), 12, '0'))),
       arrayMap(x -> concat('t', toString(x)), range(i % 3)),
       arrayMap(x -> toInt32(x), range(i % 4))
FROM (SELECT toInt64(number + 1) AS i FROM numbers(1000))
WHERE (SELECT count() FROM shop.typed_events) = 0;

-- appended: 10 million orders over two years, for latency, row limits and approximate counts.
-- created_at has 6 second steps, amount has 100000 values, user_id has 100000 values.
CREATE TABLE IF NOT EXISTS shop.big_orders (
  id Int64, user_id Int32, status LowCardinality(String), quantity Int32,
  amount Decimal(12, 2), created_at DateTime('UTC'))
ENGINE = MergeTree ORDER BY (created_at, id);

INSERT INTO shop.big_orders
SELECT i, toInt32(1 + i % 100000),
       ['completed', 'completed', 'shipped', 'processing', 'cancelled'][1 + i % 5],
       toInt32(1 + i % 4),
       toDecimal64(1 + (i * 7) % 100000, 2) / 100,
       toDateTime('2024-01-01 00:00:00', 'UTC') + toIntervalSecond(i * 6)
FROM (SELECT toInt64(number + 1) AS i FROM numbers(10000000))
WHERE (SELECT count() FROM shop.big_orders) = 0;
