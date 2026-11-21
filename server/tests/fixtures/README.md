# Synthetic fixture contract

No real user data. `aggregate_responses.json` contains populated examples for every chart
family (four rankings, heatmap, countries, two evolutions, two pies), including fractional
weights, partial coverage, missing metadata and known `Other` categories. These examples
are illustrative contract data, NOT aggregates computed from the separate DB seed.
Examples use `from=2026-01-05&to=2026-01-05&tz=UTC&limit=1`; rankings are sliced to limit
when served; use limit=2 on rankings to expose the missing-title/album rows. Other
requests return empty, schema-valid fixture responses. All are marked
`fixture=true`. The package copy is checked for equality by unit tests.

`__init__.py` exposes deterministic seed inputs from
`music_dashboard.infrastructure.persistence.seed_data`. That canonical dataset covers:
recording/release-group/artist genre fallback, zero-vote level, unresolved genre/country,
missing album, unmapped names, multi-credit, same-second distinct MSIDs, XW/XE, Chisinau and
New York DST spring/fall, non-whole-hour Kolkata/Kathmandu offsets and skipped Apia date.
The package source keeps fixture seeding usable when installed without test files.

Run `music-dashboard seed-fixtures` with a migrated writer database URL. No upstream client
or token is loaded. This is seed infrastructure, not ingestion/enrichment/statistics logic.
