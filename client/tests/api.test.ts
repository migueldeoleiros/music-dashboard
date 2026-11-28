import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, COUNTRY_ARTISTS_LIMIT, fetchChart, fetchCountryArtists } from '../src/api/client'

const filters = { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' }
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

it('serializes exact date/tz filters, caps only rankings, and passes the abort signal', async () => {
  const fetch = vi.fn(async (url: string, options?: RequestInit) => { void url; void options; return new Response('{}') })
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await fetchChart('/api/evolution/genres', { ...filters, tz: 'Asia/Kathmandu' }, signal)
  const [url, options] = fetch.mock.calls[0]
  const params = new URL(url, 'http://localhost').searchParams
  expect(params.has('limit')).toBe(false); expect(params.get('tz')).toBe('Asia/Kathmandu'); expect(params.get('from')).toBe(filters.from)
  expect(options?.signal).toBe(signal)
  await fetchChart('/api/pies/artists', filters)
  expect(new URL(String(fetch.mock.calls[1][0]), 'http://localhost').searchParams.has('limit')).toBe(false) // share-based, not Top-N
  await fetchChart('/api/rankings/artists', filters)
  expect(new URL(String(fetch.mock.calls[2][0]), 'http://localhost').searchParams.get('limit')).toBe('10')
})
it('preserves typed 400 errors, and identifies timezone errors without retrying other validation failures', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'invalid_request', message: 'Invalid request', details: ['Unsupported IANA timezone'] }), { status: 400 })))
  try { await fetchChart('/api/heatmap', filters); throw new Error('Expected rejection') } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    expect(error instanceof ApiError && error.timezoneRejected).toBe(true)
    expect(error instanceof ApiError && error.body.details).toEqual(['Unsupported IANA timezone'])
  }
  expect(new ApiError(400, { code: 'invalid_request', message: 'Invalid dates', details: [] }).timezoneRejected).toBe(false)
})
it('handles non-JSON or malformed upstream errors without exposing response bodies', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>private debug output</html>', { status: 502 })))
  await expect(fetchChart('/api/countries', filters)).rejects.toThrow('Statistics unavailable (HTTP 502). Please retry.')
})
it('requests country artists on the path-param route with encoded code, filters and limit', async () => {
  const fetch = vi.fn(async (url: string, options?: RequestInit) => { void url; void options; return new Response('{}') })
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await fetchCountryArtists('US', { ...filters, tz: 'Asia/Kathmandu' }, signal)
  const url = new URL(String(fetch.mock.calls[0][0]), 'http://localhost')
  expect(url.pathname).toBe('/api/countries/US/artists')
  expect(url.searchParams.get('limit')).toBe(String(COUNTRY_ARTISTS_LIMIT))
  expect(url.searchParams.get('tz')).toBe('Asia/Kathmandu'); expect(url.searchParams.get('from')).toBe(filters.from)
  expect(fetch.mock.calls[0][1]?.signal).toBe(signal)
  await fetchCountryArtists('a/b?', filters)
  expect(new URL(String(fetch.mock.calls[1][0]), 'http://localhost').pathname).toBe('/api/countries/a%2Fb%3F/artists')
})
