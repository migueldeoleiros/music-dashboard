import { render, screen, cleanup, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FloatingNav } from '../src/components/ui'

afterEach(cleanup)

it('renders one nav landmark holding one link per item', () => {
  const { container } = render(<FloatingNav items={['home', 'history']} />)
  const navs = screen.getAllByRole('navigation')
  expect(navs).toHaveLength(1)
  expect(navs[0].getAttribute('aria-label')).toBe('Page navigation')
  const links = within(navs[0]).getAllByRole('link')
  expect(links.map(l => l.getAttribute('href'))).toEqual(['/', '/history'])
  expect(container.querySelectorAll('.floating-nav')).toHaveLength(1)
  expect(container.querySelector('[class*="floating-nav--"]')).toBeNull()
  links.forEach(l => expect(l.classList.contains('icon-button--bare')).toBe(true))
})

it('renders a single link for one item', () => {
  render(<FloatingNav items={['history']} />)
  expect(within(screen.getByRole('navigation')).getAllByRole('link')).toHaveLength(1)
})
