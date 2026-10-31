CREATE TABLE repro.orders (id Int32, user_id Int32, other_id Int32) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.users (id Int32, vip Int32) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES (1,1,2),(2,2,1);
INSERT INTO repro.users VALUES (1,1),(2,0),(3,1);
