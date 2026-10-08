import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  Flame,
  Calendar,
  ExternalLink,
  Newspaper,
  AlertCircle,
} from 'lucide-react'
import { api } from '@/shared/api/client'
import { Empty } from '@/shared/ui/Empty'
import { Tag } from '@/shared/ui/Tag'

export function HeatDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['heat', 'topic', clusterId],
    queryFn: () => api.heat.detail(clusterId!),
    enabled: !!clusterId,
  })

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="h-6 w-32 bg-zinc-200/60 rounded animate-pulse-soft" />
        <div className="h-8 w-3/4 bg-zinc-200/60 rounded animate-pulse-soft" />
        <div className="h-40 bg-zinc-200/60 rounded animate-pulse-soft" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="max-w-3xl mx-auto">
        <Empty title="话题不存在" hint="可能已被新的采集覆盖或链接有误" />
      </div>
    )
  }

  const heat = data.heat_score ?? 0

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <Link to="/heat" className="inline-flex items-center gap-1.5 text-sm text-ink-400 hover:text-accent">
        <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
        返回热频话题
      </Link>

      {/* 分数 + 分类 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Tag tone="accent">
          <Flame className="w-3 h-3 mr-0.5" strokeWidth={1.5} />
          热度 {heat.toFixed(1)}
        </Tag>
        {data.category_l1 && <Tag tone="muted">{data.category_l1}</Tag>}
        {data.category_l2 && <Tag tone="muted">{data.category_l2}</Tag>}
        <span className="text-xs text-ink-400 inline-flex items-center gap-1">
          <Newspaper className="w-3.5 h-3.5" strokeWidth={1.5} />
          {data.source_count ?? (data.source_names?.length ?? 0)} 独立源
        </span>
        {data.report_date && (
          <span className="text-xs text-ink-400 inline-flex items-center gap-1">
            <Calendar className="w-3.5 h-3.5" strokeWidth={1.5} />
            {data.report_date}
          </span>
        )}
      </div>

      <h1 className="text-2xl font-semibold text-ink-900 leading-tight">
        {data.title || data.cluster_id}
      </h1>

      {/* 聚类原因 —— 这个话题为什么算热 */}
      {data.heat_reason && (
        <div className="card p-4 border-warn/30 bg-warn/5 text-sm text-ink-700 leading-relaxed">
          {data.heat_reason}
        </div>
      )}

      {/* 摘要 */}
      {data.summary && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">话题摘要</h3>
          <p className="text-sm text-ink-700 leading-relaxed whitespace-pre-wrap">{data.summary}</p>
        </div>
      )}

      {/* 关键词 */}
      {data.terms && data.terms.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">聚类关键词</h3>
          <div className="flex flex-wrap gap-1.5">
            {data.terms.map((term) => (
              <Tag key={term} tone="muted">{term}</Tag>
            ))}
          </div>
        </div>
      )}

      {/* 报道来源 */}
      {(data.source_names?.length ?? 0) > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">报道来源</h3>
          <div className="flex flex-wrap gap-2">
            {data.source_names!.map((name) => (
              <span
                key={name}
                className="px-2.5 py-1 rounded-md bg-zinc-100 text-ink-700 text-xs font-medium"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 原文链接 */}
      {(data.article_urls?.length ?? 0) > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">相关文章</h3>
          <ul className="space-y-2">
            {data.article_urls!.map((url, i) => (
              <li key={i}>
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-accent hover:text-accent-dark break-all"
                >
                  <ExternalLink className="w-3.5 h-3.5 shrink-0" strokeWidth={1.5} />
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!data.summary && !data.heat_reason && (data.article_urls?.length ?? 0) === 0 && (
        <div className="card p-4 flex items-start gap-3 border-warn/30 bg-warn/5">
          <AlertCircle className="w-4 h-4 text-warn shrink-0 mt-0.5" strokeWidth={1.5} />
          <p className="text-sm text-ink-700">这个话题只留下了分数与关键词，原始文章可能已被后续采集覆盖。</p>
        </div>
      )}
    </div>
  )
}
