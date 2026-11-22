import { Fragment } from 'react'
import type { ArtistCredit } from '../../api/history'

export function reliableArtistKey(key: string | null | undefined): key is string {
  return !!key && ((key.startsWith('name:') && key.slice(5).trim().length > 0) || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(key))
}

export function ArtistLink({ artist, children }: { artist: ArtistCredit; children?: React.ReactNode }) {
  return reliableArtistKey(artist.key) ? <a className="artist-link" href={`/artists/${encodeURIComponent(artist.key)}`}>{children ?? artist.label}</a> : <>{children ?? artist.label}</>
}

export function ArtistLinks({ artists }: { artists: ArtistCredit[] }) {
  return <>{artists.map((artist, index) => <Fragment key={index}>{index > 0 && ', '}<ArtistLink artist={artist} /></Fragment>)}</>
}
