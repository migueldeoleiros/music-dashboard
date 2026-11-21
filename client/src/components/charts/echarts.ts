// Scoped ECharts core: register only what is used.
import { CanvasRenderer } from 'echarts/renderers'
import { HeatmapChart, LineChart, MapChart, PieChart, ScatterChart } from 'echarts/charts'
import {
  GeoComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'

echarts.use([
  CanvasRenderer,
  LineChart,
  PieChart,
  HeatmapChart,
  MapChart,
  ScatterChart,
  GeoComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
])

export { echarts }
