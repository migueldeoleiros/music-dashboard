/** YouTube Music search deep link. Opens search results (top hit is usually the item), not an exact item. */
export function ytMusicSearchUrl(...terms: Array<string | null | undefined>): string {
  const q = terms.map(t => t?.trim()).filter(Boolean).join(' ')
  return `https://music.youtube.com/search?q=${encodeURIComponent(q)}`
}
