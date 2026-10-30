# Population ordering for ambiguous city searches

Source: US Census Bureau SUB-EST2025, July 1, 2025 population estimates, summary
level 162 (incorporated places). `population-source.json` records the download,
source/derived checksums and row count. `population.csv` includes 19,461 uniquely
named place/state pairs. These are estimates, not the 2020 enumeration counts.

Rebuild offline with `python seed/geo/build_population.py /path/to/sub-est2025.csv`.
No API key or runtime network request is required. Geography remains the existing
2025 Gazetteer. Exact official place name + state is the join key; duplicate
same-state names and absent/CDP values remain unknown, never guessed as zero.

When multiple exact same-name city candidates have a facility inside the selected
radius, covered candidates are ordered by known population descending, then label.
Unknown population comes after known population. Other candidates follow. A single
covered candidate retains the existing resolution behavior. Multiple candidates
still ask the user to choose a state; no population dominance threshold silently
chooses a city. Explicit states, ZIPs, typo suggestions and nearest-offer distances
retain their existing behavior.

Tests include Ohio-first Dayton/Columbus despite a closer facility in another
state, and Springfield retaining an explicit location choice.

