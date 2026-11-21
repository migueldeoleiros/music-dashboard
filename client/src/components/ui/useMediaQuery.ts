import { useSyncExternalStore } from 'react'

const supported = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'

/** Live `matchMedia` match; false where unsupported (SSR, jsdom without a stub). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (!supported()) return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', notify)
      return () => list.removeEventListener('change', notify)
    },
    () => supported() && window.matchMedia(query).matches,
    () => false,
  )
}
