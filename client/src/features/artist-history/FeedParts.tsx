import { useEffect, useRef } from 'react'
import { SongRowSkeleton } from '../../components/ui/Skeleton'

const SKELETON_ROWS = 3

/** Three song-row placeholders: first load of a feed and the "load more" tail. */
export function FeedSkeleton() {
  return <ol className="entity-list" aria-hidden="true">{Array.from({ length: SKELETON_ROWS }, (_, i) => <SongRowSkeleton key={i} />)}</ol>
}

export function FeedError({ error, retry }: { error: Error | null; retry: () => void }) {
  return error ? <div role="alert" className="error"><p>{error.message}</p><button type="button" onClick={retry}>Retry</button></div> : null
}

export function LoadMore({ hasMore, loading, error, onLoad, automatic = false, skeleton = false }: { hasMore: boolean; loading: boolean; error: Error | null; onLoad: () => void; automatic?: boolean; skeleton?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!automatic || !hasMore || loading || error || !ref.current || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) onLoad()
    }, { rootMargin: '200px' })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [automatic, hasMore, loading, error, onLoad])
  return <div ref={ref} className="feed-paging">
    {loading && <p role="status" className="sr-only">Loading…</p>}
    {loading && skeleton && <FeedSkeleton />}
    {hasMore && <button type="button" disabled={loading || !!error} onClick={onLoad}>Load more</button>}
  </div>
}
