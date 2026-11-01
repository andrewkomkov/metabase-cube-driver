-- Tables on the Postgres of the gate, for pg_truths.py. Rows of tz_events come from shop.zone_events.
CREATE TABLE IF NOT EXISTS regions_t (state text primary key, label text);
INSERT INTO regions_t VALUES ('TX', 'South'), ('IL', 'Midwest'), ('WA', 'West'), ('MA', 'East') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS people (id int primary key, name text, email text, city text, state text references regions_t(state));
INSERT INTO people SELECT id, name, email, city, state FROM users ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS tz_events (id int primary key, ts timestamptz, ts_plain timestamp, ts3 timestamptz(3), status text, amount numeric(12,2));
