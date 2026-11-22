import type { HistoryItem } from '../../api/history'
import { browserTimezone } from '../dashboard/filters'
import { FloatingNav } from '../../components/ui'
import { SongRow } from '../../components/ui/SongRow'
import { FeedError, LoadMore } from './FeedParts'
import { usePagedFeed } from './usePagedFeed'
import '../../styles/artist-history.css'

export function adjacentGroups(items: HistoryItem[]) {
  const groups: { label: string; items: HistoryItem[] }[] = []
  for (const item of items) {
    const previous = groups.at(-1)
    if (previous?.label === item.group) previous.items.push(item)
    else groups.push({ label: item.group, items: [item] })
  }
  return groups
}

export function HistoryPage() {
  const timezone = browserTimezone()
  const feed = usePagedFeed<HistoryItem>(`/api/history?${new URLSearchParams({ tz: timezone, limit: '50' })}`)
  const groups = adjacentGroups(feed.pages.flatMap(page => page.items))
  return <main className="app history-page">
    <FloatingNav items={['home']} />
    <header><h1>Listening history</h1></header>
    {feed.restarted && <p role="status" className="notice">Listening data changed or the page expired. History reloaded.</p>}
    {groups.map((group, index) => <section key={index} aria-label={group.label}>
      <h2>{group.label}</h2><ol className="entity-list">{group.items.map((song, index) => <SongRow key={index} song={song} />)}</ol>
    </section>)}
    {!feed.loading && !feed.error && !groups.length && <p className="entity-empty">No listening history yet.</p>}
    <FeedError error={feed.error} retry={feed.retry} />
    <LoadMore {...feed} onLoad={feed.loadMore} automatic />
  </main>
}
