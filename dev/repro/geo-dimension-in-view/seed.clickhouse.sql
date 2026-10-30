CREATE TABLE repro.users (id Int32, latitude Float64, longitude Float64) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.users VALUES (1,10.5,20.5),(2,30.5,40.5);
