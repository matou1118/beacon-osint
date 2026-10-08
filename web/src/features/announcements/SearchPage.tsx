import { useState, useMemo, useEffect, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, X, SlidersHorizontal, ChevronDown, Download } from 'lucide-react'
import { api } from '@/shared/api/client'
import { AnnouncementCard } from './AnnouncementCard'
import { CardSkeleton } from '@/shared/ui/Skeleton'
import { Empty } from '@/shared/ui/Empty'
import { Button } from '@/shared/ui/Button'
import { useFilters } from '@/shared/store/filters'
import { cn } from '@/shared/utils/format'

// 取值必须和库里的 gov_level 一致（中文）—— 发 'provincial' 这类英文码永远筛出 0 条。
// 国家级/国家部委两种写法并存（20 / 1905 条），逗号合并成一个筛选（后端 _values 按逗号拆）。
const LEVELS = [
  { value: '国家级,国家部委', label: '国家部委' },
  { value: '省级', label: '省级' },
  { value: '直辖市', label: '直辖市' },
  { value: '市级', label: '市级' },
  { value: '县级', label: '县级' },
]

const PAGE_SIZE = 40

// echarts 约 1MB —— SearchPage 是首屏静态路由，图表拆成异步块
const SearchCharts = lazy(() => import('./SearchCharts'))

export function SearchPage() {
  const filters = useFilters()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [keywordInput, setKeywordInput] = useState(filters.keyword ?? '')
  const [page, setPage] = useState(1)

  // 换筛选条件要回到第 1 页，否则会停在一个空页上
  useEffect(() => {
    setPage(1)
  }, [filters])

  const { data: categories } = useQuery({
    queryKey: ['categories'],
    queryFn: api.categories,
    staleTime: 600_000,
  })

  const { data: regions } = useQuery({
    queryKey: ['regions'],
    queryFn: api.regions,
    staleTime: 600_000,
  })

  // 行业门类跟仪表盘「行业分布」同一份统计，只列库里实际存在的
  const { data: stats } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: api.stats.overview,
    staleTime: 600_000,
  })

  // 列表 / 汇总 / 导出共用同一套筛选参数 —— 三处各写一遍迟早对不上
  const searchParams = useMemo(
    () => ({
      gov_level: filters.gov_level,
      region: filters.region,
      category: filters.category,
      category_l2: filters.category_l2,
      target_group: filters.target_group,
      target_industry: filters.target_industry,
      keyword: filters.keyword,
    }),
    [filters],
  )

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['announcements', 'search', filters, page],
    queryFn: () => api.announcements.list({ ...searchParams, page, page_size: PAGE_SIZE }),
  })

  const activeCount = useMemo(
    () =>
      [filters.gov_level, filters.region, filters.category, filters.category_l2, filters.target_group, filters.target_industry, filters.keyword].filter(
        Boolean,
      ).length,
    [filters],
  )

  // 全量汇总 —— 列表只翻一页，分布图画的是整个命中集
  const { data: agg } = useQuery({
    queryKey: ['announcements', 'stats', filters],
    queryFn: () => api.announcements.stats(searchParams),
  })

  return (
    <div className="space-y-5">
      {/* 搜索栏 */}
      <div className="flex items-center gap-2">
        <div className="flex-1 relative">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-400"
            strokeWidth={1.5}
          />
          <input
            type="text"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') filters.set({ keyword: keywordInput || null })
            }}
            placeholder="关键词搜索，回车确认"
            className="w-full h-10 pl-10 pr-4 rounded-md border border-zinc-200 bg-surface text-sm placeholder:text-ink-400 focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
          />
        </div>
        <Button
          variant="outline"
          size="md"
          onClick={() => setDrawerOpen(true)}
          className="lg:hidden"
        >
          <SlidersHorizontal className="w-4 h-4" strokeWidth={1.5} />
          筛选
          {activeCount > 0 && (
            <span className="ml-1 text-[10px] bg-accent text-white rounded-full px-1.5">
              {activeCount}
            </span>
          )}
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-5">
        {/* 桌面筛选侧栏 */}
        <aside className="hidden lg:block">
          <FilterPanel
            categories={categories?.l1 ?? []}
            industries={stats?.ind_data.map((i) => i.name) ?? []}
            provinces={regions?.provinces ?? []}
          />
        </aside>

        {/* 结果区 */}
        <div>
          {/* 汇总图：分类 / 层级 / 地域 / 行业 / 趋势 —— 随筛选条件一起变 */}
          {agg && agg.total > 0 && (
            <div className="mb-4">
              <Suspense
                fallback={<div className="card h-64 animate-pulse-soft bg-zinc-100/60" />}
              >
                <SearchCharts stats={agg} />
              </Suspense>
            </div>
          )}

          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-ink-600">
              {data ? (
                <>
                  共 <span className="font-semibold text-ink-900">{data.total}</span> 条结果
                </>
              ) : (
                '加载中'
              )}
            </p>
            <div className="flex items-center gap-3">
              {data && data.total > 0 && (
                <a
                  href={api.announcements.exportUrl(searchParams)}
                  target="_blank"
                  rel="noreferrer"
                  title="按当前筛选全量导出（不分页）"
                  className="text-xs text-ink-400 hover:text-accent inline-flex items-center gap-1"
                >
                  <Download className="w-3 h-3" strokeWidth={1.5} />
                  导出 CSV
                </a>
              )}
              {(activeCount > 0 || filters.keyword) && (
                <button
                  onClick={() => {
                    filters.reset()
                    setKeywordInput('')
                  }}
                  className="text-xs text-ink-400 hover:text-accent inline-flex items-center gap-1"
                >
                  <X className="w-3 h-3" strokeWidth={1.5} />
                  清除筛选
                </button>
              )}
            </div>
          </div>

          {isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <CardSkeleton key={i} />
              ))}
            </div>
          ) : isError ? (
            <Empty
              title="加载失败"
              hint="后端服务可能未启动"
              action={
                <button onClick={() => refetch()} className="text-xs text-accent hover:underline">
                  重试
                </button>
              }
            />
          ) : !data || data.items.length === 0 ? (
            <Empty title="未找到匹配公告" hint="尝试放宽筛选条件或更换关键词" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {data.items.map((item, i) => (
                <AnnouncementCard key={item.id} item={item} index={i} showMatch />
              ))}
            </div>
          )}

          {/* 分页 —— 后端 total 是全量命中数，不止这一页 */}
          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-center gap-4 pt-5">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="px-4 py-2 rounded-lg bg-zinc-100 text-ink-600 text-sm font-medium hover:bg-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                上一页
              </button>
              <span className="text-sm text-ink-400">
                第 {page} 页 / 共 {Math.ceil(data.total / PAGE_SIZE)} 页
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= Math.ceil(data.total / PAGE_SIZE)}
                className="px-4 py-2 rounded-lg bg-zinc-100 text-ink-600 text-sm font-medium hover:bg-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                下一页
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 移动端筛选抽屉 */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-ink-900/30 backdrop-blur-sm"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute right-0 top-0 bottom-0 w-80 max-w-[85vw] bg-surface p-5 overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold">筛选条件</h3>
              <button
                onClick={() => setDrawerOpen(false)}
                className="tap rounded-md text-ink-600 hover:bg-zinc-100"
              >
                <X className="w-5 h-5" strokeWidth={1.5} />
              </button>
            </div>
            <FilterPanel
              categories={categories?.l1 ?? []}
              industries={stats?.ind_data.map((i) => i.name) ?? []}
              provinces={regions?.provinces ?? []}
              onPick={() => setDrawerOpen(false)}
            />
          </div>
        </div>
      )}
    </div>
  )
}

