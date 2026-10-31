CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-01-01 00:00:00'),
(2, 'a', 200, '2025-01-02 00:00:00'),
(3, 'b', 300, '2025-01-03 00:00:00');
