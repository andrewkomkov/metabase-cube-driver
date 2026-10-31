CREATE TABLE repro.users (id Int32) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.orders (id Int32, user_id Int32, status String) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.users VALUES (1),(2);
INSERT INTO repro.orders VALUES (1,1,'early'),(2,1,'late'),(3,2,'late');
