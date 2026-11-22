import { useEffect, useRef } from 'react'
import type { PublicSong } from '../../api/history'
import { EntityImage } from '../../components/ui/EntityImage'
import { ArtistLink, ArtistLinks } from './ArtistLinks'

export function SongRow({ song }: { song: PublicSong }) {
  const only = song.artists.length === 1 ? song.artists[0] : null
  return <li className="entity-row song-row">
    <EntityImage src={song.image_url} label={song.title || 'Missing title'} size="md" decorative />
    <span className="entity-text">
      <span className="entity-label">{only ? <ArtistLink artist={only}>{song.title || 'Missing title'}</ArtistLink> : song.title || 'Missing title'}</span>
      <span className="entity-secondary"><ArtistLinks artists={song.artists} />{song.album && <> · {only ? <ArtistLink artist={only}>{song.album}</ArtistLink> : song.album}</>}</span>
    </span>
  </li>
}

export function FeedError({ error, retry }: { error: Error | null; retry: () => void }) {
  return error ? <div role="alert" className="error"><p>{error.message}</p><button type="button" onClick={retry}>Retry</button></div> : null
}

export function LoadMore({ hasMore, loading, error, onLoad, automatic = false }: { hasMore: boolean; loading: boolean; error: Error | null; onLoad: () => void; automatic?: boolean }) {
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
    {loading && <p role="status">Loading…</p>}
    {hasMore && <button type="button" disabled={loading || !!error} onClick={onLoad}>Load more</button>}
  </div>
}
