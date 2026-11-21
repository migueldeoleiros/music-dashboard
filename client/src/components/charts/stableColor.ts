export const OTHER_COLOR = '#808080'

function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** Deterministic color per key, independent of rank/order/filter. */
export function stableColor(key: string, isOther = false): string {
  if (isOther) return OTHER_COLOR
  const h = hash(key)
  const hue = h % 360
  const sat = 55 + ((h >>> 9) % 15)
  // Bright enough for thin graphical marks against the dark chart surface,
  // including blue hues whose luminance is otherwise especially low.
  const light = 64 + ((h >>> 17) % 9)
  return `hsl(${hue} ${sat}% ${light}%)`
}
