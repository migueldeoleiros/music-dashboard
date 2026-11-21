// Scoped ECharts core: register only what is used. Chart features add their
// own chart types/components here as they land.
import { CanvasRenderer } from 'echarts/renderers'
import { GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'

echarts.use([CanvasRenderer, GridComponent, TooltipComponent])

export { echarts }
