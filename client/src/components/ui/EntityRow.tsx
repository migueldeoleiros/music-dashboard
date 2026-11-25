import type { ReactNode } from 'react'
import { EntityImage, type EntityImageProps } from './EntityImage'

export type EntityRowProps = {
  label: string
  /** Replaces the label text (e.g. a link); `label` stays the title/image name. */
  labelContent?: ReactNode
  src?: string | null
  /** Optional line under the label. */
  secondary?: ReactNode
  /** Right-aligned tabular value (e.g. listens). */
  value?: ReactNode
  rank?: number
  /** Hide the image column entirely (e.g. genres). Default true. */
  showImage?: boolean
  shape?: EntityImageProps['shape']
  /** Makes the whole row one button (stretched over the row, like `.row-link`); `label` is its name. */
  onSelect?: () => void
}

// One <li>; render inside EntityList. The image is decorative because the label is adjacent text.
export function EntityRow({ label, labelContent, src, secondary, value, rank, showImage = true, shape, onSelect }: EntityRowProps) {
  const content = onSelect ? <button type="button" className="row-link row-button" onClick={onSelect}>{labelContent ?? label}</button> : labelContent ?? label
  return <li className={`entity-row${onSelect ? ' row--link' : ''}`}>
    {rank !== undefined && <span className="entity-rank">{String(rank).padStart(2, '0')}</span>}
    {showImage && <EntityImage src={src} label={label} size="md" shape={shape} decorative />}
    <span className="entity-text"><span className="entity-label" title={label}>{content}</span>{secondary && <small className="entity-secondary">{secondary}</small>}</span>
    {value !== undefined && <span className="entity-value">{value}</span>}
  </li>
}
