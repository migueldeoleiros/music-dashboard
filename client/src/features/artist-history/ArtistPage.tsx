import { useQuery } from '@tanstack/react-query'
import type { components } from '../../api/schema'
import { artistApi, fetchHistoryJson, HistoryApiError, type ArtistHeatmap, type ArtistRankingPage, type ArtistSummary, type PublicSong } from '../../api/history'
import { EntityImage, FloatingNav, Panel } from '../../components/ui'
import { SongRow } from '../../components/ui/SongRow'
import { HeatmapPanel } from '../dashboard/HeatmapPanel'
import { RankingList } from '../dashboard/RankingList'
import { browserTimezone, todayInZone } from '../dashboard/filters'
import { FeedError, FeedSkeleton, LoadMore } from './FeedParts'
import { HeatmapSkeleton, RankingSkeleton } from '../dashboard/ChartSkeleton'
import { usePagedFeed } from './usePagedFeed'
import { formatMetric, metricParams, useMetric, type Metric } from '../../app/metric'
import '../../styles/artist-history.css'

const NO_COVERAGE = { included_listens: 0, excluded_listens: 0, total_listens: 0 }

function Rankings({ artistKey, dimension }: { artistKey: string; dimension: 'albums' | 'tracks' }) {
  const metric = useMetric()
  const feed = usePagedFeed<ArtistRankingPage['items'][number]>(`${artistApi(artistKey)}/rankings/${dimension}?${new URLSearchParams({ limit: '10', ...metricParams(metric) })}`)
  const items = feed.pages.flatMap(page => page.items)
  const data = { dimension, items: items.map(item => ({ ...item, mbid: null, metadata_state: item.label ? 'resolved' : 'missing' })) } as unknown as components['schemas']['RankingResponse']
  return <Panel title={`Top ${dimension}`} variant="ranking">
    {feed.restarted && <p role="status">Listening data changed. Rankings reloaded.</p>}
    {!feed.loading && !feed.error && <RankingList data={data} excludeArtistKey={artistKey} />}
    {feed.loading && <RankingSkeleton label={`Top ${dimension}`} />}
    <FeedError error={feed.error} retry={feed.retry} />
  </Panel>
}

function Recent({ artistKey }: { artistKey: string }) {
  const feed = usePagedFeed<PublicSong>(`${artistApi(artistKey)}/recent?limit=50`)
  const items = feed.pages.flatMap(page => page.items)
  return <Panel title="Recent songs">
    {feed.restarted && <p role="status">Listening data changed. Recent songs reloaded.</p>}
    {feed.loading && !items.length && <FeedSkeleton />}
    <ol className="entity-list">{items.map((song, index) => <SongRow key={index} song={song} excludeArtistKey={artistKey} />)}</ol>
    {!feed.loading && !feed.error && !items.length && <p className="entity-empty">No recent songs yet.</p>}
    <FeedError error={feed.error} retry={feed.retry} />
    <LoadMore {...feed} onLoad={feed.loadMore} skeleton={items.length > 0} />
  </Panel>
}

function addDays(day: string, delta: number) {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + delta)
  return date.toISOString().slice(0, 10)
}

// Rolling last year for this artist only. The artist endpoint is per calendar year, so merge current + previous year.
function Heatmap({ artistKey }: { artistKey: string }) {
  const metric = useMetric()
  const query = useQuery({
    queryKey: ['artist-heatmap', artistKey, browserTimezone(), metric],
    queryFn: async ({ signal }) => {
      const load = async (tz: string, year?: number) => fetchHistoryJson<ArtistHeatmap>(`${artistApi(artistKey)}/heatmap?${new URLSearchParams({ tz, ...(year === undefined ? {} : { year: String(year) }), ...metricParams(metric) })}`, signal)
      let tz = browserTimezone()
      let current: ArtistHeatmap
      try { current = await load(tz) } catch (error) {
        if (tz === 'UTC' || !(error instanceof HistoryApiError && error.status === 400)) throw error
        tz = 'UTC'
        current = await load(tz)
      }
      const previous = await load(tz, current.year - 1).catch(() => null)
      const to = todayInZone(tz)
      const from = addDays(to, -364)
      const cells = [...(previous?.cells ?? []), ...current.cells].filter(cell => cell.day >= from && cell.day <= to)
      return { cells, range: { from, to, tz }, week_starts_on: 'monday', fixture: false, generation: 0, last_updated: null, coverage: { listens: NO_COVERAGE } } as unknown as components['schemas']['HeatmapResponse']
    },
    retry: false,
  })
  return <Panel title="Daily listening" variant="heatmap">
    {query.isPending && <HeatmapSkeleton label="Daily listening" from={addDays(todayInZone(browserTimezone()), -364)} to={todayInZone(browserTimezone())} />}
    <FeedError error={query.error} retry={() => void query.refetch()} />
    {query.data && <HeatmapPanel data={query.data} />}
  </Panel>
}

export function ArtistPage({ artistKey }: { artistKey: string }) {
  const metric: Metric = useMetric()
  const query = useQuery({ queryKey: ['artist-summary', artistKey, metric], queryFn: ({ signal }) => fetchHistoryJson<ArtistSummary>(`${artistApi(artistKey)}?${new URLSearchParams(metricParams(metric))}`, signal), retry: false })
  const artist = query.data
  const missing = query.error instanceof HistoryApiError && query.error.status === 404
  return <main className="app artist-page">
    <FloatingNav items={['home', 'history']} />
    {query.isPending && <p role="status">Loading artist…</p>}
    {missing ? <section><h1>Artist not found</h1><p>This artist link is no longer available. Return to the dashboard to find an artist.</p></section> : <FeedError error={query.error} retry={() => void query.refetch()} />}
    {artist && <>
      <header className="artist-profile"><EntityImage src={artist.image_url} label={artist.label} size="hero" decorative /><div><h1>{artist.label}</h1><p className="artist-total">{formatMetric(artist.total_listens, metric)}{metric === 'listens' && <> <span>listens</span></>}</p>{artist.genres.length > 0 && <p className="entity-secondary">{artist.genres.join(' · ')}</p>}</div></header>
      <Heatmap key={artistKey} artistKey={artistKey} />
      <div className="grid-2">
        <Rankings key={`tracks-${artistKey}`} artistKey={artistKey} dimension="tracks" />
        <Rankings key={`albums-${artistKey}`} artistKey={artistKey} dimension="albums" />
      </div>
      <Recent key={artistKey} artistKey={artistKey} />
    </>}
  </main>
}
