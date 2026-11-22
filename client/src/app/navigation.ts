/** Internal navigation keeps dashboard filter URLs in browser history. */
export function artistHref(key: string): string {
  return `/artists/${encodeURIComponent(key)}`
}

export function navigate(href: string): void {
  window.history.pushState(null, '', href)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
