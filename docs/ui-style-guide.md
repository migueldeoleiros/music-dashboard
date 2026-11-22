# UI style guide

Dashboard is the visual reference; this guide codifies it. Sections: tokens, components and rules, breakpoints. The first section is the token contract.

## Tokens

CSS custom properties on `:root` in `client/src/styles/global.css` are the only shared styling
interface between UI lanes. **Names are frozen; values may change** (theme lane C1). Components
and ECharts options read tokens (`var(--x)`, or `getComputedStyle(document.documentElement)` at
runtime for canvas charts) and never hard-code the values below.

| Token | Role | Initial value |
| --- | --- | --- |
| `--bg` | Page background | `#000000` |
| `--surface` | Cards, panels | `#0a0a0a` |
| `--surface-2` | Raised/inset surfaces: inputs, buttons, image placeholders | `#171717` |
| `--border` | Hairlines and outlines | `#2a2a2a` |
| `--text` | Primary text | `#fafafa` |
| `--text-muted` | Secondary text, captions | `#a8a8a8` |
| `--accent` | Primary accent: active state, focus ring, key marks | `#2ca7d3` |
| `--accent-soft` | Translucent accent for fills, hover and selected backgrounds | `rgb(44 167 211 / 18%)` |
| `--radius-sm` | Inputs, buttons, small tiles | `5px` |
| `--radius-md` | Cards, panels, images | `8px` |
| `--radius-lg` | Pills, hero cards | `30px` |
| `--font` | Font stack | `'Inter Variable', system-ui, sans-serif` |
| `--series-1` … `--series-10` | Ordered categorical chart series colors | `hsl(H 72% ~60%)` with H = 197, 12, 145, 262, 40, 330, 172, 85, 225, 295 (see `global.css`) |
| `--on-accent` | Text on solid accent fills (active chips, primary buttons) | `#04141c` |
| `--warning` | Cautionary notes (partial bucket) | `hsl(40 88% 68%)` |
| `--danger`, `--danger-bg`, `--danger-border` | Error notices: text, fill, outline | `hsl(350 …)` (see `global.css`) |
| `--scrim` | Backdrop/shadow veil behind sheets and popovers | `rgb(0 0 0 / 75%)` |
| `--tap-size` | Minimum touch target height | `44px` |
| `--series-other` | The "Other" aggregate series and no-data marks | `#808080` |

`--muted` is a deprecated alias of `--text-muted`, kept so existing rules keep working. New code
uses `--text-muted`.

## Rules

- Add tokens only by editing this guide and `global.css` in the same change; never rename or remove one.
- Key-stable identity colors (`stableColor`) may stay hash-derived; ordered series use `--series-N`
  (wrap after 10) and `--series-other` for "Other".
- Styles live in the file for their area (below); `global.css` holds tokens, reset and shared control sizing.

## Style files

| File | Contents |
| --- | --- |
| `client/src/styles/global.css` (token values) and `styles/shell.css` | shell, period bar, layout, coverage |
| `client/src/styles/charts.css` | charts, heatmap scroller, map controls |
| `client/src/styles/entity-list.css` | entity list, rankings, country panel/sheet |
| `client/src/styles/ui.css` | icons, icon buttons, floating nav, full-row links, SongRow |
| `client/src/styles/artist-history.css` | History and artist page layout |

## Breakpoints and touch rules

CSS custom properties cannot be used in media queries, so the breakpoints are literals. Keep them in sync:

| Name | Query | Where it is used |
| --- | --- | --- |
| mobile | `(max-width: 720px)` | `shell.css`, `charts.css`, `entity-list.css`, `global.css`; JS mirrors: `MOBILE_QUERY` (`components/charts/theme.ts`) and `SHEET_QUERY` (`features/origin-map/CountryArtistsPanel.tsx`) |
| tablet | `(max-width: 1100px)` | `shell.css` (4-up rankings become 2-up) |
| touch | `(pointer: coarse)` | 44px targets and 16px inputs also apply on coarse tablets wider than 720px; charts switch to tap tooltips and touch pan/pinch |
| hover | `(hover: hover)` | hover-only decoration is wrapped in this query; nothing depends on hover |
| reduced motion | `(prefers-reduced-motion: reduce)` | CSS animations/transitions near-zero; ECharts `animation: false` |

Behavior at the mobile breakpoint: period chips become one horizontally scrollable row; the native Custom timeframe dialog stacks date inputs in a single column (desktop: two columns), stays within the viewport and scrolls internally; charts use `--chart-h-narrow`; the heatmap scrolls sideways inside its own region, parked at the latest weeks; the country panel becomes a modal bottom sheet (`role=dialog`, backdrop, page scroll lock, focus trap, Escape, tap outside, swipe-down on the handle, safe-area insets).

## Map selection

`App` owns `selectedCountry`. Map click, keyboard chip or the same country again toggles it; the panel's close button and Escape clear it. Changing the period keeps the selection and the panel refetches with the shared filters (empty ranges show the panel's own empty state). On wide screens the panel sits beside the map (map height unchanged); on mobile it is the sheet. Backdrop clicks within 500 ms of opening are ignored because the browser's compatibility click for the tap that opened the sheet would otherwise close it immediately.

## Shared components (reuse before building)

