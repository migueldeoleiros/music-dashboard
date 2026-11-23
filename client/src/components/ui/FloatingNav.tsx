import { IconButton } from './IconButton'
import type { IconName } from './Icon'

export type NavTarget = 'home' | 'history'

// Fixed order: Home always first.
const TARGETS: { id: NavTarget; href: string; label: string; icon: IconName }[] = [
  { id: 'home', href: '/', label: 'Home', icon: 'home' },
  { id: 'history', href: '/history', label: 'Listening history', icon: 'history' },
]

/**
 * Top-left floating bubble (one container) of icon-only buttons. Pass the targets for the page:
 * dashboard ['history'], History ['home'], artist ['home', 'history'].
 */
export function FloatingNav({ items }: { items: NavTarget[] }) {
  const shown = TARGETS.filter(t => items.includes(t.id))
  return <nav className="floating-nav" aria-label="Page navigation">
    {shown.map(t => <IconButton key={t.id} icon={t.icon} label={t.label} href={t.href} bare />)}
  </nav>
}
