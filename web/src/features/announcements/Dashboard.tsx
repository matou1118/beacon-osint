import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { FileText, CheckCircle2, MapPin, Layers } from 'lucide-react'
import type { Announcement, StatsOverview } from '@/shared/types'
import { CATEGORY_MAP } from '@/shared/config/categories'
import { api } from '@/shared/api/client'
import { useTheme } from '@/shared/store/theme'
import type { LucideIcon } from 'lucide-react'

interface DashboardProps {
  items: Announcement[]
  total: number
}

// 行业口径的父分类 —— 与后端 config/category_rules.yaml 的父子结构一致
const INDUSTRY_PARENTS = ['行业政策', '行业补贴']

// 统一视图：stats 可用时吃全库聚合，否则由当前页 items 推导（后端不可用回退）
interface ChartView {
  cat: Array<{ name: string; value: number; color?: string }>
  ind: Array<{ name: string; value: number }>
  provInd: Array<{ prov: string; ind: string; value: number }>
  prov: Array<{ name: string; value: number }>
  year: Array<{ year: string; count: number }>
  day: Array<{ day: string; count: number }>
  gov: Array<{ name: string; value: number }>
  totals: { total: number; verified: number; regions: number; categories: number }
}

function fromStats(stats: StatsOverview): ChartView {
  return {
    // 饼图只画前 12 个分类（后端 cat_data 是全量，徽标要用全量）
    cat: stats.cat_data.slice(0, 12).map((r) => ({
      name: r.name,
      value: r.value,
      color: CATEGORY_MAP.get(r.name)?.color,
    })),
    ind: stats.ind_data,
    provInd: stats.prov_ind_data,
    prov: stats.prov_data,
    year: stats.year_data,
    day: stats.day_data,
    gov: stats.gov_level,
    totals: {
      total: stats.total,
      verified: stats.corroborated,
      regions: stats.provinces,
      categories: stats.categories,
    },
  }
}

function countBy<T>(rows: T[], key: (row: T) => string | null | undefined): Map<string, number> {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const k = key(row) ?? '其他'
    counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  return counts
}

function fromItems(items: Announcement[], total: number): ChartView {
  const catCounts = countBy(items, (a) => a.category_l1)
  const provCounts = countBy(items, (a) => a.region_province || a.region)
  const dayCounts = countBy(items, (a) => a.publish_date?.slice(0, 10))
  const govCounts = countBy(items, (a) => a.gov_level)
  const yearCounts = countBy(items, (a) => {
    const year = a.publish_date?.slice(0, 4)
    return year && /^\d{4}$/.test(year) ? year : null
  })
  const indCounts = countBy(
    items.filter((a) => INDUSTRY_PARENTS.includes(a.category_l1 ?? '')),
    (a) => a.category_l2,
  )

  const provInd: ChartView['provInd'] = []
  const pairCounts = new Map<string, number>()
  for (const a of items) {
    if (!INDUSTRY_PARENTS.includes(a.category_l1 ?? '') || !a.category_l2) continue
    const prov = a.region_province || a.region || '未知'
    const key = `${prov}\u0000${a.category_l2}`
    pairCounts.set(key, (pairCounts.get(key) ?? 0) + 1)
  }
  for (const [key, value] of pairCounts) {
    const [prov, ind] = key.split('\u0000')
    provInd.push({ prov, ind, value })
  }

  const sortedEntries = (m: Map<string, number>, desc = true, limit = Infinity) =>
    [...m.entries()].sort((a, b) => (desc ? b[1] - a[1] : a[1] - b[1])).slice(0, limit)

  return {
    cat: sortedEntries(catCounts).map(([name, value]) => ({
      name,
      value,
      color: CATEGORY_MAP.get(name)?.color,
    })),
    ind: sortedEntries(indCounts, true, 14).map(([name, value]) => ({ name, value })),
    provInd,
    prov: sortedEntries(provCounts, true, 10).map(([name, value]) => ({ name, value })),
    year: sortedEntries(yearCounts, false).map(([year, count]) => ({ year, count })),
    day: sortedEntries(dayCounts, false, 30).map(([day, count]) => ({ day, count })),
    gov: sortedEntries(govCounts).map(([name, value]) => ({ name, value })),
    totals: {
      total,
      verified: items.filter((a) => a.corroboration_status === 'corroborated').length,
      regions: new Set(items.map((a) => a.region).filter(Boolean)).size,
      categories: catCounts.size,
    },
  }
}

