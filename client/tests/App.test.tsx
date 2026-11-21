import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from '../src/app/App'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows API health status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }))))
  render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>)
  expect(await screen.findByText(/API: ok/)).toBeTruthy()
})
