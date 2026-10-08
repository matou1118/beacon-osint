import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams, Link } from 'react-router-dom'
import {
  Network,
  AlertCircle,
  Search,
  ArrowRight,
  ArrowLeft,
  ExternalLink,
  X,
} from 'lucide-react'
import { api } from '@/shared/api/client'
import { Empty } from '@/shared/ui/Empty'
import { Tag } from '@/shared/ui/Tag'
import { StatCard } from '@/shared/ui/StatCard'
import { BarH, Donut, ChartCard } from '@/shared/ui/charts'
import { cn } from '@/shared/utils/format'

const TYPE_LABEL: Record<string, string> = {
  announcement: '公告',
  organization: '机构',
  region: '地域',
  category: '分类',
  industry: '行业',
  audience: '人群',
  source: '来源',
}

function typeLabel(type?: string) {
  if (!type) return '节点'
  return TYPE_LABEL[type] ?? type
}

function toData(data?: Record<string, number>) {
  return Object.entries(data ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ name, value }))
}

export function GraphPage() {
  const [params, setParams] = useSearchParams()
  const nodeId = params.get('node')

  const [input, setInput] = useState('')
  const [keyword, setKeyword] = useState('')

  const { data: summary, isLoading, isError } = useQuery({
    queryKey: ['graph', 'summary'],
    queryFn: api.graph.summary,
  })

  // 空关键词也给一批节点 —— 否则用户没有任何起点
  const { data: search } = useQuery({
    queryKey: ['graph', 'search', keyword],
    queryFn: () => api.graph.search(keyword, 20),
  })

  const { data: detail, isLoading: detailLoading, isError: detailError } = useQuery({
    queryKey: ['graph', 'node', nodeId],
    queryFn: () => api.graph.node(nodeId!),
    enabled: !!nodeId,
    retry: false,
  })

  const selectNode = (id: string) => {
    const next = new URLSearchParams(params)
    next.set('node', id)
    setParams(next)
  }

  const clearNode = () => {
    const next = new URLSearchParams(params)
    next.delete('node')
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink-900 tracking-tight flex items-center gap-2">
          <Network className="w-6 h-6 text-accent" strokeWidth={1.5} />
          知识图谱
        </h1>
        <p className="text-sm text-ink-400 mt-1">
          从公告结构化字段构建 · 节点可追溯 · 关系有证据
        </p>
      </div>

      {isLoading ? (
        <div className="card p-20 flex items-center justify-center">
          <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : isError ? (
        <Empty
          title="图谱加载失败"
          hint="后端服务可能未启动"
          action={<AlertCircle className="w-5 h-5 text-ink-400" strokeWidth={1.5} />}
        />
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="节点总数" value={summary?.nodes ?? 0} />
            <StatCard label="关系总数" value={summary?.edges ?? 0} />
            <StatCard label="证据条目" value={summary?.evidence ?? 0} />
            <StatCard
              label="待复核"
              value={summary?.needs_review ?? 0}
              tone={summary && summary.needs_review > 0 ? 'warn' : 'default'}
            />
          </div>

          {summary && summary.needs_review > 0 && (
            <div className="card p-4 flex items-start gap-3 border-warn/30 bg-warn/5">
              <AlertCircle className="w-4 h-4 text-warn shrink-0 mt-0.5" strokeWidth={1.5} />
              <p className="text-sm text-ink-700 leading-relaxed">
                有 <span className="font-mono font-semibold">{summary.needs_review.toLocaleString()}</span>{' '}
                条关系置信度不足，等待人工确认后才计入统计口径。
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ChartCard title="节点类型" total={summary?.nodes}>
              <Donut
                data={toData(summary?.node_types).map((d) => ({ ...d, name: typeLabel(d.name) }))}
                height={230}
              />
            </ChartCard>
            <ChartCard title="关系类型" total={summary?.edges}>
              <BarH data={toData(summary?.edge_types)} height={230} />
            </ChartCard>
          </div>
        </>
      )}

      {/* 节点探索 —— 搜索 → 详情 → 沿关系走 */}
      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4 items-start">
        <div className="card p-4 space-y-3">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              setKeyword(input.trim())
            }}
            className="flex items-center gap-2"
          >
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-400" strokeWidth={1.5} />
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="搜节点名称，如 四川省人社厅"
                className="w-full h-9 pl-9 pr-3 rounded-md border border-zinc-200 bg-surface text-sm placeholder:text-ink-400 focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
              />
            </div>
            <button
              type="submit"
              className="h-9 px-3 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors"
            >
              搜索
            </button>
          </form>

          <div className="text-xs text-ink-400">
            {keyword ? `「${keyword}」` : '节点（按名称）'} · {search?.nodes.length ?? 0} 个
          </div>

          <div className="space-y-1 max-h-96 overflow-y-auto">
            {(search?.nodes ?? []).map((node) => (
              <button
                key={node.id}
                onClick={() => selectNode(node.id)}
                className={cn(
                  'w-full text-left px-3 py-2 rounded-md text-sm transition-colors flex items-center gap-2',
                  node.id === nodeId
                    ? 'bg-accent-soft text-accent'
                    : 'hover:bg-zinc-100 text-ink-700',
                )}
              >
                <span className="shrink-0">
                  <Tag tone="muted">{typeLabel(node.type)}</Tag>
                </span>
                <span className="truncate">{node.name || node.id}</span>
              </button>
            ))}
            {(search?.nodes.length ?? 0) === 0 && (
              <p className="text-xs text-ink-400 px-3 py-2">没有匹配的节点</p>
            )}
          </div>
        </div>

        {!nodeId ? (
          <div className="card p-10">
            <Empty
              title="选一个节点开始探索"
              hint="左侧搜索或直接点节点列表，右侧会列出它的关系，点关系可以继续往下走"
            />
          </div>
        ) : detailLoading ? (
          <div className="card p-6 space-y-3">
            <div className="h-6 w-1/2 bg-zinc-200/60 rounded animate-pulse-soft" />
            <div className="h-40 bg-zinc-200/60 rounded animate-pulse-soft" />
          </div>
        ) : detailError || !detail ? (
          <div className="card p-6">
            <Empty
              title="节点不存在"
              hint="它可能在最近一次重建图谱时被清理掉了"
              action={
                <button onClick={clearNode} className="text-xs text-accent hover:underline">
                  回到节点列表
                </button>
              }
            />
          </div>
        ) : (
          <div className="card p-5 space-y-5">
            {/* 当前节点 */}
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1.5">
                  <Tag tone="accent">{typeLabel(detail.node.type)}</Tag>
                  <code className="text-[11px] text-ink-400 truncate">{detail.node.id}</code>
                </div>
                <h2 className="text-lg font-semibold text-ink-900 leading-snug break-words">
                  {detail.node.name || detail.node.id}
                </h2>
              </div>
              <button
                onClick={clearNode}
                className="shrink-0 p-1.5 rounded-md text-ink-400 hover:bg-zinc-100 hover:text-ink-700"
                aria-label="关闭节点"
              >
                <X className="w-4 h-4" strokeWidth={1.5} />
              </button>
            </div>

            {/* 属性 */}
            {Object.keys(detail.node.attributes ?? {}).length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-3">
                {Object.entries(detail.node.attributes ?? {})
                  .slice(0, 12)
                  .map(([key, value]) => (
                    <div key={key} className="min-w-0">
                      <p className="text-xs text-ink-400 mb-0.5">{key}</p>
                      <p className="text-sm text-ink-900 break-words">
                        {typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—')}
                      </p>
                    </div>
                  ))}
              </div>
            )}

            {/* 关系 */}
            <div>
              <h3 className="text-sm font-semibold text-ink-900 mb-3">
                关系 · {detail.neighbors.length}
              </h3>
              {detail.neighbors.length === 0 ? (
                <p className="text-sm text-ink-400">这个节点暂时没有已确认的关系</p>
              ) : (
                <ul className="space-y-1.5">
                  {detail.neighbors.map((nb, i) => (
                    <li key={`${nb.node.id}-${nb.edge_type}-${i}`}>
                      <button
                        onClick={() => selectNode(nb.node.id)}
                        className="w-full text-left px-3 py-2 rounded-md hover:bg-zinc-100 transition-colors flex items-center gap-2 text-sm group"
                      >
                        {nb.direction === 'outgoing' ? (
                          <ArrowRight className="w-3.5 h-3.5 text-ink-300 shrink-0" strokeWidth={1.5} />
                        ) : (
                          <ArrowLeft className="w-3.5 h-3.5 text-ink-300 shrink-0" strokeWidth={1.5} />
                        )}
                        <span className="shrink-0">
                          <Tag tone="muted">{nb.edge_type}</Tag>
                        </span>
                        <span className="truncate text-ink-700 group-hover:text-accent">
                          {nb.node.name || nb.node.id}
                        </span>
                        <span className="ml-auto shrink-0 text-[11px] text-ink-400">
                          {typeLabel(nb.node.type)}
                        </span>
                        {nb.needs_review && (
                          <span className="shrink-0 text-[11px] text-warn">待复核</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 公告节点直达原文 */}
            {detail.node.type === 'announcement' && (
              <Link
                to={`/announcements/${detail.node.id.split(':')[1] ?? ''}`}
                className="inline-flex items-center gap-1.5 text-sm text-accent hover:text-accent-dark"
              >
                <ExternalLink className="w-3.5 h-3.5" strokeWidth={1.5} />
                打开这条公告的详情页
              </Link>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
