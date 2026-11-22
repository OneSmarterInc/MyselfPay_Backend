# Hospital published rates

Shows what hospitals near a member publish for six imaging exams, from the hospitals' own price transparency files. No member data is used. The endpoint takes a ZIP the caller supplies and stores nothing about it.

## Endpoint

`GET /api/hospital-rates?zip=45459&cpt=70551&radius=25` (public, read only; radius 1 to 50 miles, default 25)

Returns the hospitals within the radius, nearest first, with cash price, gross charge and the negotiated low and high, plus the range across hospitals, the count of hospitals and of systems (hospitals sharing one price list count once), and the note that figures may not include the radiologist's reading. Hospitals with no name or no posted price for the exam are left out. A bad ZIP or code returns 400, an unknown ZIP 404.

Exams: 70551 MRI brain, 73721 MRI knee, 72148 MRI lumbar spine, 70450 CT head, 71250 CT chest, 74177 CT abdomen and pelvis with contrast (all without contrast except 74177).

## Change record (live database): needs "approve db"

Status: not applied. Vikram or the named operator runs steps 2 to 5 on the hosted database after a verified backup. Claude applies nothing.

1. Backup: take a snapshot of the hosted database and restore it into a scratch instance to confirm it opens. Record the snapshot id here.
2. Create the table by running `ops/hospital-rates.sql` (one `CREATE TABLE IF NOT EXISTS` and one index; no existing table changes).
3. Load the rows: `python manage.py import_hospital_rates seed/hospital_rates_oh.csv --checked 2026-11` (replaces every row in `hospital_rates`; 888 rows, 157 hospitals).
4. Verify: `SELECT cpt, COUNT(*) FROM hospital_rates GROUP BY cpt;` returns 6 rows totalling 888, and `GET /api/hospital-rates?zip=45459&cpt=70551` returns 10 hospitals.
5. Rollback: `DROP TABLE hospital_rates;`. Nothing else reads the table, so the rest of the app is unchanged.

The endpoint answers 500 until step 2 is done, and the frontend block ships only after step 4.

## Data notes

Source: `imaging.duckdb` (163 Ohio hospitals), filtered by `build_hospital_rates.py` to the six codes, outpatient facility rows, positive prices. 14 hospitals have no name in the file; they stay hidden until the data team fills the name from the CMS hospital list. Shared price lists are grouped when every posted figure matches exactly. The data team spot-checks 20 hospitals against their own websites before launch.
