import ReactECharts from 'echarts-for-react'
import { useTheme } from '@/shared/store/theme'

// echarts 选项里反复出现的三块底色 —— 和 Dashboard 同一套口径
const AXIS = { fontSize: 10, color: '#a1a1aa' }
const SPLIT = { lineStyle: { color: '#f4f4f5' } }
const TIP = {
  backgroundColor: '#18181b',
  borderWidth: 0,
  textStyle: { color: '#fafafa', fontSize: 11 },
}
const OPTS = { renderer: 'svg' as const }

/** 语义色 —— 对齐 tailwind.config.ts 的 ok/warn/danger，图表里同色系 */
export const TONE = { ok: '#15803d', warn: '#b45309', danger: '#b91c1c', muted: '#a1a1aa' }

export interface Datum {
  name: string
  value: number
  /** 指定该条颜色，缺省用主题强调色 */
  color?: string
}

export function ChartCard({
  title,
  hint,
  total,
  className = '',
  children,
}: {
  title: string
  hint?: string
  total?: number | string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={`card p-4 ${className}`}>
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
          {hint && <p className="text-[10px] text-ink-400 mt-0.5">{hint}</p>}
        </div>
        {total !== undefined && (
          <span className="text-xs font-mono text-ink-400 shrink-0">
            {typeof total === 'number' ? total.toLocaleString() : total}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}

/** 横向条形图 —— 长名称截断、数值标右侧，只取前 12 条（尾部是噪声） */
export function BarH({ data, height = 200, barWidth = 12 }: { data: Datum[]; height?: number; barWidth?: number }) {
  const theme = useTheme((s) => s.theme)
  if (data.length === 0) return null
  const cut = (s: string) => (s.length > 16 ? `${s.slice(0, 15)}…` : s)
  const rows = data
    .slice(0, 12)
    .map((d) => ({ ...d, name: cut(d.name) }))
    .reverse()

  return (
    <ReactECharts
      option={{
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...TIP },
        grid: { left: 8, right: 44, top: 6, bottom: 4, containLabel: true },
        xAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT },
        yAxis: {
          type: 'category',
          data: rows.map((r) => r.name),
          axisLabel: { fontSize: 11, color: '#52525b' },
          axisLine: { show: false },
          axisTick: { show: false },
        },
        series: [
          {
            type: 'bar',
            data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color ?? theme.accent } })),
            barWidth,
            label: { show: true, position: 'right', fontSize: 10, color: '#71717a' },
            itemStyle: { borderRadius: [0, 4, 4, 0] },
          },
        ],
      }}
      style={{ height }}
      opts={OPTS}
    />
  )
}

/** 折线趋势 —— 按时间轴（月份），带面积填充 */
export function Trend({ data, height = 210 }: { data: Datum[]; height?: number }) {
  const theme = useTheme((s) => s.theme)
  if (data.length === 0) return null

  return (
    <ReactECharts
      option={{
        tooltip: { trigger: 'axis', ...TIP },
        grid: { left: 8, right: 12, top: 16, bottom: 4, containLabel: true },
        xAxis: {
          type: 'category',
          data: data.map((d) => d.name),
          boundaryGap: false,
          axisLabel: AXIS,
          axisLine: { lineStyle: { color: '#e4e4e7' } },
        },
        yAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT, minInterval: 1 },
        series: [
          {
            type: 'line',
            data: data.map((d) => d.value),
            smooth: true,
            symbol: 'circle',
            symbolSize: 5,
            lineStyle: { color: theme.accent, width: 2 },
            itemStyle: { color: theme.accent },
            areaStyle: {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: `${theme.accent}40` },
                  { offset: 1, color: `${theme.accent}05` },
                ],
              },
            },
          },
        ],
      }}
      style={{ height }}
      opts={OPTS}
    />
  )
}

/** 环形图 —— 图例竖排在右，点图例可隐藏该扇区 */
export function Donut({ data, height = 220 }: { data: Datum[]; height?: number }) {
  const theme = useTheme((s) => s.theme)
  if (data.length === 0) return null

  return (
    <ReactECharts
      option={{
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', ...TIP },
        legend: {
          type: 'scroll',
          orient: 'vertical',
          right: 0,
          top: 'middle',
          textStyle: { fontSize: 11, color: '#52525b' },
          itemWidth: 10,
          itemHeight: 10,
        },
        series: [
          {
            type: 'pie',
            radius: ['44%', '70%'],
            center: ['36%', '50%'],
            avoidLabelOverlap: true,
            label: { show: false },
            labelLine: { show: false },
            data: data.map((d, i) => ({
              ...d,
              itemStyle: { color: d.color ?? theme.palette[i % theme.palette.length] },
            })),
          },
        ],
      }}
      style={{ height }}
      opts={OPTS}
    />
  )
}
