CREATE TABLE repro.users (id Int32, state String) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.orders (id Int32, user_id Int32, bill_state String, ship_state String) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.shipments (order_id Int32, state String) ENGINE=MergeTree ORDER BY order_id;
CREATE TABLE repro.regions (state String, region String) ENGINE=MergeTree ORDER BY state;
INSERT INTO repro.users VALUES (1,'TX'),(2,'WA');
INSERT INTO repro.orders VALUES (1,1,'TX','WA'),(2,1,'TX','TX'),(3,2,'WA','TX');
INSERT INTO repro.shipments VALUES (1,'WA'),(2,'TX'),(3,'TX');
INSERT INTO repro.regions VALUES ('TX','South'),('WA','West');
