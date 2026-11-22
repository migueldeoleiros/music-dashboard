import { IconButton } from './IconButton'
import type { IconName } from './Icon'

export type NavTarget = 'home' | 'history'

// Fixed order: Home always first.
const TARGETS: { id: NavTarget; href: string; label: string; icon: IconName }[] = [
  { id: 'home', href: '/', label: 'Home', icon: 'home' },
  { id: 'history', href: '/history', label: 'Listening history', icon: 'history' },
]

/**
 * Top-left floating stack of icon-only buttons. Pass the targets for the page:
 * dashboard ['history'], History ['home'], artist ['home', 'history'].
 */
export function FloatingNav({ items }: { items: NavTarget[] }) {
  return <nav className="floating-nav" aria-label="Page navigation">
    {TARGETS.filter(t => items.includes(t.id)).map(t => <IconButton key={t.id} icon={t.icon} label={t.label} href={t.href} />)}
  </nav>
}
