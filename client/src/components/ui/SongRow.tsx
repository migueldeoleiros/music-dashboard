import { Fragment } from 'react'
import type { PublicSong } from '../../api/history'
import { artistHref } from '../../app/navigation'
import { reliableArtistKey, ArtistLink } from '../../features/artist-history/ArtistLinks'
import { EntityImage } from './EntityImage'

/**
 * Credits cell: first credited artist is plain text (the row link covers it); the others link
 * individually. If the first artist is unresolved the row has no link but resolved others still do.
 */
export function CreditNames({ artists, excludeArtistKey }: { artists: PublicSong['artists']; excludeArtistKey?: string }) {
  return <>{artists.map((a, i) => <Fragment key={i}>{i > 0 && ', '}{i > 0 && a.key !== excludeArtistKey ? <ArtistLink artist={a} className="row-inline-link" /> : a.label}</Fragment>)}</>
}

/** History / artist recent-songs row: image + columns title | artist | album. Row links to the primary artist. */
export function SongRow({ song, excludeArtistKey }: { song: PublicSong; excludeArtistKey?: string }) {
  const first = song.artists[0]
  const href = first && first.key !== excludeArtistKey && reliableArtistKey(first.key) ? artistHref(first.key) : undefined
  const title = song.title || 'Missing title'
  return <li className={`entity-row song-row${href ? ' row--link' : ''}`}>
    <EntityImage src={song.image_url} label={title} size="md" decorative />
    <span className="song-cols">
      <span className="song-title">{href ? <a className="row-link" href={href}>{title}</a> : title}</span>
      <span className="song-artist"><CreditNames artists={song.artists} excludeArtistKey={excludeArtistKey} /></span>
      <span className="song-album">{song.album}</span>
    </span>
  </li>
}