function FilterPanel({
  categories,
  industries,
  provinces,
  onPick,
}: {
  categories: string[]
  industries: string[]
  provinces: string[]
  onPick?: () => void
}) {
  const filters = useFilters()
  const [regionOpen, setRegionOpen] = useState(true)

  return (
    <div className="space-y-5">
      {/* 层级 */}
      <FilterGroup title="行政层级">
        <div className="flex flex-wrap gap-1.5">
          {LEVELS.map((l) => (
            <FilterChip
              key={l.value}
              active={filters.gov_level === l.value}
              onClick={() => {
                filters.set({ gov_level: filters.gov_level === l.value ? null : l.value })
                onPick?.()
              }}
            >
              {l.label}
            </FilterChip>
          ))}
        </div>
      </FilterGroup>

      {/* 分类 */}
      <FilterGroup title="一级分类">
        <div className="flex flex-wrap gap-1.5">
          {categories.map((c) => (
            <FilterChip
              key={c}
              active={filters.category === c}
              onClick={() => {
                filters.set({ category: filters.category === c ? null : c })
                onPick?.()
              }}
            >
              {c}
            </FilterChip>
          ))}
        </div>
      </FilterGroup>

      {/* 地域 */}
      <FilterGroup title="地域">
        <button
          onClick={() => setRegionOpen((v) => !v)}
          className="flex items-center gap-1 text-xs text-ink-400 mb-2"
        >
          <ChevronDown
            className={cn('w-3 h-3 transition-transform', !regionOpen && '-rotate-90')}
            strokeWidth={1.5}
          />
          {filters.region ?? '选择省份'}
        </button>
        {regionOpen && (
          <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
            {provinces.map((p) => (
              <FilterChip
                key={p}
                active={filters.region === p}
                onClick={() => {
                  filters.set({ region: filters.region === p ? null : p })
                  onPick?.()
                }}
              >
                {p}
              </FilterChip>
            ))}
          </div>
        )}
      </FilterGroup>

      {/* 行业门类 —— 全库 target_group/target_industry 都是空的，能用的行业轴是 category_l2 */}
      {industries.length > 0 && (
        <FilterGroup title="行业门类">
          <div className="flex flex-wrap gap-1.5">
            {industries.map((c) => (
              <FilterChip
                key={c}
                active={filters.category_l2 === c}
                onClick={() => {
                  filters.set({ category_l2: filters.category_l2 === c ? null : c })
                  onPick?.()
                }}
              >
                {c}
              </FilterChip>
            ))}
          </div>
        </FilterGroup>
      )}
    </div>
  )
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-ink-700 mb-2">{title}</h4>
      {children}
    </div>
  )
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-2.5 h-7 rounded-md text-xs font-medium transition-colors',
        active
          ? 'bg-accent text-white'
          : 'bg-zinc-100 text-ink-600 hover:bg-zinc-200/60',
      )}
    >
      {children}
    </button>
  )
}
