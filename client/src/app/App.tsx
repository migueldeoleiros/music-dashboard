import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../api/client'
import type { components } from '../api/schema'

export function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => apiGet<components['schemas']['HealthResponse']>('/health', signal), retry: false })
  return (
    <main className="app">
      <h1>Music Dashboard</h1>
      <section className="card" aria-live="polite">
        API: {health.isPending ? 'checking…' : health.isError ? 'unavailable' : health.data.status}
      </section>
    </main>
  )
}
