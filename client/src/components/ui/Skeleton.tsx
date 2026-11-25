import type { CSSProperties } from 'react'

/** The one shimmer placeholder. Decorative: wrap loading regions in a `role="status"` sr-only text instead. */
export function Skeleton({ className = '', style }: { className?: string; style?: CSSProperties }) {
  return <span className={`skeleton ${className}`.trim()} style={style} aria-hidden="true" />
}

/** Placeholder with the shape of `CompactRow` (rank, optional image, two text lines, count). */
export function RowSkeleton({ showImage = true }: { showImage?: boolean }) {
  return <li className="entity-row" aria-hidden="true">
    <Skeleton className="skeleton-rank" />
    {showImage && <Skeleton className="skeleton-image" />}
    <span className="entity-text"><Skeleton className="skeleton-line" style={{ width: '60%' }} /><Skeleton className="skeleton-line skeleton-line--small" style={{ width: '35%' }} /></span>
    <Skeleton className="skeleton-line skeleton-count" />
  </li>
}

/** Placeholder with the shape of `SongRow` (image + title | artist | album). */
export function SongRowSkeleton() {
  return <li className="entity-row song-row" aria-hidden="true">
    <Skeleton className="skeleton-image" />
    <span className="song-cols"><Skeleton className="skeleton-line" /><Skeleton className="skeleton-line" /><Skeleton className="skeleton-line" /></span>
  </li>
}

/** Placeholder with the shape of the #1 ranking hero card. Always the same size, whatever the list length. */
export function HeroSkeleton() {
  return <li className="entity-hero" aria-hidden="true">
    <Skeleton className="skeleton-hero-art" />
    <span className="entity-text"><Skeleton className="skeleton-line skeleton-line--hero" style={{ width: '65%' }} /><Skeleton className="skeleton-line" style={{ width: '35%' }} /></span>
  </li>
}
