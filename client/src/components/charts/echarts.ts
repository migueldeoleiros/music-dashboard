// Scoped ECharts core: register only what is used.
import { CanvasRenderer } from 'echarts/renderers'
import { HeatmapChart, LineChart, MapChart, PieChart, ScatterChart } from 'echarts/charts'
import {
  GeoComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { LabelLayout } from 'echarts/features'
import * as echarts from 'echarts/core'

echarts.use([
  CanvasRenderer,
  LabelLayout,
  LineChart,
  PieChart,
  HeatmapChart,
  MapChart,
  ScatterChart,
  GeoComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
])

export { echarts }
