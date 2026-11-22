export type IconName = 'home' | 'history' | 'add'

// Material Symbols (24px viewBox) paths. Add a name here to add an icon; never inline other SVGs in pages.
const PATHS: Record<IconName, string> = {
  home: 'M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z',
  history: 'M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z',
  add: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z',
}

/** Decorative inline SVG (aria-hidden); the surrounding control provides the accessible name. Uses currentColor. */
export function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d={PATHS[name]} /></svg>
}
