import { useState, useMemo, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Flame, ArrowRight, Activity, Search } from 'lucide-react'
import { api } from '@/shared/api/client'
import { AnnouncementCard } from './AnnouncementCard'
import { CategoryNav } from './CategoryNav'
import { CardSkeleton } from '@/shared/ui/Skeleton'
import { Empty } from '@/shared/ui/Empty'
import { useFilters } from '@/shared/store/filters'
import { CATEGORY_GROUPS, getCategoryConfig } from '@/shared/config/categories'
import type { Announcement } from '@/shared/types'

// Dashboard 依赖 echarts（约 1MB）。HomePage 是首屏且被路由静态引入，
// 同步 import 会把 echarts 塞进主包 —— 首屏从 404KB 涨到 1.48MB。
// 懒加载把它挪出关键路径。
const Dashboard = lazy(() =>
  import('./Dashboard').then((m) => ({ default: m.Dashboard })),
)

export function HomePage() {
  const filters = useFilters()
  const [activeGroup, setActiveGroup] = useState('all')
  const [page, setPage] = useState(1)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['announcements', 'home', filters, page],
    queryFn: () =>
      api.announcements.list({
        gov_level: filters.gov_level,
        region: filters.region,
        category: filters.category,
        category_l2: filters.category_l2,
        target_group: filters.target_group,
        target_industry: filters.target_industry,
        keyword: filters.keyword,
        page,
        page_size: 100,
      }),
  })

  const { data: heatData } = useQuery({
    queryKey: ['heat', 'home'],
    queryFn: () => api.heat.topics(undefined, 6),
  })

  // 与 Dashboard 同一个 queryKey —— react-query 会去重，不会多打一次接口
  const { data: stats } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: () => api.stats.overview(),
    staleTime: 600_000,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0

  // 分类计数 —— 优先全库口径（stats.cat_data）。旧实现只数当前页 100 条，
  // 超过 100 条的分类徽标全是错的。
  const categoryCounts = useMemo(() => {
    if (stats) {
      const counts: Record<string, number> = { _all: stats.total }
      for (const row of stats.cat_data) counts[row.name] = row.value
      return counts
    }
    const counts: Record<string, number> = { _all: total }
    for (const a of items) {
      const cat = a.category_l1 ?? '其他'
      counts[cat] = (counts[cat] ?? 0) + 1
    }
    return counts
  }, [stats, items, total])

  // 按分类组过滤
  const filteredItems = useMemo(() => {
    if (activeGroup === 'all') return items
    const group = CATEGORY_GROUPS.find((g) => g.key === activeGroup)
    if (!group) return items
    return items.filter((a) => group.categories.includes(a.category_l1 ?? ''))
  }, [items, activeGroup])

  // 按分类分组（全部视图下分组展示）
  const groupedItems = useMemo(() => {
    if (activeGroup !== 'all') return null
    const groups: { category: string; items: Announcement[] }[] = []
    for (const config of CATEGORY_GROUPS) {
      if (config.key === 'all') continue
      const groupItems = items.filter((a) => config.categories.includes(a.category_l1 ?? ''))
      if (groupItems.length > 0) {
        groups.push({ category: config.categories[0], items: groupItems })
      }
    }
    return groups
  }, [items, activeGroup])

  return (
    <div className="space-y-6">
      {/* 标题区 —— 非对称左对齐 */}
      <section className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4 items-end">
        <div>
          <h1 className="text-2xl font-semibold text-ink-900 tracking-tight">民生信息总览</h1>
          <p className="text-sm text-ink-400 mt-1">
            政府公开信息分类筛选 · 摘要可追溯 · 原文可核验 · 匹配可解释
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to="/search"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-100 text-ink-600 hover:bg-zinc-200 text-xs font-medium transition-colors"
          >
            <Search className="w-3.5 h-3.5" strokeWidth={1.5} />
            高级检索
          </Link>
          <div className="flex items-center gap-2 text-xs text-ink-400">
            <span className="inline-flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-ok animate-pulse-soft" />
              <Activity className="w-3.5 h-3.5" strokeWidth={1.5} />
              {data ? `共 ${total} 条` : '加载中'}
            </span>
          </div>
        </div>
      </section>

      {/* 仪表盘 —— 统计 + 图表 */}
      {isLoading ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      ) : items.length > 0 ? (
        <Suspense
            fallback={<div className="card p-8 h-40 animate-pulse-soft bg-zinc-100/60" />}
          >
            <Dashboard items={items} total={total} />
          </Suspense>
      ) : null}

      {/* 热频话题横滚 */}
      {heatData && heatData.topics && heatData.topics.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
              <Flame className="w-4 h-4 text-warn" strokeWidth={1.5} />
              热频话题
            </h2>
            <Link
              to="/heat"
              className="text-xs text-ink-400 hover:text-accent inline-flex items-center gap-1"
            >
              全部 <ArrowRight className="w-3 h-3" strokeWidth={1.5} />
            </Link>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-2 -mx-4 px-4 snap-x">
            {heatData.topics.slice(0, 6).map((t) => (
              <Link
                key={t.cluster_id}
                to={`/heat/${encodeURIComponent(t.cluster_id)}`}
                className="card p-4 shrink-0 w-64 snap-start hover:border-accent/30 transition-colors"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-mono text-accent">
                    {(t.heat_score ?? 0).toFixed(1)}
                  </span>
                  <span className="text-xs text-ink-400">
                    {t.source_count ?? 1} 源
                  </span>
                </div>
                <p className="text-sm text-ink-900 truncate-2 leading-snug">
                  {t.title || t.cluster_id}
                </p>
                {t.terms && t.terms.length > 0 && (
                  <div className="mt-2 flex gap-1 flex-wrap">
                    {t.terms.slice(0, 3).map((term, i) => (
                      <span
                        key={i}
                        className="text-[10px] text-ink-400 bg-zinc-100 px-1.5 py-0.5 rounded-sm"
                      >
                        {term}
                      </span>
                    ))}
                  </div>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* 分类导航栏 */}
      <section>
        <div className="card p-3">
          <CategoryNav active={activeGroup} onChange={setActiveGroup} counts={categoryCounts} />
        </div>
      </section>

      {/* 信息流 —— 分组展示或过滤展示 */}
      <section>
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        ) : isError ? (
          <Empty
            title="加载失败"
            hint="后端服务可能未启动，点重试再拉一次"
            action={
              <button
                onClick={() => refetch()}
                className="text-xs text-accent hover:underline"
              >
                重试
              </button>
            }
          />
        ) : !data || items.length === 0 ? (
          <Empty title="暂无公告" hint="尝试调整筛选条件或稍后再来" />
        ) : activeGroup === 'all' && groupedItems ? (
          /* 全部视图 —— 按分类分组展示 */
          <div className="space-y-6">
            {groupedItems.map((group) => {
              const config = getCategoryConfig(group.category)
              return (
                <div key={group.category}>
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
                      <span className="w-1 h-4 rounded-full" style={{ backgroundColor: config.color }} />
                      {config.label}
                      <span className="text-xs font-normal text-ink-400">{group.items.length} 条</span>
                    </h2>
                    <button
                      onClick={() => {
                        const grp = CATEGORY_GROUPS.find((g) => g.categories.includes(group.category))
                        if (grp) setActiveGroup(grp.key)
                      }}
                      className="text-xs text-ink-400 hover:text-accent inline-flex items-center gap-1"
                    >
                      查看全部 <ArrowRight className="w-3 h-3" strokeWidth={1.5} />
                    </button>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {group.items.slice(0, 8).map((item, i) => (
                      <AnnouncementCard key={item.id} item={item} index={i} />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          /* 单分类视图 —— 展示该分类下全部 */
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-ink-900">
                {CATEGORY_GROUPS.find((g) => g.key === activeGroup)?.label}
                <span className="text-xs font-normal text-ink-400 ml-2">{filteredItems.length} 条</span>
              </h2>
            </div>
            {filteredItems.length === 0 ? (
              <Empty title="该分类暂无信息" hint="试试其他分类" />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {filteredItems.map((item, i) => (
                  <AnnouncementCard key={item.id} item={item} index={i} />
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* 分页 */}
      {data && total > 100 && activeGroup === 'all' && (
        <div className="flex items-center justify-center gap-4 pt-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-4 py-2 rounded-lg bg-zinc-100 text-ink-600 text-sm font-medium hover:bg-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            上一页
          </button>
          <span className="text-sm text-ink-400">
            第 {page} 页 / 共 {Math.ceil(total / 100)} 页
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= Math.ceil(total / 100)}
            className="px-4 py-2 rounded-lg bg-zinc-100 text-ink-600 text-sm font-medium hover:bg-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
