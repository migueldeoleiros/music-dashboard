import { render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { ChartSkeleton, HeatmapSkeleton, skeletonKind } from '../src/features/dashboard/ChartSkeleton'
import { FeedSkeleton } from '../src/features/artist-history/FeedParts'
import { RowSkeleton } from '../src/components/ui/Skeleton'

it('maps endpoints to skeleton kinds', () => {
  expect(skeletonKind('/api/heatmap')).toBe('heatmap')
  expect(skeletonKind('/api/rankings/tracks')).toBe('ranking')
  expect(skeletonKind('/api/countries')).toBe('map')
  expect(skeletonKind('/api/evolution/genres')).toBe('evolution')
  expect(skeletonKind('/api/pies/artists')).toBe('pie')
})

it('heatmap skeleton draws one dim cell per day-slot of the real grid, with a single status', () => {
  const { container } = render(<HeatmapSkeleton label="Daily listening" from="2026-01-05" to="2026-01-18" />)
  expect(screen.getAllByRole('status')).toHaveLength(1)
  expect(screen.getByRole('status').textContent).toBe('Loading daily listening…')
  expect(container.querySelectorAll('.skeleton-heat-grid > i')).toHaveLength(14) // two Monday-start weeks
})

it('ranking skeleton is one hero placeholder, hidden from assistive tech', () => {
  const { container } = render(<ChartSkeleton path="/api/rankings/tracks" label="Top tracks" from="2026-01-01" to="2026-01-07" />)
  const list = container.querySelector('ol')!
  expect(list.getAttribute('aria-hidden')).toBe('true')
  expect(list.querySelectorAll('li')).toHaveLength(1)
  expect(list.querySelector('.entity-hero')).toBeTruthy()
})

it('feed skeleton shows three song rows; row skeleton can drop the image', () => {
  const { container } = render(<><FeedSkeleton /><ol><RowSkeleton showImage={false} /></ol></>)
  expect(container.querySelectorAll('.song-row')).toHaveLength(3)
  expect(within(container.querySelector('ol:last-child') as HTMLElement).queryByText('x')).toBeNull()
  expect(container.querySelectorAll('ol:last-child .skeleton-image')).toHaveLength(0)
})
