import { setMetric, useMetric, type Metric } from '../../app/metric'

const options: { key: Metric; label: string }[] = [{ key: 'listens', label: 'Listens' }, { key: 'minutes', label: 'Minutes' }]

export function MetricToggle() {
  const metric = useMetric()
  return <div className="metric-toggle" role="group" aria-label="Count by">
    {options.map(option => <button type="button" key={option.key} aria-pressed={metric === option.key} onClick={() => setMetric(option.key)}>{option.label}</button>)}
  </div>
}