All in `client/src/components/ui/` (barrel `index.ts`).

| Component | Use |
| --- | --- |
| `EntityImage` | The only way to render cover art/photos. Size by role: `sm` 32 (dense), `md` 48 (rows), `lg` 96, `hero` (page header, #1 card). Placeholder tile when no/failed image. |
| `CompactRow` | Image, title, count. Dashboard top lists and artist top albums/tracks. Props: `label, href?, src?, secondary?, count?, rank?, showImage?, shape?`. `EntityRow` is legacy; migrate to `CompactRow`. |
| `SongRow` | Image + full-width columns title \| artist \| album (stacked on mobile). Only History and artist recent songs. Props: `song: PublicSong`. Links itself to the primary artist. |
| `EntityList` | The `<ol>` wrapper for rows. |
| `Icon` | Inline SVG Material icon, `name: 'home' \| 'history' \| 'add'`, `size` (24). Add new icons to its path map. |
| `IconButton` | Round 44px icon-only control. Props: `icon, label` (required name + tooltip), `href?` (renders `<a>`) or `onClick`. |
| `FloatingNav` | Top-left vertical stack (absolute, scrolls with page; its height is reserved above content via `.floating-nav--N`) of IconButtons, Home first. `items`: dashboard `['history']`, History `['home']`, artist `['home','history']`. No bottom-left bubble, no text nav links. |
| `HeatmapPanel`, `RankingList` (`features/dashboard`) | Reused as-is on the artist page (artist data, own range). |

### Rows and lists
- Whole row is one link: the title anchor (`.row-link`) is stretched over the `<li>` (`::after { inset: 0 }`). Never nest anchors.
- Multi-credit songs/albums: row links to the primary (first credited) artist. Other credited names are individual links (`.row-inline-link`, `position: relative; z-index: 1`) above the row link. Unresolved artists: plain text, no link. Only collaboration rows have two hover behaviours.
- Row look follows the dashboard: `.entity-row` padding, hairline separators, `--text` title, `--text-muted` secondary.

### Links and hover
- Hover changes text color to `--accent` only: no underline, no background, no border change.
- Hover styles are wrapped in `@media (hover: hover)`. Keyboard focus is always visible (2px `--accent` outline; rows show it on the row).

### Icon buttons
- Icon-only, `aria-label` + tooltip, 44px (`--tap-size`), `--surface-2` fill, `--border` outline; hover color `--accent`.

### Page header
- Page title `h1` (`clamp(1.6rem, 4vw, 2.6rem)`), small muted line(s) under it only when informative. No explanatory subtext about sorting, grouping or privacy. Navigation is the FloatingNav, not inline links.
- Artist page order: header (name, play total, small genre line), heatmap, top albums, top tracks, recent songs.

### Panels
- Panel titles sit **outside** the card, above it, like the dashboard (`ChartPanel`). The card holds only content.

### Cover art
- Every cover goes through `EntityImage` with the size for its role. Never use a bare `<img>`.
- `image_url` for rankings, History feed and artist endpoints comes from the single server resolver (below); clients never guess covers.

### Reuse-before-building checklist
1. Is there a component above that does this? Use it; extend with an additive prop if needed.
2. Rows: `CompactRow` (lists) or `SongRow` (songs). No new row markup.
3. Links: stretched row link, accent text hover in `(hover: hover)`, visible focus, no nested anchors.
4. Icons: `Icon`/`IconButton`; no text-only floating buttons.
5. Colors, radii, sizes from tokens; breakpoints from the table below.
6. Covers: `EntityImage` only.
7. Panel title outside the card; no redundant subtext.
8. Privacy: no exact listen timestamps anywhere.

## Server artwork resolver contract

One module: `server/src/music_dashboard/infrastructure/persistence/artwork_resolver.py`, extracted from the `statistics_queries.py` artwork join (`artwork` by MBID, `artwork_name` by `name:` key, `validate_image_url`) and `artist_cover_fallback.py`. Behavior must match current rankings exactly.

```python
ArtworkType = Literal["artist", "album", "track"]

def resolve_artwork(
    c: Connection,
    entity_type: ArtworkType,
    keys: Sequence[str],          # MBID uuid strings and/or stable "name:" ranking keys
    params: dict[str, Any],       # must carry shared "casefold"/"whitespace" fold params
) -> dict[str, str]:              # key -> validated image_url; missing keys are absent
    """Photo/cover by id, else by name key; for artist (and album/track credited artists
    when the item itself has none) fall back to an existing album cover. Read-only."""
```

- `entity_type="artist"` includes the album-cover fallback (`artist_cover_fallbacks`). Albums/tracks: id then name key only; `artist_fallback=True` kwarg (default False) adds the credited artist's image when the item has none.
- Rankings (`statistics_queries.py`), history feed (`public_history_queries.py`) and artist endpoints (`artist_queries.py`) call it; no other artwork SQL.
- DTO contract (unchanged shape): `PublicSong.image_url` (history feed, artist recent), `ArtistSummary.image_url`, `ArtistRankedItem.image_url`, `RankedEntity.image_url` are `str | None`, validated https URL or null. For a song, the image key is the album, else the track.
- Parity: an item with a cover in rankings has the same cover in History and artist pages.
