CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-04-10 00:00:00'),
(2, 'a', 10, '2025-05-10 00:00:00'),
(3, 'a', 100, '2025-06-10 00:00:00');
