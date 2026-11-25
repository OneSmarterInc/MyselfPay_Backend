CREATE TABLE IF NOT EXISTS public.de_hospital_imaging_prices (
 source_row integer PRIMARY KEY, state text NOT NULL CHECK(state='DE'),
 place_id text NOT NULL, ccn text NOT NULL, hospital text NOT NULL, city text NOT NULL, zip5 varchar(5) NOT NULL,
 modality text NOT NULL CHECK(modality IN ('MRI','CT')), contrast text NOT NULL,
 cpt_code text NOT NULL, setting text NOT NULL, billing_class text NOT NULL, professional boolean,
 cash_price numeric CHECK(cash_price>=0), gross_charge numeric CHECK(gross_charge>=0),
 insured_min numeric CHECK(insured_min>=0), insured_median numeric CHECK(insured_median>=0), insured_max numeric CHECK(insured_max>=0),
 latitude double precision, longitude double precision, raw_values jsonb NOT NULL, source_sha256 char(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS de_imaging_comparison ON public.de_hospital_imaging_prices(modality,contrast,place_id);
