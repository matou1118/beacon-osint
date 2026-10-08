import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  ArrowLeft,
  ExternalLink,
  Building2,
  MapPin,
  Calendar,
  CheckCircle2,
  AlertCircle,
  ThumbsUp,
  ThumbsDown,
  ShieldCheck,
  Network,
} from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { Tag } from '@/shared/ui/Tag'
import { Button } from '@/shared/ui/Button'
import { Empty } from '@/shared/ui/Empty'
import { formatDate, levelLabel, corroborationLabel } from '@/shared/utils/format'
import { useAuth } from '@/shared/store/auth'

export function DetailPage() {
  const { id } = useParams<{ id: string }>()
  const isAuth = useAuth((s) => s.isAuthenticated)
  const qc = useQueryClient()
  const [feedbackSent, setFeedbackSent] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['announcement', id],
    queryFn: () => api.announcements.detail(id!),
    enabled: !!id,
  })

  const feedbackMut = useMutation({
    mutationFn: (useful: boolean) =>
      api.feedback({ announcement_id: Number(id), useful }),
    onSuccess: () => {
      setFeedbackSent(true)
      qc.invalidateQueries({ queryKey: ['announcement', id] })
    },
  })

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="h-6 w-32 bg-zinc-200/60 rounded animate-pulse-soft" />
        <div className="h-8 w-3/4 bg-zinc-200/60 rounded animate-pulse-soft" />
        <div className="h-32 bg-zinc-200/60 rounded animate-pulse-soft" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="max-w-3xl mx-auto">
        <Empty title="公告不存在" hint="可能已被归档或链接有误" />
      </div>
    )
  }

  const corr = corroborationLabel(data.corroboration_status)

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* 返回 */}
      <Link
        to="/search"
        className="inline-flex items-center gap-1.5 text-sm text-ink-400 hover:text-accent"
      >
        <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
        返回列表
      </Link>

      {/* 标签条 */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {data.gov_level && <Tag tone="accent">{levelLabel(data.gov_level)}</Tag>}
        {data.category_l1 && <Tag>{data.category_l1}</Tag>}
        {data.category_l2 && <Tag tone="muted">{data.category_l2}</Tag>}
        <Tag tone={corr.tone}>
          {corr.text} · {data.corroboration_count} 源
        </Tag>
        <Link
          to={`/graph?node=${encodeURIComponent(`announcement:${id}`)}`}
          className="ml-auto inline-flex items-center gap-1 text-xs text-ink-400 hover:text-accent"
        >
          <Network className="w-3.5 h-3.5" strokeWidth={1.5} />
          在图谱中查看
        </Link>
      </div>

      {/* 标题 */}
      <h1 className="text-2xl font-semibold text-ink-900 leading-tight">{data.title}</h1>

      {/* 原文直链 —— 突出显示 */}
      <a
        href={data.source_url}
        target="_blank"
        rel="noopener noreferrer"
        className="card p-4 flex items-center justify-between hover:border-accent/40 hover:shadow-pop transition-all group"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-accent-soft flex items-center justify-center">
            <ExternalLink className="w-5 h-5 text-accent" strokeWidth={1.5} />
          </div>
          <div>
            <p className="text-sm font-semibold text-ink-900">查看政府网站原文</p>
            <p className="text-xs text-ink-400 mt-0.5">{extractDomain(data.source_url)}</p>
          </div>
        </div>
        <ExternalLink className="w-5 h-5 text-ink-400 group-hover:text-accent transition-colors" strokeWidth={1.5} />
      </a>

      {/* 元信息 */}
      <div className="flex items-center gap-4 text-sm text-ink-600 flex-wrap">
        <span className="inline-flex items-center gap-1.5">
          <Building2 className="w-4 h-4 text-ink-400" strokeWidth={1.5} />
          {data.issuing_body}
        </span>
        {data.region && (
          <span className="inline-flex items-center gap-1.5">
            <MapPin className="w-4 h-4 text-ink-400" strokeWidth={1.5} />
            {data.region}
          </span>
        )}
        {data.publish_date && (
          <span className="inline-flex items-center gap-1.5">
            <Calendar className="w-4 h-4 text-ink-400" strokeWidth={1.5} />
            {formatDate(data.publish_date)}
          </span>
        )}
      </div>

      {/* 结构化字段卡 */}
      {(data.amount || data.deadline || data.target_group || data.target_industry) && (
        <div className="card p-5 grid grid-cols-2 gap-4">
          {data.amount && <Field label="金额" value={data.amount} />}
          {data.deadline && <Field label="截止日期" value={formatDate(data.deadline)} />}
          {data.target_group && <Field label="适用人群" value={data.target_group} />}
          {data.target_industry && <Field label="适用行业" value={data.target_industry} />}
        </div>
      )}

      {/* 要点 */}
      {data.key_points && data.key_points.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">关键要点</h3>
          <ul className="space-y-2">
            {data.key_points.map((p, i) => (
              <li key={i} className="flex gap-2 text-sm text-ink-700 leading-relaxed">
                <span className="text-accent shrink-0">·</span>
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 正文 */}
      {data.raw_text && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">正文</h3>
          <p className="text-sm text-ink-700 leading-relaxed whitespace-pre-wrap">
            {data.raw_text}
          </p>
        </div>
      )}

      {/* 交叉印证 */}
      {data.corroborating_sources && data.corroborating_sources.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" strokeWidth={1.5} />
            交叉印证来源
          </h3>
          <div className="space-y-2">
            {data.corroborating_sources.map((s, i) => (
              <a
                key={i}
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-ink-600 hover:text-accent"
              >
                <ExternalLink className="w-3.5 h-3.5" strokeWidth={1.5} />
                {s.domain ?? s.source_id ?? `来源 ${i + 1}`}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* 原文核验 —— 底部冗余链接 */}

      {/* 反馈 */}
      {isAuth && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-900 mb-3">这条信息对您有用吗？</h3>
          {feedbackSent ? (
            <p className="text-sm text-ok flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" strokeWidth={1.5} />
              感谢反馈
            </p>
          ) : (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => feedbackMut.mutate(true)}
                loading={feedbackMut.isPending}
              >
                <ThumbsUp className="w-4 h-4" strokeWidth={1.5} />
                有用
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => feedbackMut.mutate(false)}
                loading={feedbackMut.isPending}
              >
                <ThumbsDown className="w-4 h-4" strokeWidth={1.5} />
                无关
              </Button>
            </div>
          )}
          {feedbackMut.error instanceof ApiError && (
            <p className="text-xs text-danger mt-2 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" strokeWidth={1.5} />
              {feedbackMut.error.message}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-ink-400 mb-1">{label}</p>
      <p className="text-sm font-medium text-ink-900">{value}</p>
    </div>
  )
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}
