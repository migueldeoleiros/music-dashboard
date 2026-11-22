import type { ReactNode } from 'react'
import { EntityImage, type EntityImageProps } from './EntityImage'

export type CompactRowProps = {
  label: string
  /** Whole row is one link (stretched title anchor). Omit for unresolved entities: no link. */
  href?: string
  src?: string | null
  /** Optional line under the title. Any links inside must be `.row-inline-link` (they sit above the row link). */
  secondary?: ReactNode
  /** Right-aligned tabular count. */
  count?: ReactNode
  rank?: number
  /** Hide the image column (genres). Default true. */
  showImage?: boolean
  shape?: EntityImageProps['shape']
}

/** Dashboard top-list row: image, title, count. One <li>; render inside EntityList. */
export function CompactRow({ label, href, src, secondary, count, rank, showImage = true, shape }: CompactRowProps) {
  return <li className={`entity-row${href ? ' row--link' : ''}`}>
    {rank !== undefined && <span className="entity-rank">{String(rank).padStart(2, '0')}</span>}
    {showImage && <EntityImage src={src} label={label} size="md" shape={shape} decorative />}
    <span className="entity-text">
      <span className="entity-label" title={label}>{href ? <a className="row-link" href={href}>{label}</a> : label}</span>
      {secondary && <small className="entity-secondary">{secondary}</small>}
    </span>
    {count !== undefined && <span className="entity-value">{count}</span>}
  </li>
}
