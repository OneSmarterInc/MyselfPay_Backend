CREATE TABLE IF NOT EXISTS public.mo_hospital_imaging_prices (
 source_row integer PRIMARY KEY CHECK (source_row >= 2),
 state text NOT NULL CHECK (state = 'MO'),
 hospital text NOT NULL,
 distance_miles numeric CHECK (distance_miles >= 0),
 cash_price numeric CHECK (cash_price >= 0),
 gross_charge numeric CHECK (gross_charge >= 0),
 insurer_rates text NOT NULL,
 raw_values jsonb NOT NULL,
 source_sha256 char(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS mo_imaging_hospital_row ON public.mo_hospital_imaging_prices(hospital,source_row);
