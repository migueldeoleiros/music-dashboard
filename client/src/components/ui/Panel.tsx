import { useId, type ReactNode } from 'react'

export type PanelProps = {
  /** Section title: rendered as the h2 above (outside) the card and used as its accessible name. */
  title: string
  variant?: 'default' | 'heatmap' | 'ranking'
  busy?: boolean
  children: ReactNode
}

/** The one section title + card frame (dashboard, artist page, History). Never hand-roll `.card-title`. */
export function Panel({ title, variant = 'default', busy, children }: PanelProps) {
  const titleId = useId()
  const card = ['card', variant === 'ranking' ? 'ranking-card' : '', variant === 'heatmap' ? 'card-heatmap' : ''].filter(Boolean).join(' ')
  return <div className={`card-group${variant === 'heatmap' ? ' card-group-heatmap' : ''}`}>
    <h2 className="card-title" id={titleId}>{title}</h2>
    <section className={card} aria-labelledby={titleId} aria-busy={busy}>{children}</section>
  </div>
}
