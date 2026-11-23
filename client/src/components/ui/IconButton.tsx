import { Icon, type IconName } from './Icon'

export type IconButtonProps = {
  icon: IconName
  /** Required accessible name; also the tooltip. */
  label: string
  /** Renders an <a> when set, otherwise a <button type="button">. */
  href?: string
  onClick?: () => void
  /** Borderless transparent variant for use inside a container bubble. */
  bare?: boolean
}

export function IconButton({ icon, label, href, onClick, bare }: IconButtonProps) {
  const cls = bare ? 'icon-button icon-button--bare' : 'icon-button'
  return href !== undefined
    ? <a className={cls} href={href} aria-label={label} title={label}><Icon name={icon} /></a>
    : <button type="button" className={cls} aria-label={label} title={label} onClick={onClick}><Icon name={icon} /></button>
}
