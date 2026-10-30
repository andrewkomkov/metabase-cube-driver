CREATE TABLE repro.users (id int, state text);
CREATE TABLE repro.orders (id int, user_id int, bill_state text, ship_state text);
CREATE TABLE repro.shipments (order_id int, state text);
CREATE TABLE repro.regions (state text, region text);
INSERT INTO repro.users VALUES (1,'TX'),(2,'WA');
INSERT INTO repro.orders VALUES (1,1,'TX','WA'),(2,1,'TX','TX'),(3,2,'WA','TX');
INSERT INTO repro.shipments VALUES (1,'WA'),(2,'TX'),(3,'TX');
INSERT INTO repro.regions VALUES ('TX','South'),('WA','West');
