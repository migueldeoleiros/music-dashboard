# Pinned artist/history integration contract

Approved spec: `/home/miguel/projects/artist-history-pages.md`.
Existing aggregate contracts stay unchanged. Canonical DTOs:
`server/src/music_dashboard/presentation/api/schemas/history.py` (extra fields forbidden).

## HTTP (integrator owns route modules, OpenAPI and generated TS)
- `GET /api/artists/{artist_key}` -> ArtistSummary
- `GET /api/artists/{artist_key}/rankings/{dimension}` -> ArtistRankingPage; dimension albums|tracks; limit default 50 max 100; cursor optional
- `GET /api/artists/{artist_key}/heatmap` -> ArtistHeatmap; year optional (server current year), tz default UTC
- `GET /api/artists/{artist_key}/recent` -> ArtistRecentPage; limit default 50 max 100; cursor optional
- `GET /api/history` -> HistoryPage; tz default UTC; limit default 50 max 100; cursor optional
All new HTTP responses/errors: `Cache-Control: no-store`. Existing ErrorResponse shape. 400 invalid input; 404 missing artist; 409 stale/expired cursor (UI replaces pages); 503 disabled/unavailable. No exact listen times, raw fields, listen IDs or freshness fields anywhere. Cursor opaque random bounded server-side tokens (simple in-process TTL/LRU acceptable; restart -> 409). Pin server clock, timezone, generation, query identity in cursor; never caller as_of. Generation string must not encode exact times. History group is display label Today/Yesterday/This Week/Last Week/Month YYYY, computed on server in pinned browser timezone with Monday weeks and exclusive precedence. UI merges adjacent equal labels. Preserve actual repeated plays without public row IDs.

## Data lane interfaces (synchronous, API calls in sync handlers)
Artist lane owns `application/artist_history.py`, exposing `ArtistHistory` with no-arg constructor and methods `summary(artist_key)`, `rankings(artist_key, dimension, limit=50, cursor=None)`, `heatmap(artist_key, year=None, tz="UTC")`, `recent(artist_key, limit=50, cursor=None)` returning matching DTOs or allowlisted dicts. Own repository/helper/test files uniquely. Reuse existing engine/config patterns. Raise fastapi.HTTPException with above statuses, safe generic detail.
History lane owns `application/public_history.py`, exposing `PublicHistory` with no-arg constructor and `page(tz="UTC", limit=50, cursor=None)` -> HistoryPage. Own cursor helpers shared with artist lane if useful; coordinate directly. Own `MUSIC_PUBLIC_EVENT_FEEDS_ENABLED` config (False default); both recent/history must fail closed (503) when disabled/fixture. Prefer bounded opaque in-memory tokens, no deployment secret needed. Ask integrator for migration needs; do not edit migrations/routes/OpenAPI/generated TS. Deliver representative bounded SQL EXPLAIN evidence if available, otherwise report blocked; do not enable exposure.
Artist aggregate summary/rankings/heatmap need not use event-feed switch; recent must.

## Client ownership
UI owns `features/artist-history/ArtistPage.tsx` exporting `ArtistPage({ artistKey }: { artistKey: string })`; `features/artist-history/HistoryPage.tsx` exporting `HistoryPage()`; shared UI components/styles/tests and dashboard reliable links. UI may own `api/history.ts` fetchers typed from generated schema (until regenerated, canonical DTO fields above).
Integrator owns App routing, generated schema, ChartPath exclusions and `app/navigation.ts` helper. Browser routes `/artists/:artistKey` (encodeURIComponent key) and `/history`. `artistHref(key: string): string`; `navigate(href: string): void` pushState + popstate, preserving native modifier clicks via links. Standard anchors work; helper is optional. Dashboard stays at `/` with its existing filter query untouched on leaving; Back restores it. Integrator adds floating bottom-left history link and root route switch; UI should not edit App.tsx. Page links back to `/` may use native anchors. Keys are MBID first, `name:` normalized fallback, never guessed collaboration identity; unresolved credit key null.

## Integration
Workers commit only their lane, send SHA/checks/migration requirements via workmux to artist-history-integration. Integrator merges worker branches into artist-history-integration only, never main, no deploy. Focused checks; no exhaustive verification.
