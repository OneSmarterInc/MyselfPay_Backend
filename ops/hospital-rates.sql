CREATE TABLE IF NOT EXISTS hospital_rates (
 cms_number TEXT NOT NULL, cpt TEXT NOT NULL,
 hospital_name TEXT NOT NULL, zip TEXT NOT NULL, city TEXT NOT NULL,
 latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, exam TEXT NOT NULL,
 cash_price DOUBLE PRECISION, gross_charge DOUBLE PRECISION,
 negotiated_low DOUBLE PRECISION, negotiated_high DOUBLE PRECISION, payer_count INTEGER NOT NULL DEFAULT 0,
 reading_low DOUBLE PRECISION, reading_high DOUBLE PRECISION, reading_included TEXT NOT NULL DEFAULT 'Unknown',
 shared_list_id TEXT, source_url TEXT, file_date TEXT, checked_on TEXT,
 PRIMARY KEY (cms_number, cpt)
);
CREATE INDEX IF NOT EXISTS hospital_rates_cpt ON hospital_rates(cpt);
