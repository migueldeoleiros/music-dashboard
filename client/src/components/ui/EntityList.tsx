import { Children, type ReactNode } from 'react'

export type EntityListProps = {
  /** Accessible name of the list (and of the scroll region when scrollable). */
  label: string
  children?: ReactNode
  /** Make the list a keyboard-focusable scroll region capped at this CSS height (e.g. '60vh'). */
  maxHeight?: string
  empty?: ReactNode
  className?: string
}

export function EntityList({ label, children, maxHeight, empty, className = '' }: EntityListProps) {
  if (Children.count(children) === 0) return <p className="entity-empty">{empty ?? 'Nothing to show.'}</p>
  const list = <ol className={`entity-list ${className}`.trim()} aria-label={label}>{children}</ol>
  if (!maxHeight) return list
  return <div className="entity-scroll" role="region" aria-label={label} tabIndex={0} style={{ maxHeight }}>{list}</div>
}
