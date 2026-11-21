export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api${path}`, { signal, headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`API ${res.status}`)
  return (await res.json()) as T
}
