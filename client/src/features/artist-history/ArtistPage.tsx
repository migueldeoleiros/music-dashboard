import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { artistApi, fetchHistoryJson, HistoryApiError, type ArtistHeatmap, type ArtistRankedItem, type ArtistSummary, type PublicSong } from '../../api/history'
import { EntityImage } from '../../components/ui/EntityImage'
import { EntityRow } from '../../components/ui/EntityRow'
import { browserTimezone } from '../dashboard/filters'
import { FeedError, LoadMore, SongRow } from './FeedParts'
import { usePagedFeed } from './usePagedFeed'
import '../../styles/artist-history.css'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

function Rankings({ artistKey, dimension }: { artistKey: string; dimension: 'albums' | 'tracks' }) {
  const feed = usePagedFeed<ArtistRankedItem>(`${artistApi(artistKey)}/rankings/${dimension}?limit=50`)
  const items = feed.pages.flatMap(page => page.items)
  return <section className="card" aria-label={`Top ${dimension}`}>
    <h2>Top {dimension}</h2>
    {feed.restarted && <p role="status">Listening data changed. Rankings reloaded.</p>}
    <ol className="entity-list">{items.map((item, index) => <EntityRow key={item.key} rank={index + 1} label={item.label || 'Missing metadata'} src={item.image_url} value={`${number.format(item.listens)} listens`} />)}</ol>
    {!feed.loading && !feed.error && !items.length && <p className="entity-empty">No {dimension} yet.</p>}
    <FeedError error={feed.error} retry={feed.retry} />
    <LoadMore {...feed} onLoad={feed.loadMore} />
  </section>
}

function Recent({ artistKey }: { artistKey: string }) {
  const feed = usePagedFeed<PublicSong>(`${artistApi(artistKey)}/recent?limit=50`)
  const items = feed.pages.flatMap(page => page.items)
  return <section className="card" aria-label="Recent songs">
    <h2>Recent songs</h2>
    {feed.restarted && <p role="status">Listening data changed. Recent songs reloaded.</p>}
    <ol className="entity-list">{items.map((song, index) => <SongRow key={index} song={song} />)}</ol>
    {!feed.loading && !feed.error && !items.length && <p className="entity-empty">No recent songs yet.</p>}
    <FeedError error={feed.error} retry={feed.retry} />
    <LoadMore {...feed} onLoad={feed.loadMore} />
  </section>
}

function YearGrid({ data }: { data: ArtistHeatmap }) {
  const first = `${String(data.year).padStart(4, '0')}-01-01`
  const days: string[] = []
  const day = new Date(`${first}T12:00:00Z`)
  const offset = (day.getUTCDay() + 6) % 7
  while (day.getUTCFullYear() === data.year) {
    days.push(day.toISOString().slice(0, 10))
    day.setUTCDate(day.getUTCDate() + 1)
  }
  const counts = new Map(data.cells.map(cell => [cell.day, cell.listens]))
  const max = Math.max(1, ...data.cells.map(cell => cell.listens))
  return <div className="artist-heat-scroll" tabIndex={0} role="region" aria-label={`Daily listening in ${data.year}, weeks start Monday; scroll sideways`}>
    <div className="artist-heat-grid" role="img" aria-label={`Artist daily listening in ${data.year}, ${data.timezone}, weeks start Monday`}>
      {Array.from({ length: offset }, (_, index) => <i key={index} aria-hidden="true" />)}
      {days.map(date => {
        const count = counts.get(date) ?? 0
        return <i key={date} title={`${date}: ${count} listens`} style={{ background: `color-mix(in srgb, var(--accent) ${count ? 20 + count / max * 80 : 0}%, var(--surface-2))` }} />
      })}
    </div>
    <table className="sr-only"><caption>Daily artist listens in {data.year}</caption><thead><tr><th>Day</th><th>Listens</th></tr></thead><tbody>{data.cells.map(cell => <tr key={cell.day}><th scope="row">{cell.day}</th><td>{cell.listens}</td></tr>)}</tbody></table>
  </div>
}

function Heatmap({ artistKey }: { artistKey: string }) {
  const [year, setYear] = useState<number>()
  const [timezone, setTimezone] = useState(browserTimezone)
  const params = new URLSearchParams({ tz: timezone })
  if (year !== undefined) params.set('year', String(year))
  const query = useQuery({
    queryKey: ['artist-heatmap', artistKey, year, timezone],
    queryFn: async ({ signal }) => {
      try { return await fetchHistoryJson<ArtistHeatmap>(`${artistApi(artistKey)}/heatmap?${params}`, signal) }
      catch (error) {
        if (timezone !== 'UTC' && error instanceof HistoryApiError && error.status === 400) {
          const fallback = new URLSearchParams(params)
          fallback.set('tz', 'UTC')
          return fetchHistoryJson<ArtistHeatmap>(`${artistApi(artistKey)}/heatmap?${fallback}`, signal)
        }
        throw error
      }
    },
    retry: false,
  })
  const data = query.data
  return <section className="card" aria-label="Artist daily listening">
    <header className="artist-heat-header"><h2>Daily listening{data ? ` · ${data.year}` : ''}</h2>
      {data && <label>Year <select value={data.year} onChange={event => { setYear(Number(event.target.value)); if (data.timezone === 'UTC') setTimezone('UTC') }}>{[...new Set([...data.years, data.year])].sort((a, b) => b - a).map(value => <option key={value} value={value}>{value}</option>)}</select></label>}
    </header>
    {query.isPending && <p role="status">Loading daily listening…</p>}
    <FeedError error={query.error} retry={() => void query.refetch()} />
    {data && <><p className="entity-secondary">{data.timezone}{data.timezone !== browserTimezone() ? ' (UTC fallback)' : ''} · Monday weeks</p><YearGrid data={data} /></>}
  </section>
}

export function ArtistPage({ artistKey }: { artistKey: string }) {
  const query = useQuery({ queryKey: ['artist-summary', artistKey], queryFn: ({ signal }) => fetchHistoryJson<ArtistSummary>(artistApi(artistKey), signal), retry: false })
  const artist = query.data
  const missing = query.error instanceof HistoryApiError && query.error.status === 404
  return <main className="app artist-page">
    <nav aria-label="Page navigation"><a href="/">Dashboard</a> · <a href="/history">Listening history</a></nav>
    {query.isPending && <p role="status">Loading artist…</p>}
    {missing ? <section><h1>Artist not found</h1><p>This artist link is no longer available. Return to the dashboard to find an artist.</p></section> : <FeedError error={query.error} retry={() => void query.refetch()} />}
    {artist && <>
      <header className="artist-profile"><EntityImage src={artist.image_url} label={artist.label} size="hero" decorative /><div><h1>{artist.label}</h1><p className="artist-total">{number.format(artist.total_listens)} <span>all-time listens</span></p>{artist.genres.length > 0 && <p className="entity-secondary">{artist.genres.join(' · ')}</p>}{artist.byline && <p className="artist-byline">{artist.byline}</p>}</div></header>
      <Heatmap key={artistKey} artistKey={artistKey} />
      <div className="grid-2"><Rankings key={`albums-${artistKey}`} artistKey={artistKey} dimension="albums" /><Rankings key={`tracks-${artistKey}`} artistKey={artistKey} dimension="tracks" /></div>
      <Recent key={artistKey} artistKey={artistKey} />
    </>}
  </main>
}
