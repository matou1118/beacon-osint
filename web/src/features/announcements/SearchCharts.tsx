import { BarH, Donut, Trend, ChartCard } from '@/shared/ui/charts'
import type { AnnouncementStats } from '@/shared/types'

/** 检索结果汇总图 —— 懒加载：SearchPage 是首屏静态路由，图表不进主包 */
export default function SearchCharts({ stats }: { stats: AnnouncementStats }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="分类构成" total={stats.total}>
          <Donut data={stats.category_l1} height={230} />
        </ChartCard>
        <ChartCard title="行政层级">
          <Donut data={stats.gov_level} height={230} />
        </ChartCard>
        <ChartCard title="地域 TOP10">
          <BarH data={stats.province} height={230} />
        </ChartCard>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="行业 TOP12" hint="按二级分类自动归类">
          <BarH data={stats.category_l2} height={200} />
        </ChartCard>
        <ChartCard title="发布趋势" hint="按月份 · 全部命中" className="lg:col-span-2">
          <Trend data={stats.month.slice(-12)} height={200} />
        </ChartCard>
      </div>
    </div>
  )
}