export function Dashboard({ items, total }: DashboardProps) {
  const theme = useTheme((s) => s.theme)
  const { data: stats } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: () => api.stats.overview(),
    staleTime: 60_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const view = useMemo(
    () => (stats ? fromStats(stats) : fromItems(items, total)),
    [stats, items, total],
  )

  // 省份 × 行业：每省一根堆叠条 —— 只画头部省份/行业，尾部是噪声
  const provIndChart = useMemo(() => {
    const byProv = new Map<string, { total: number; cells: Map<string, number> }>()
    const sums = new Map<string, number>()
    for (const row of view.provInd) {
      const entry = byProv.get(row.prov) ?? { total: 0, cells: new Map<string, number>() }
      entry.total += row.value
      entry.cells.set(row.ind, (entry.cells.get(row.ind) ?? 0) + row.value)
      byProv.set(row.prov, entry)
      sums.set(row.ind, (sums.get(row.ind) ?? 0) + row.value)
    }
    const inds = [...sums.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map((e) => e[0])
    const provs = [...byProv.entries()]
      .sort((a, b) => b[1].total - a[1].total)
      .slice(0, 8)
      .map((e) => e[0])
    return {
      provs: provs.reverse(),
      series: inds.map((ind, i) => ({
        name: ind,
        type: 'bar' as const,
        stack: 'pi',
        data: provs.map((p) => byProv.get(p)?.cells.get(ind) ?? 0),
        itemStyle: { color: theme.palette[i % theme.palette.length] },
        barWidth: 9,
      })),
    }
  }, [view.provInd, theme])

  const axisText = { fontSize: 10, color: '#a1a1aa' }
  const splitLine = { lineStyle: { color: '#f4f4f5' } }
  const tooltipBg = { backgroundColor: '#18181b', borderWidth: 0, textStyle: { color: '#fafafa', fontSize: 11 } }

  return (
    <div className="space-y-4">
      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard icon={FileText} label="收录信息" value={view.totals.total} unit="条" tone="teal" />
        <StatCard icon={CheckCircle2} label="多源印证" value={view.totals.verified} unit="条" tone="green" />
        <StatCard icon={MapPin} label="覆盖地域" value={view.totals.regions} unit="个" tone="sky" />
        <StatCard icon={Layers} label="信息分类" value={view.totals.categories} unit="类" tone="violet" />
      </div>

      {/* 第一排：分类 / 行业 / 地域 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">信息分类分布</h3>
          <ReactECharts
            option={{
              tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)', ...tooltipBg },
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
                  radius: ['42%', '68%'],
                  center: ['38%', '50%'],
                  avoidLabelOverlap: true,
                  label: { show: false },
                  labelLine: { show: false },
                  data: view.cat.map((d) => ({ ...d, itemStyle: d.color ? { color: d.color } : undefined })),
                },
              ],
            }}
            style={{ height: 220 }}
            opts={{ renderer: 'svg' }}
          />
        </div>

        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-1.5">行业分布 TOP8</h3>
          <p className="text-[10px] text-ink-400 mb-2">按行业门类自动归类（行业政策 / 行业补贴）</p>
          <ReactECharts
            option={{
              tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipBg },
              grid: { left: 8, right: 28, top: 4, bottom: 4, containLabel: true },
              xAxis: { type: 'value', axisLabel: axisText, splitLine },
              yAxis: {
                type: 'category',
                data: view.ind.slice(0, 8).map((d) => d.name).reverse(),
                axisLabel: { fontSize: 11, color: '#52525b' },
                axisLine: { show: false },
                axisTick: { show: false },
              },
              series: [
                {
                  type: 'bar',
                  data: view.ind.slice(0, 8).map((d) => d.value).reverse(),
                  barWidth: 12,
                  label: { show: true, position: 'right', fontSize: 10, color: '#71717a' },
                  itemStyle: { color: theme.accent, borderRadius: [0, 4, 4, 0] },
                },
              ],
            }}
            style={{ height: 220 }}
            opts={{ renderer: 'svg' }}
          />
        </div>

        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">地域分布 TOP10</h3>
          <ReactECharts
            option={{
              tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipBg },
              grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
              xAxis: {
                type: 'value',
                axisLabel: axisText,
                splitLine,
              },
              yAxis: {
                type: 'category',
                data: view.prov.map((d) => d.name).reverse(),
                axisLabel: { fontSize: 11, color: '#52525b' },
                axisLine: { show: false },
                axisTick: { show: false },
              },
              series: [
                {
                  type: 'bar',
                  data: view.prov.map((d) => d.value).reverse(),
                  barWidth: 14,
                  itemStyle: { color: theme.palette[0], borderRadius: [0, 4, 4, 0] },
                },
              ],
            }}
            style={{ height: 220 }}
            opts={{ renderer: 'svg' }}
          />
        </div>
      </div>

      {/* 第二排：省份×行业（宽） + 年度趋势 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 lg:col-span-2">
          <h3 className="text-sm font-semibold text-ink-900 mb-1.5">省份 × 行业</h3>
          <p className="text-[10px] text-ink-400 mb-2">各省产业政策方向 —— 仅统计有行业门类的记录，体裁词不计入</p>
          <ReactECharts
            option={{
              backgroundColor: 'transparent',
              grid: { left: 8, right: 16, top: 24, bottom: 4, containLabel: true },
              legend: { top: 0, right: 0, itemWidth: 8, itemHeight: 8, textStyle: { color: '#71717a', fontSize: 10 } },
              tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipBg },
              xAxis: { type: 'value', show: false },
              yAxis: {
                type: 'category',
                data: provIndChart.provs,
                axisLabel: { color: '#52525b', fontSize: 11 },
                axisLine: { show: false },
                axisTick: { show: false },
              },
              series: provIndChart.series,
            }}
            style={{ height: 230 }}
            opts={{ renderer: 'svg' }}
          />
        </div>

        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">年度趋势</h3>
          <ReactECharts
            option={{
              tooltip: { trigger: 'axis', ...tooltipBg },
              grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
              xAxis: {
                type: 'category',
                data: view.year.map((d) => d.year),
                axisLabel: axisText,
                axisLine: { lineStyle: { color: '#e4e4e7' } },
              },
              yAxis: {
                type: 'value',
                axisLabel: axisText,
                splitLine,
                minInterval: 1,
              },
              series: [
                {
                  type: 'bar',
                  data: view.year.map((d) => d.count),
                  barWidth: '55%',
                  itemStyle: {
                    borderRadius: [4, 4, 0, 0],
                    color: {
                      type: 'linear',
                      x: 0, y: 0, x2: 0, y2: 1,
                      colorStops: [
                        { offset: 0, color: theme.accent },
                        { offset: 1, color: theme.accent + '55' },
                      ],
                    },
                  },
                },
              ],
            }}
            style={{ height: 208 }}
            opts={{ renderer: 'svg' }}
          />
        </div>
      </div>

      {/* 第三排：近 30 日发布 + 行政层级 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">近期发布节奏</h3>
          <ReactECharts
            option={{
              tooltip: { trigger: 'axis', ...tooltipBg },
              grid: { left: 8, right: 8, top: 8, bottom: 24, containLabel: true },
              xAxis: {
                type: 'category',
                data: view.day.map((d) => d.day.slice(5)),
                axisLabel: { ...axisText, rotate: 30 },
                axisLine: { lineStyle: { color: '#e4e4e7' } },
              },
              yAxis: {
                type: 'value',
                axisLabel: axisText,
                splitLine,
                minInterval: 1,
              },
              series: [
                {
                  type: 'line',
                  data: view.day.map((d) => d.count),
                  smooth: true,
                  symbol: 'circle',
                  symbolSize: 5,
                  lineStyle: { color: theme.accent, width: 2 },
                  itemStyle: { color: theme.accent },
                  areaStyle: {
                    color: {
                      type: 'linear',
                      x: 0, y: 0, x2: 0, y2: 1,
                      colorStops: [
                        { offset: 0, color: theme.accent + '40' },
                        { offset: 1, color: theme.accent + '05' },
                      ],
                    },
                  },
                },
              ],
            }}
            style={{ height: 200 }}
            opts={{ renderer: 'svg' }}
          />
        </div>

        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">行政层级覆盖</h3>
          <div className="grid grid-cols-2 gap-x-5 gap-y-3">
            {view.gov.slice(0, 6).map((g, i) => (
              <LevelBar
                key={g.name}
                label={g.name}
                count={g.value}
                total={view.totals.total}
                color={theme.palette[i % theme.palette.length]}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 子组件 ──────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  unit,
  tone,
}: {
  icon: LucideIcon
  label: string
  value: number
  unit: string
  tone: 'teal' | 'green' | 'sky' | 'violet'
}) {
  const tones = {
    teal: 'bg-teal-50 text-teal-700',
    green: 'bg-green-50 text-green-700',
    sky: 'bg-sky-50 text-sky-700',
    violet: 'bg-violet-50 text-violet-700',
  }
  return (
    <div className="card p-4 flex items-center gap-3">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${tones[tone]}`}>
        <Icon className="w-5 h-5" strokeWidth={1.5} />
      </div>
      <div>
        <div className="text-xl font-semibold text-ink-900 tabular-nums">
          {value}
          <span className="text-xs font-normal text-ink-400 ml-1">{unit}</span>
        </div>
        <div className="text-xs text-ink-500">{label}</div>
      </div>
    </div>
  )
}

function LevelBar({ label, count, total, color }: { label: string; count: number; total: number; color: string }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-xs text-ink-600">{label}</span>
        <span className="text-xs font-semibold text-ink-900 tabular-nums">
          {count}
          <span className="text-ink-400 font-normal ml-1">{pct}%</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-zinc-100 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}
