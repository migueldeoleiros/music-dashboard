# UI token contract

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

- Add tokens only by editing this file and `global.css` in the same change; never rename or remove one.
- Key-stable identity colors (`stableColor`) may stay hash-derived; ordered series use `--series-N`
  (wrap after 10) and `--series-other` for "Other".
- Styles live in the file for their area (below); `global.css` holds tokens, reset and shared control sizing.

## Style files

| File | Contents |
| --- | --- |
| `client/src/styles/global.css` (token values) and `styles/shell.css` | shell, period bar, layout, coverage |
| `client/src/styles/charts.css` | charts, heatmap scroller, map controls |
| `client/src/styles/entity-list.css` | entity list, rankings, country panel/sheet |

## Breakpoints and touch rules

CSS custom properties cannot be used in media queries, so the breakpoints are literals. Keep them in sync:

| Name | Query | Where it is used |
| --- | --- | --- |
| mobile | `(max-width: 720px)` | `shell.css`, `charts.css`, `entity-list.css`, `global.css`; JS mirrors: `MOBILE_QUERY` (`components/charts/theme.ts`) and `SHEET_QUERY` (`features/origin-map/CountryArtistsPanel.tsx`) |
| tablet | `(max-width: 1100px)` | `shell.css` (4-up rankings become 2-up) |
| touch | `(pointer: coarse)` | 44px targets and 16px inputs also apply on coarse tablets wider than 720px; charts switch to tap tooltips and touch pan/pinch |
| hover | `(hover: hover)` | hover-only decoration is wrapped in this query; nothing depends on hover |
| reduced motion | `(prefers-reduced-motion: reduce)` | CSS animations/transitions near-zero; ECharts `animation: false` |

Behavior at the mobile breakpoint: period chips become one horizontally scrollable row; custom range is a single column; charts use `--chart-h-narrow`; the heatmap scrolls sideways inside its own region, parked at the latest weeks; the country panel becomes a modal bottom sheet (`role=dialog`, backdrop, page scroll lock, focus trap, Escape, tap outside, swipe-down on the handle, safe-area insets).

## Map selection

`App` owns `selectedCountry`. Map click, keyboard chip or the same country again toggles it; the panel's close button and Escape clear it. Changing the period keeps the selection and the panel refetches with the shared filters (empty ranges show the panel's own empty state). On wide screens the panel sits beside the map (map height unchanged); on mobile it is the sheet. Backdrop clicks within 500 ms of opening are ignored because the browser's compatibility click for the tap that opened the sheet would otherwise close it immediately.
