import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Controls } from '../src/features/dashboard/Controls'
import { installDialogStub } from './dialog'

beforeEach(() => { installDialogStub(); vi.setSystemTime(new Date('2026-11-21T12:00:00Z')) })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers() })
const filters = { from: '2026-01-05', to: '2026-01-06', tz: 'UTC' }

it('opens a named native modal only on request, Cancel restores focus and discards the draft without navigation', () => {
  const onChange = vi.fn()
  const navigate = vi.spyOn(window.history, 'pushState')
  render(<Controls filters={filters} period="custom" onChange={onChange} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  const opener = screen.getByRole('button', { name: 'Custom' })
  fireEvent.click(opener)
  expect(screen.getByRole('dialog', { name: 'Custom timeframe' }).tagName).toBe('DIALOG')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-02-01' } })
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(opener)
  expect(onChange).not.toHaveBeenCalled()
  expect(navigate).not.toHaveBeenCalled()
  fireEvent.click(opener)
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe(filters.from)
})

it('validates empty, reversed and future dates, then applies a one-day fixed range and closes', () => {
  const onChange = vi.fn()
  render(<Controls filters={filters} period="custom" onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  const from = screen.getByLabelText('From')
  const to = screen.getByLabelText('To')
  const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  fireEvent.change(to, { target: { value: '' } }); submit()
  expect(screen.getByRole('alert').textContent).toMatch(/valid local dates/)
  fireEvent.change(to, { target: { value: '2026-01-04' } }); submit()
  expect(screen.getByRole('alert').textContent).toMatch(/on or before/)
  fireEvent.change(to, { target: { value: '2026-11-22' } }); submit()
  expect(screen.getByRole('alert').textContent).toMatch(/today or earlier/)
  expect(onChange).not.toHaveBeenCalled()
  fireEvent.change(from, { target: { value: '2026-01-05' } })
  fireEvent.change(to, { target: { value: '2026-01-05' } }); submit()
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...filters, to: filters.from }, 'custom')
  expect(screen.queryByRole('dialog')).toBeNull()
})

it.each(['From', 'To'])('rejects unfinished native %s input instead of treating an empty value as intentional', label => {
  const onChange = vi.fn()
  render(<Controls filters={filters} period="custom" onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  const input = screen.getByLabelText<HTMLInputElement>(label)
  fireEvent.change(input, { target: { value: '' } })
  // Native browsers expose badInput for unfinished segmented date entry; jsdom does not.
  Object.defineProperty(input, 'validity', { configurable: true, value: { badInput: true } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(onChange).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toMatch(/valid local dates/)
  expect(input.getAttribute('aria-invalid')).toBe('true')
  expect(input.getAttribute('aria-describedby')).toBe(screen.getByRole('alert').id)
  expect(screen.getByRole('dialog')).toBeTruthy()
})

it('back/forward closes an uncommitted draft without applying it', () => {
  const onChange = vi.fn()
  render(<Controls filters={filters} period="custom" onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  fireEvent.popState(window)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(onChange).not.toHaveBeenCalled()
})
