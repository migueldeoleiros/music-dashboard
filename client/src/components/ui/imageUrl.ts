export type ImageSize = 'sm' | 'md' | 'lg' | 'hero'

export const DEEZER_SIZES = [120, 250, 500, 1000] as const
const DEEZER_HOSTS = new Set(['cdn-images.dzcdn.net', 'e-cdns-images.dzcdn.net'])
const SIZE_SEGMENT = /\/\d+x\d+-/

/** Deezer pixel size for a render size. High-density screens (dpr > 1) get the next step up. */
export function deezerSize(size: ImageSize, dpr = 1): (typeof DEEZER_SIZES)[number] {
  const hiDpi = dpr > 1
  switch (size) {
    case 'sm': return hiDpi ? 250 : 120
    case 'md': return 250
    case 'lg': return 500
    case 'hero': return hiDpi ? 1000 : 500
  }
}

/** Rewrites the `{N}x{N}-` segment of Deezer CDN URLs; every other URL is returned unchanged. */
export function sizedImageUrl(src: string, size: ImageSize, dpr = 1): string {
  let url: URL
  try { url = new URL(src) } catch { return src }
  if (url.protocol !== 'https:' || !DEEZER_HOSTS.has(url.hostname) || !SIZE_SEGMENT.test(url.pathname)) return src
  const n = deezerSize(size, dpr)
  url.pathname = url.pathname.replace(SIZE_SEGMENT, `/${n}x${n}-`)
  return url.toString()
}

const LOCAL_ARTWORK = /^\/artwork\/([0-9a-f]{32})\.webp$/

/** Server-converted WebP copies: `/artwork/<key>.webp` -> `/artwork/<key>-<N>.webp` (120/250/500/1000). */
export function localArtworkUrl(src: string, size: ImageSize, dpr = 1): string | null {
  const match = LOCAL_ARTWORK.exec(src)
  return match ? `/artwork/${match[1]}-${deezerSize(size, dpr)}.webp` : null
}

/** Local-only sources: same-origin paths, data: and blob: URLs. */
function isLocal(src: string): boolean {
  return (src.startsWith('/') && !src.startsWith('//')) || src.startsWith('data:image/') || src.startsWith('blob:')
}

/**
 * Final `src` to load, or null for "show the placeholder". Mock mode never loads external URLs;
 * otherwise only http(s) and local sources are accepted.
 */
export function resolveImageSrc(src: string | null | undefined, size: ImageSize, opts: { mock?: boolean; dpr?: number } = {}): string | null {
  if (!src) return null
  const local = localArtworkUrl(src, size, opts.dpr)
  if (local) return local
  if (isLocal(src)) return src
  if (opts.mock) return null
  if (!/^https?:\/\//i.test(src)) return null
  return sizedImageUrl(src, size, opts.dpr)
}

/** Up to two initials (first letters of the first two words). */
export function initials(label: string): string {
  const words = label.trim().split(/\s+/).filter(Boolean)
  const letters = words.map(word => Array.from(word).find(ch => /[\p{L}\p{N}]/u.test(ch)) ?? '').filter(Boolean)
  if (!letters.length) return '?'
  return letters.slice(0, 2).join('').toUpperCase()
}

/** Deterministic 1..10 index into the --series-N tokens. */
export function hueIndex(label: string): number {
  let hash = 5381
  for (const ch of label) hash = ((hash * 33) ^ ch.codePointAt(0)!) >>> 0
  return (hash % 10) + 1
}
