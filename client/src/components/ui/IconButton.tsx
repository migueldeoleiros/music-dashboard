import { Icon, type IconName } from './Icon'

export type IconButtonProps = {
  icon: IconName
  /** Required accessible name; also the tooltip. */
  label: string
  /** Renders an <a> when set, otherwise a <button type="button">. */
  href?: string
  onClick?: () => void
}

export function IconButton({ icon, label, href, onClick }: IconButtonProps) {
  return href !== undefined
    ? <a className="icon-button" href={href} aria-label={label} title={label}><Icon name={icon} /></a>
    : <button type="button" className="icon-button" aria-label={label} title={label} onClick={onClick}><Icon name={icon} /></button>
}
