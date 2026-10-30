CREATE TABLE repro.orders (id UInt32, user_id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.users (id UInt32, vip UInt8) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES (1,1,'a',100,'2025-01-01 00:00:00'),(2,2,'b',500,'2025-01-02 00:00:00'),(3,2,'b',100,'2025-01-03 00:00:00');
INSERT INTO repro.users VALUES (1,1),(2,0);
