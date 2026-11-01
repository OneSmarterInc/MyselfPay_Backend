CREATE TABLE IF NOT EXISTS spatial_locations (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    entry_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    h3_res7 VARCHAR(15) NOT NULL,
    h3_res11 VARCHAR(15) NOT NULL,
    location TEXT NOT NULL,
    FOREIGN KEY(batch_id,entry_key) REFERENCES entries(batch_id,entry_key) ON DELETE CASCADE,
    UNIQUE(batch_id,entry_key,ordinal)
);
CREATE INDEX IF NOT EXISTS spatial_batch_res7 ON spatial_locations(batch_id,h3_res7);
CREATE INDEX IF NOT EXISTS spatial_offer_entry ON offers(batch_id,entry_key);
