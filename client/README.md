# Dashboard client

## Preview without a backend

```sh
npm ci
npm run dev:mock -- --port 18140
```

Open http://localhost:18140/. Use `?fixture=full` or `?fixture=empty` to change the mock coverage (default: `partial`). For the January 5 fixture, use `?from=2026-01-05&to=2026-01-05`. Mock mode makes no requests to music services. Leave `VITE_MOCK_API` off for live builds.

`npm run dev` proxies `/api` to port 8000. `npm run build` builds the static client for Compose.

## Display rules

- The default range is 7 days. Live presets persist as `?preset=today` (or `week`, `7d`, `month`, `30d`, `year`, `all`) and recalculate in the browser timezone on load/back/forward; default `7d` may stay bare. Explicit valid `from`/`to` pairs stay fixed Custom, even alongside a legacy preset. Invalid or incomplete pairs fall back to 7 days. Legacy `tz` is ignored; rejected browser zones visibly fall back to UTC without changing fixed dates.
- Custom opens a native modal dialog. Apply validates and updates the URL; Cancel/Escape discard edits without navigation. Unrelated query parameters and the hash are preserved.
- The heatmap always shows the last 365 days, independent of the selected range.
- Pies include named entries at or above 1%; only the top five named slices get labels. Evolution includes entries above 2%, and is hidden for one-day ranges. The API groups the rest under Other.
- Top Genres counts one appearance per distinct genre per listen. Genre pies and evolution still use weighted counts.
- The map lists countries until you select one, then shows its artists. On phones the artist list opens in a bottom sheet.

## Checks

```sh
npm run lint
npm run typecheck
npm test
npm run build
npm run check:api
```

`src/api/schema.d.ts` is generated from `../contracts/openapi.json`. See the [contract generation instructions](../docs/development.md#regenerate-contract-and-typescript).
