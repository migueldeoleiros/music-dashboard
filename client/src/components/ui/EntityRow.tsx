import type { ReactNode } from 'react'
import { EntityImage, type EntityImageProps } from './EntityImage'

export type EntityRowProps = {
  label: string
  src?: string | null
  /** Optional line under the label. */
  secondary?: ReactNode
  /** Right-aligned tabular value (e.g. listens). */
  value?: ReactNode
  rank?: number
  /** Hide the image column entirely (e.g. genres). Default true. */
  showImage?: boolean
  shape?: EntityImageProps['shape']
}

// One <li>; render inside EntityList. The image is decorative because the label is adjacent text.
export function EntityRow({ label, src, secondary, value, rank, showImage = true, shape }: EntityRowProps) {
  return <li className="entity-row">
    {rank !== undefined && <span className="entity-rank">{String(rank).padStart(2, '0')}</span>}
    {showImage && <EntityImage src={src} label={label} size="md" shape={shape} decorative />}
    <span className="entity-text"><span className="entity-label" title={label}>{label}</span>{secondary && <small className="entity-secondary">{secondary}</small>}</span>
    {value !== undefined && <span className="entity-value">{value}</span>}
  </li>
}
