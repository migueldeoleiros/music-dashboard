import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, fetchChart, fetchHealth } from '../src/api/client'

const filters = { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' }
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

it('serializes exact date/tz filters with endpoint-specific top limits and abort signal', async () => {
  vi.stubEnv('VITE_MOCK_API', 'false')
  const fetch = vi.fn(async (url: string, options?: RequestInit) => { void url; void options; return new Response('{}') })
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await fetchChart('/api/evolution/genres', { ...filters, tz: 'Asia/Kathmandu' }, signal)
  const [url, options] = fetch.mock.calls[0]
  const params = new URL(url, 'http://localhost').searchParams
  expect(params.get('limit')).toBe('8'); expect(params.get('tz')).toBe('Asia/Kathmandu'); expect(params.get('from')).toBe(filters.from)
  expect(options?.signal).toBe(signal)
  await fetchChart('/api/pies/artists', filters)
  expect(new URL(String(fetch.mock.calls[1][0]), 'http://localhost').searchParams.get('limit')).toBe('10')
})
it('preserves typed 400 errors, and identifies timezone errors without retrying other validation failures', async () => {
  vi.stubEnv('VITE_MOCK_API', 'false')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'invalid_request', message: 'Invalid request', details: ['Unsupported IANA timezone'] }), { status: 400 })))
  try { await fetchChart('/api/heatmap', filters); throw new Error('Expected rejection') } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    expect(error instanceof ApiError && error.timezoneRejected).toBe(true)
    expect(error instanceof ApiError && error.body.details).toEqual(['Unsupported IANA timezone'])
  }
  expect(new ApiError(400, { code: 'invalid_request', message: 'Invalid dates', details: [] }).timezoneRejected).toBe(false)
})
it('handles non-JSON or malformed upstream errors without exposing response bodies', async () => {
  vi.stubEnv('VITE_MOCK_API', 'false')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>private debug output</html>', { status: 502 })))
  await expect(fetchChart('/api/countries', filters)).rejects.toThrow('Statistics unavailable (HTTP 502). Please retry.')
})
it('standalone fixture mode never calls fetch, and every family resolves without an API', async () => {
  vi.stubEnv('VITE_MOCK_API', 'true')
  const fetch = vi.fn(() => { throw new Error('Network forbidden') })
  vi.stubGlobal('fetch', fetch)
  for (const path of ['/api/rankings/artists', '/api/rankings/albums', '/api/rankings/tracks', '/api/rankings/genres', '/api/heatmap', '/api/countries', '/api/evolution/artists', '/api/evolution/genres', '/api/pies/artists', '/api/pies/genres'] as const) {
    const result = await fetchChart(path, filters)
    expect(result.fixture).toBe(true)
  }
  expect((await fetchHealth()).mode).toBe('fixture')
  expect(fetch).not.toHaveBeenCalled()
})
