import { useQuery } from '@tanstack/react-query'
import { BarH, Donut, ChartCard, Trend } from '@/shared/ui/charts'
import { api } from '@/shared/api/client'
import type { HeatTopic } from '@/shared/types'

/** 热点概览图 —— 懒加载：HeatPage 是首屏静态路由，同步引入会把 echarts 塞进主包 */
export default function HeatCharts({ topics }: { topics: HeatTopic[] }) {
  const top = [...topics]
    .sort((a, b) => (b.heat_score ?? 0) - (a.heat_score ?? 0))
    .slice(0, 10)
    .map((t) => ({
      name: t.title || t.cluster_id,
      value: Number((t.heat_score ?? 0).toFixed(1)),
    }))

  const counts = new Map<string, number>()
  for (const t of topics) {
    const key = t.category_l1 || '未分类'
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const cats = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ name, value }))

  // 跨日期趋势，和所选日期无关，单独一路查询（30s 内不重复打）
  const { data: trendData } = useQuery({
    queryKey: ['heat', 'trend'],
    queryFn: api.heat.trend,
    staleTime: 30_000,
  })
  const trend = (trendData?.trend ?? []).map((p) => ({ name: p.date, value: p.heat }))
  const trendTopics = (trendData?.trend ?? []).reduce((sum, p) => sum + p.count, 0)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <ChartCard title="热度 TOP10" hint="该日期话题按热度排序" className="lg:col-span-2">
        <BarH data={top} height={250} />
      </ChartCard>
      <ChartCard title="话题分类" total={topics.length}>
        <Donut data={cats} height={250} />
      </ChartCard>
      {trend.length > 1 && (
        <ChartCard
          title="热度趋势"
          hint="按报告日期聚合 · 热度合计"
          total={`${trendTopics} 话题`}
          className="lg:col-span-3"
        >
          <Trend data={trend} height={200} />
        </ChartCard>
      )}
    </div>
  )
}
