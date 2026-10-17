CREATE TABLE IF NOT EXISTS items (
  id serial PRIMARY KEY,
  name text NOT NULL,
  price_cents int NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id serial PRIMARY KEY,
  item_id int NOT NULL,
  quantity int NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO items (name, price_cents)
SELECT 'item-' || g, 100 + (g % 50) * 10 FROM generate_series(1, 1000) AS g;
