// Shared chart theme: reads the CSS token contract (docs/ui-tokens.md) at runtime so canvas
// charts follow whatever values the theme lane sets. Fallbacks only apply when a token is missing.

export type ChartTheme = {
  accent: string
  text: string
  textMuted: string
  border: string
  surface: string
  surface2: string
  font: string
  radiusSm: number
  radiusMd: number
  /** --series-1 … --series-10, in order */
  series: string[]
  other: string
  /** Tooltip/emphasis shadow color derived from --bg. */
  shadow: string
  /** Primary pointer is touch: tooltips open on tap, map pans/pinches, bigger marks. */
  coarse: boolean
  /** Viewport at or below the mobile breakpoint (keep in sync with `--bp-mobile` in docs/ui-tokens.md). */
  narrow: boolean
  reducedMotion: boolean
}

export const MOBILE_QUERY = '(max-width: 720px)'
const matches = (query: string) => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches

const FALLBACK_SERIES = [
  'hsl(200 62% 68%)', 'hsl(155 60% 68%)', 'hsl(260 60% 72%)', 'hsl(320 58% 70%)', 'hsl(10 62% 68%)',
  'hsl(40 66% 68%)', 'hsl(75 58% 66%)', 'hsl(180 56% 66%)', 'hsl(230 60% 72%)', 'hsl(350 60% 70%)',
]

export function readTheme(el?: Element): ChartTheme {
  const root = el ?? (typeof document !== 'undefined' ? document.documentElement : undefined)
  const cs = root ? getComputedStyle(root) : undefined
  const v = (name: string, fallback: string) => cs?.getPropertyValue(name).trim() || fallback
  const px = (name: string, fallback: number) => {
    const n = parseFloat(v(name, ''))
    return Number.isFinite(n) ? n : fallback
  }
  return {
    accent: v('--accent', '#2ca7d3'),
    text: v('--text', '#e9edf2'),
    textMuted: v('--text-muted', '#a2aab6'),
    border: v('--border', '#2b3038'),
    surface: v('--surface', '#15181d'),
    surface2: v('--surface-2', '#1b2027'),
    font: v('--font', 'system-ui, sans-serif'),
    radiusSm: px('--radius-sm', 5),
    radiusMd: px('--radius-md', 8),
    series: FALLBACK_SERIES.map((f, i) => v(`--series-${i + 1}`, f)),
    other: v('--series-other', '#8a8f98'),
    shadow: mixColors(v('--bg', '#0a0f17'), '#000000', 0.5),
    coarse: matches('(pointer: coarse)'),
    narrow: matches(MOBILE_QUERY),
    reducedMotion: matches('(prefers-reduced-motion: reduce)'),
  }
}

/** Ordered series color (wraps after 10); the "Other" aggregate always uses --series-other. */
export function seriesColor(t: ChartTheme, index: number, isOther = false): string {
  return isOther ? t.other : t.series[index % t.series.length]
}

// ---------- color math (hex / rgb() / hsl() only) ----------
type RGBA = [number, number, number, number]

export function parseColor(input: string): RGBA | null {
  const c = input.trim().toLowerCase()
  const hex = /^#([0-9a-f]{3,8})$/.exec(c)
  if (hex) {
    let h = hex[1]
    if (h.length === 3 || h.length === 4) h = [...h].map((x) => x + x).join('')
    if (h.length !== 6 && h.length !== 8) return null
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16)
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1]
  }
  const fn = /^(rgba?|hsla?)\((.*)\)$/.exec(c)
  if (!fn) return null
  const nums = fn[2].match(/-?\d*\.?\d+/g)?.map(Number)
  if (!nums || nums.length < 3) return null
  const alphaRaw = nums[3]
  const alpha = alphaRaw === undefined ? 1 : /%\s*$/.test(fn[2]) ? alphaRaw / 100 : alphaRaw
  if (fn[1].startsWith('rgb')) return [nums[0], nums[1], nums[2], alpha]
  const h = (((nums[0] % 360) + 360) % 360) / 360
  const s = nums[1] / 100
  const l = nums[2] / 100
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const chan = (t0: number) => {
    const t = (t0 + 1) % 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return [chan(h + 1 / 3) * 255, chan(h) * 255, chan(h - 1 / 3) * 255, alpha]
}

/** Linear mix: t=0 -> a, t=1 -> b. Unparseable inputs snap to the nearer endpoint. */
export function mixColors(a: string, b: string, t: number): string {
  const k = Math.min(1, Math.max(0, t))
  const ca = parseColor(a)
  const cb = parseColor(b)
  if (!ca || !cb) return k >= 0.5 ? b : a
  const m = ca.map((x, i) => x + (cb[i] - x) * k)
  return `rgb(${Math.round(m[0])} ${Math.round(m[1])} ${Math.round(m[2])})`
}

// ---------- tooltip ----------
/** HTML-escape API-provided text before it goes into a tooltip (ECharts renders HTML). */
export function esc(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

export function tooltipStyle(t: ChartTheme) {
  return {
    confine: true,
    // Touch has no hover: open on tap. Mouse keeps hover + click.
    triggerOn: t.coarse ? ('click' as const) : ('mousemove|click' as const),
    enterable: false,
    backgroundColor: t.surface,
    borderColor: t.border,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: t.text, fontFamily: t.font, fontSize: 12 },
    extraCssText: `border-radius:${t.radiusMd}px;box-shadow:0 6px 20px ${t.shadow};font-variant-numeric:tabular-nums;`,
  }
}

export const tipTitle = (t: ChartTheme, text: string) =>
  `<div style="color:${esc(t.textMuted)};margin-bottom:4px">${esc(text)}</div>`

export function tipRow(t: ChartTheme, row: { color?: string; label: string; value: string; sub?: string }) {
  const dot = row.color
    ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${esc(row.color)}"></span>`
    : ''
  const sub = row.sub ? ` <span style="color:${esc(t.textMuted)}">${esc(row.sub)}</span>` : ''
  return `<div style="display:flex;justify-content:space-between;gap:16px;align-items:center"><span>${dot}${esc(row.label)}</span><span><b style="font-variant-numeric:tabular-nums">${esc(row.value)}</b>${sub}</span></div>`
}
