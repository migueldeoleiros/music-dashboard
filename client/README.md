# Dashboard client (T-006)

React/TypeScript, TanStack Query, and scoped `echarts/core` imports. The generated
`src/api/schema.d.ts` remains the frozen OpenAPI v1 contract; no server changes.
Inter and Natural Earth assets are local, with licenses next to the assets.
Every assigned ISO country has either a polygon or a Natural Earth label-point
marker; an automated 249-code coverage test guards missing small territories.

## Standalone synthetic preview (no API required)

```sh
cd client
npm ci
npm run dev:mock -- --port 18140
```

Open `http://localhost:18140/?from=2026-01-05&to=2026-01-05&tz=UTC`.
`fixture=partial` (default), `fixture=full`, and `fixture=empty` select partial,
100%, and zero coverage. Every view labels synthetic data. No ListenBrainz or
MusicBrainz requests, credentials, or API process are involved.

`src/api/fixtures/aggregate-responses.json` copies the foundation's
`server/tests/fixtures/aggregate_responses.json`, normalizing only the internal
`from_date`/`to_date` aliases to public `from`/`to`. These are illustrative fixture
aggregates, **not** a browser analytics engine: the January 5 sample is returned
only for ranges containing that day. Empty ranges have zero coverage; the full
scenario scales known genre/country weights to four included listens. Foundation
pie/evolution examples illustrate Other at a smaller top-N than the live request.
Partial final buckets are tested separately with coherent weekly component data;
the frozen one-day example is a complete daily bucket.

For production/API integration, use `npm run dev` (Vite proxies `/api` to port
8000), or `npm run build` for the existing Compose static client. Mock mode is
**off by default**; never enable `VITE_MOCK_API` for a live-data build.

## Shared controls and chart semantics

- `from`, `to`, `tz` in the URL are authoritative inclusive local-date filters.
  Presets write absolute dates; navigation/back restores controls and every query.
- Dates use calendar arithmetic only in the UI. The API/PostgreSQL owns timezone
  boundaries and statistical buckets. `to` is clamped to today in the chosen tz.
- Browser `Intl` supplies the default timezone; overrides and reset are explicit.
  An API timezone rejection changes to UTC visibly. If UTC is still yesterday,
  fallback and browser reset also clamp `from` when needed to avoid an invalid
  one-day range.
- All time sends the contract-supported extreme bound `0001-01-01`: the API has no
  earliest-history field, and SQL evolution trims empty edges to actual history before
  allocating monthly buckets. This avoids guessing a start date or changing the contract.
  Heatmap years start at the first returned listen, not year 0001, and include quiet
  intermediate years. There is no client duration cap.
- The heatmap uses timezone-independent civil-day grid coordinates, Monday first,
  with a bounded one-year calendar and matching daily data table. Full-range
  totals remain visible. It never builds a thousands-of-years canvas.
- Every view shows its authoritative dimension's included/total/excluded coverage
  and ingestion freshness. Sync status separately preserves unknown lag as unknown.
- Pies request top 10; evolution requests whole-range top 8. Other is supplied by
  the API and means known categories outside the top N, not unresolved metadata.
- Keys, not labels, drive chart identity and stable colors. Missing artist/album/
  track metadata remains explicit. Unknown genres/countries have no fake category.
- Tables/disclosures are keyboard-accessible alternatives to canvas interaction.

## Checks

```sh
npm run lint
npm run typecheck
npm test
npm run build
npm run check:api
```

Tests cover shared URLs/presets/date validation/UTC fallback, custom timezone and
browser reset, API errors, loading/empty/partial/full coverage, one-day/all-time,
per-bucket series, partial final bucket indicators, independent duplicate-label
legend identities, civil-date heatmap coordinates, local ISO map registration,
resize/disposal, sync freshness and fixture mode with network forbidden.

Chart tests inspect options through a mocked ECharts instance; they do **not**
prove rendered geometry, tooltips, accessibility contrast, or mobile layout.

## Browser integration evidence

T-008 exercised the built client through the same-origin Compose proxy on port 18150
against the SQL-backed API and 8,000 synthetic listens, not fixture mocks. Real local
Chromium/Playwright was used under the task's explicit fallback authorization after the
browser hub's Podman namespace failure. At 360×800 and 1440×1000 all ten views rendered,
with document width equal to viewport width and no console/page errors. Ranges, timezone
override/reset, shared URL reload and zero/partial/full coverage were exercised.

See [`docs/integration.md`](../docs/integration.md) for commands, exact scope, limitations
and screenshot paths under `/tmp/md-integration-shots/`. This is synthetic integration
proof, not the later real ListenBrainz-data acceptance gate.
