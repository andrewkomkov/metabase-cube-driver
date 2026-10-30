CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2024-12-15 00:00:00'),
(2, 'a', 10, '2025-01-15 00:00:00'),
(3, 'a', 100, '2025-02-15 00:00:00'),
(4, 'a', 1000, '2025-03-15 00:00:00'),
(5, 'a', 10000, '2025-05-15 00:00:00'),
(6, 'a', 100000, '2025-07-15 00:00:00');
