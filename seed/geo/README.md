# Offline location lookup

Bundled public-domain US Census geography, pinned to the **2025 Gazetteer** release. These are geographic reference points, not exact member addresses or verified facility coordinates. ZIP Code Tabulation Areas (ZCTAs) approximate ZIP service areas and do not cover every postal ZIP.

Sources downloaded 2026-10-09:

- `2025_Gaz_place_national.zip` (local download name `places.zip`): https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip
- `2025_Gaz_zcta_national.zip` (`zcta.zip`): https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip
- `2025_Gaz_state_national.zip` (`states.zip`): https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_state_national.zip
- **2020** ZCTA-to-county relationships (`relationships.txt`): https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt

The 2025 Gazetteer uses 2020 ZCTAs. Their states are taken from county FIPS prefixes in the 2020 relationship file, preserving every state with positive land overlap. Cross-state ZCTAs have multiple rows; no nearest-city or guessed state assignment is used. Territorial FIPS prefixes 60, 66, 69 and 78 map to AS, GU, MP and VI. Coordinates remain the Census internal point for the whole ZCTA, even when it spans states.

The CSV files retain only name, state, ZIP, latitude and longitude. ZIP remains text to preserve leading zeros. Blank ZIP fields in places/states and blank names in ZCTAs are intentional. Counts: 32,350 place rows, 52 state/DC/PR rows, 33,931 ZCTA/state rows. State lookup covers the 50 states, DC and PR; ZCTA coverage also includes territories present in the source.

`checksums.json` records SHA-256 of source downloads and derived CSVs. To regenerate, download the four files using the local names above, then run:

```sh
python seed/geo/build.py /path/to/downloads
python -m unittest test_geo -v
```

Review and commit data changes; nothing downloads automatically. No database migration is required.

## Website behavior

`geo.lookup(text)` reads and caches the bundled CSVs on the application server. It returns candidate name, state, ZIP, latitude, longitude, kind, label and match status. No network or location logging is performed by this module. Search and the deterministic conversational agent share this function. The frontend sends locations only to the existing same-origin application API.

- `Dayton`: asks the member to choose among states.
- `Dayton, OH` or `Dayton OH`: resolves the Ohio place.
- `45402`: resolves its Census area, then explicitly shows **state-wide** directory listings.
- Misspellings: suggests candidates and waits for selection, never silently corrects.
- Unknown ZIP/city: no price matches, with a helpful explanation.
- City searches still match directory text and state. No radius, travel time, nearby-facility guarantee or address geocoding is provided.
- Canonical Census labels may be remembered as guest preferences; arbitrary free text remains disallowed.

Census places do not include all informal neighborhoods or postal city names. Provider data and verification dates are unchanged. This feature neither publishes a directory batch nor enables directory sync.

