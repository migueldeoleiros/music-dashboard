import { useChartQuery } from '../dashboard/ChartSection'
import type { Filters } from '../dashboard/filters'
import { EntityList } from '../../components/ui/EntityList'
import { EntityRow } from '../../components/ui/EntityRow'
import { countryName } from '../../components/charts/builders'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

// Default content of the panel beside the map while no country is selected: countries ranked by listens.
// Selecting a country swaps this for CountryArtistsPanel; closing that restores it. Countries have no artwork.
export function CountryRankingPanel({ filters }: { filters: Filters }) {
  const query = useChartQuery('/api/countries', filters)
  const data = query.data && 'countries' in query.data ? query.data : undefined
  const countries = data?.countries ?? []
  return <div className="country-panel-slot">
    <section className="country-ranking" aria-label="Countries ranked by listens" aria-busy={query.isPending}>
      <div className="country-panel-header"><h3 className="panel-title">Top countries</h3></div>
      {query.isPending && <p role="status" className="entity-empty">Loading countries…</p>}
      {query.isError && <p role="alert" className="entity-empty">Could not load countries.</p>}
      {data && <EntityList label="Countries ranked by listens" maxHeight="none" empty="No countries in this range.">
        {countries.map((country, index) => <EntityRow key={country.code} rank={index + 1} label={countryName(country.code)} showImage={false} value={number.format(country.listens)} />)}
      </EntityList>}
    </section>
  </div>
}
