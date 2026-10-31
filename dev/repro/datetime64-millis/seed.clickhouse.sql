CREATE TABLE repro.events (id Int32, ts DateTime64(3,'UTC')) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.events VALUES (1,'2025-01-01 10:00:00.007'),(2,'2025-01-01 10:00:00.500');
