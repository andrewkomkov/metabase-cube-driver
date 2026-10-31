CREATE TABLE repro.orders (id Int32, user_id Int32, status String, amount Int32) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.users (id Int32, state String) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES (1,1,'a',10),(2,2,'a',100);
INSERT INTO repro.users VALUES (1,'TX'),(2,'WA');
