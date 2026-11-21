# UI token contract

CSS custom properties on `:root` in `client/src/styles/global.css` are the only shared styling
interface between UI lanes. **Names are frozen; values may change** (theme lane C1). Components
and ECharts options read tokens (`var(--x)`, or `getComputedStyle(document.documentElement)` at
runtime for canvas charts) and never hard-code the values below.

| Token | Role | Initial value |
| --- | --- | --- |
| `--bg` | Page background | `#0a0f17` |
| `--surface` | Cards, panels | `#121a26` |
| `--surface-2` | Raised/inset surfaces: inputs, buttons, image placeholders | `#1b2635` |
| `--border` | Hairlines and outlines | `#2f3e54` |
| `--text` | Primary text | `#f3f7fc` |
| `--text-muted` | Secondary text, captions | `#aebbcc` |
| `--accent` | Primary accent: active state, focus ring, key marks | `#2ca7d3` |
| `--accent-soft` | Translucent accent for fills, hover and selected backgrounds | `rgb(44 167 211 / 18%)` |
| `--radius-sm` | Inputs, buttons, small tiles | `5px` |
| `--radius-md` | Cards, panels, images | `8px` |
| `--radius-lg` | Pills, hero cards | `30px` |
| `--font` | Font stack | `'Inter Variable', system-ui, sans-serif` |
| `--series-1` … `--series-10` | Ordered categorical chart series colors | `hsl(H 72% ~60%)` with H = 197, 12, 145, 262, 40, 330, 172, 85, 225, 295 (see `global.css`) |
| `--series-other` | The "Other" aggregate series and no-data marks | `#7d8696` |

`--muted` is a deprecated alias of `--text-muted`, kept so existing rules keep working. New code
uses `--text-muted`.

## Rules

- Add tokens only by editing this file and `global.css` in the same change; never rename or remove one.
- Key-stable identity colors (`stableColor`) may stay hash-derived; ordered series use `--series-N`
  (wrap after 10) and `--series-other` for "Other".
- Each lane styles only in its own file; `global.css` holds tokens, reset and legacy rules.

## Style file ownership

| File | Lane |
| --- | --- |
| `client/src/styles/global.css` (token values) and `styles/shell.css` | C1 shell/theme |
| `client/src/styles/charts.css` | C2 charts |
| `client/src/styles/entity-list.css` | C3 entity list / rankings / country panel |
