import { useEffect, useState, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Flame, Calendar, TrendingUp, Newspaper, AlertCircle } from 'lucide-react'
import { api } from '@/shared/api/client'
import { Empty } from '@/shared/ui/Empty'
import { Tag } from '@/shared/ui/Tag'
import { formatDate } from '@/shared/utils/format'

// echarts 约 1MB —— HeatPage 是静态路由，图表拆成异步块不进首屏
const HeatCharts = lazy(() => import('./HeatCharts'))

export function HeatPage() {
  const [date, setDate] = useState<string | null>(null)

  const { data: datesData, isLoading: datesLoading, isError: datesError, refetch: refetchDates } = useQuery({
    queryKey: ['heat', 'dates'],
    queryFn: api.heat.dates,
    staleTime: 300_000,
  })

  const dates = datesData?.dates ?? []

  // 默认落在最新有数据的日期 —— 后端不带日期时查的是「今天」，而今天常常是空的
  useEffect(() => {
    if (date === null && dates.length > 0) setDate(dates[0])
  }, [dates, date])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['heat', 'topics', date],
    queryFn: () => api.heat.topics(date ?? undefined, 100),
    enabled: date !== null,
  })

  const topics = data?.topics ?? []
  const maxHeat = topics.reduce((m, t) => Math.max(m, t.heat_score ?? 0), 0) || 1

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink-900 tracking-tight flex items-center gap-2">
            <Flame className="w-6 h-6 text-warn" strokeWidth={1.5} />
            热频话题
          </h1>
          <p className="text-sm text-ink-400 mt-1">
            基于审核媒体的公共信息报道密度 · 非点击量
          </p>
        </div>
        {data?.date && (
          <span className="text-xs text-ink-400 inline-flex items-center gap-1">
            <Calendar className="w-3.5 h-3.5" strokeWidth={1.5} />
            {data.date}
          </span>
        )}
      </div>

      {/* 日期切换 —— 只列有数据的日期（report_date 里混着月粒度脏值，不能当日历用） */}
      {datesError ? (
        <div className="card p-4 flex items-center justify-between border-danger/30 bg-danger/5">
          <span className="text-sm text-ink-700 inline-flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-danger" strokeWidth={1.5} />
            日期列表加载失败，后端服务可能未启动
          </span>
          <button onClick={() => refetchDates()} className="text-xs text-accent hover:underline">
            重试
          </button>
        </div>
      ) : (
        dates.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            {dates.map((d) => (
              <button
                key={d}
                onClick={() => setDate(d)}
                className={`px-3 h-8 rounded-full text-xs font-medium transition-colors ${
                  d === date
                    ? 'bg-accent text-white'
                    : 'bg-zinc-100 text-ink-600 hover:bg-zinc-200/70'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        )
      )}

      {/* 概览图 —— 热度排序 + 分类占比，替代光看列表 */}
      {date !== null && topics.length > 0 && (
        <Suspense
          fallback={<div className="card h-72 animate-pulse-soft bg-zinc-100/60" />}
        >
          <HeatCharts topics={topics} />
        </Suspense>
      )}

      {/* 列表 */}
      {datesLoading || (date !== null && isLoading) ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card p-5 h-20 animate-pulse-soft bg-zinc-100/60" />
          ))}
        </div>
      ) : isError ? (
        <Empty
          title="加载失败"
          hint="后端服务可能未启动"
          action={
            <button className="text-xs text-accent hover:underline" onClick={() => refetch()}>
              重试
            </button>
          }
        />
      ) : date === null || topics.length === 0 ? (
        <Empty
          title="该日期暂无热频话题"
          hint={dates.length > 0 ? '换一个日期，或等今日采集完成后自动出现' : '热频话题由系统按报道密度自动聚类，暂未生成'}
        />
      ) : (
        <div className="space-y-3">
          {topics.map((topic, i) => {
            const heat = topic.heat_score ?? 0
            const heatPct = (heat / maxHeat) * 100
            return (
              <Link
                key={topic.cluster_id}
                to={`/heat/${encodeURIComponent(topic.cluster_id)}`}
                className="card p-5 hover:shadow-pop hover:border-zinc-300/60 transition-all duration-200 block"
              >
                <div className="flex items-start gap-4">
                  {/* 排名 */}
                  <div className="shrink-0 w-8 h-8 rounded-md bg-zinc-100 flex items-center justify-center">
                    <span className="text-sm font-mono font-semibold text-ink-700">
                      {String(i + 1).padStart(2, '0')}
                    </span>
                  </div>

                  {/* 主体 */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <TrendingUp className="w-3.5 h-3.5 text-accent" strokeWidth={1.5} />
                      <span className="text-sm font-mono text-accent">{heat.toFixed(1)}</span>
                      <span className="text-xs text-ink-400">· {topic.source_count ?? 1} 独立源</span>
                      {topic.category_l1 && <Tag tone="muted">{topic.category_l1}</Tag>}
                      {topic.category_l2 && <Tag tone="muted">{topic.category_l2}</Tag>}
                    </div>

                    <h3 className="text-sm font-medium text-ink-900 leading-snug truncate-2">
                      {topic.title || topic.cluster_id}
                    </h3>

                    {topic.terms && topic.terms.length > 0 && (
                      <div className="mt-2 flex gap-1 flex-wrap">
                        {topic.terms.slice(0, 6).map((term, j) => (
                          <Tag key={j} tone="muted">{term}</Tag>
                        ))}
                      </div>
                    )}

                    {/* 热度条 */}
                    <div className="mt-3 h-1 bg-zinc-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-accent to-accent-hover rounded-full transition-all duration-500"
                        style={{ width: `${heatPct}%` }}
                      />
                    </div>
                  </div>

                  <Newspaper className="w-4 h-4 text-ink-300 shrink-0" strokeWidth={1.5} />
                </div>
              </Link>
            )
          })}
        </div>
      )}

      {/* 底部说明 */}
      <p className="text-xs text-ink-400 text-center pt-4">
        热度 = 信源权重 × 独立源数 × 时效衰减 · {formatDate(new Date().toISOString())}
      </p>
    </div>
  )
}
