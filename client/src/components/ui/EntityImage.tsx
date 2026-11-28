import { useState, type CSSProperties } from 'react'
import { hueIndex, initials, resolveImageSrc, type ImageSize } from './imageUrl'

export type EntityImageProps = {
  src?: string | null
  label: string
  size: ImageSize
  /** 'rounded' (default, --radius-md) or 'circle'. */
  shape?: 'rounded' | 'circle'
  /**
   * Set when the label is rendered as adjacent text (rows, hero): the image is then decorative
   * (alt="", placeholder aria-hidden) so screen readers don't read the name twice. Default false:
   * standalone images get alt=label.
   */
  decorative?: boolean
}

const dpr = () => (typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1)

export function EntityImage({ src, label, size, shape = 'rounded', decorative = false }: EntityImageProps) {
  const url = resolveImageSrc(src, size, { dpr: dpr() })
  const [failed, setFailed] = useState<string | null>(null)
  const cls = `entity-image entity-image--${size}${shape === 'circle' ? ' entity-image--circle' : ''}`
  if (!url || failed === url) {
    const style = { '--tile-hue': `var(--series-${hueIndex(label)})` } as CSSProperties
    return <span className={`${cls} entity-image--placeholder`} style={style} {...(decorative ? { 'aria-hidden': true } : { role: 'img', 'aria-label': label })}>
      <span aria-hidden="true">{initials(label)}</span>
    </span>
  }
  return <span className={cls}>
    <img src={url} alt={decorative ? '' : label} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(url)} />
  </span>
}
