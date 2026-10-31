CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-01-10 00:00:00'),
(2, 'a', 10, '2025-03-20 00:00:00'),
(3, 'a', 100, '2025-04-20 00:00:00'),
(4, 'a', 1000, '2025-05-10 00:00:00');
