import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchHistoryJson, HistoryApiError, pageUrl, type FeedPage } from '../../api/history'

type State<T> = { url: string; pages: FeedPage<T>[]; loading: boolean; error: Error | null; restarted: boolean }

/** Append only within a generation. A stale cursor replaces the entire feed, never mixes pages. */
export function usePagedFeed<T>(url: string) {
  const [state, setState] = useState<State<T>>({ url, pages: [], loading: true, error: null, restarted: false })
  const current = useRef(state)
  const request = useRef<AbortController | null>(null)
  const run = useCallback(async (reset: boolean) => {
    if (!reset && request.current) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const before = current.current
    const last = before.pages.at(-1)
    const cursor = reset ? null : last?.next_cursor ?? null
    const update = (next: State<T>) => {
      if (controller.signal.aborted) return
      current.current = next
      setState(next)
    }
    update({ ...before, url, pages: reset ? [] : before.pages, loading: true, error: null })
    try {
      let page: FeedPage<T>
      let restart = false
      try {
        page = await fetchHistoryJson<FeedPage<T>>(pageUrl(url, cursor), controller.signal)
        restart = !!cursor && page.generation !== before.pages[0]?.generation
      } catch (error) {
        if (!(cursor && error instanceof HistoryApiError && error.status === 409)) throw error
        restart = true
        page = await fetchHistoryJson<FeedPage<T>>(url, controller.signal)
      }
      if (restart) {
        // A changed generation returned with a 200 also requires a fresh first page.
        if (page.generation !== before.pages[0]?.generation && cursor) {
          page = await fetchHistoryJson<FeedPage<T>>(url, controller.signal)
        }
        update({ url, pages: [page], loading: false, error: null, restarted: true })
      } else update({ url, pages: reset ? [page] : [...before.pages, page], loading: false, error: null, restarted: reset ? false : before.restarted })
    } catch (error) {
      update({ ...current.current, loading: false, error: error instanceof Error ? error : new Error('Could not load this view.') })
    } finally {
      if (request.current === controller) request.current = null
    }
  }, [url])
  useEffect(() => {
    current.current = { url, pages: [], loading: true, error: null, restarted: false }
    void run(true)
    return () => { request.current?.abort(); request.current = null }
  }, [run, url])
  // Hide old data during the render before the URL-change effect.
  const visible = state.url === url ? state : { url, pages: [], loading: true, error: null, restarted: false }
  return { ...visible, hasMore: !!visible.pages.at(-1)?.next_cursor, loadMore: () => void run(false), retry: () => void run(current.current.pages.length === 0) }
}
