"""Build offline city ranking data from Census SUB-EST2025 CSV (no runtime network)."""
import csv
import hashlib
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
out = Path(__file__).parent
states = {r['name']: r['state'] for r in csv.DictReader((out / 'states.csv').open())}
rows = []
with source.open(encoding='latin1', newline='') as stream:
    for row in csv.DictReader(stream):
        if row['SUMLEV'] != '162' or row['STNAME'] not in states:
            continue
        rows.append((row['NAME'], states[row['STNAME']], int(row['POPESTIMATE2025'])))
assert len(rows) > 19000, 'Unexpected Census place coverage'
# Same-named places within a state cannot be joined safely by name.
from collections import Counter
counts = Counter((r[0], r[1]) for r in rows)
rows = [r for r in rows if counts[(r[0], r[1])] == 1]
with (out / 'population.csv').open('w', newline='') as stream:
    writer = csv.writer(stream)
    writer.writerow(['name', 'state', 'population'])
    writer.writerows(sorted(rows))
(out / 'population-source.json').write_text(json.dumps({
    'source': 'https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/cities/totals/sub-est2025.csv',
    'vintage': 2025, 'field': 'POPESTIMATE2025', 'summary_level': '162',
    'description': 'Census Bureau July 1, 2025 incorporated-place population estimates',
    'downloaded': '2026-10-14', 'rows': len(rows),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'derived_sha256': hashlib.sha256((out / 'population.csv').read_bytes()).hexdigest(),
}, indent=2) + '\n')
print('Population rows:', len(rows))

