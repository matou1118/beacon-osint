import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ExternalLink,
  MapPin,
  Calendar,
  Building2,
  Coins,
  Clock,
  CheckCircle2,
  ArrowUpRight,
  Target,
} from 'lucide-react'
import type { Announcement } from '@/shared/types'
import { Tag } from '@/shared/ui/Tag'
import { formatDate, levelLabel, corroborationLabel } from '@/shared/utils/format'
import { getCategoryConfig } from '@/shared/config/categories'

export function AnnouncementCard({
  item,
  index = 0,
  showMatch = false,
}: {
  item: Announcement
  index?: number
  /** 检索页打开：命中了哪些规则（关键词/层级/地域），让「匹配可解释」看得见 */
  showMatch?: boolean
}) {
  const corr = corroborationLabel(item.corroboration_status)
  const cat = getCategoryConfig(item.category_l1)
  const domain = extractDomain(item.source_url)
  const reasons = (item.match_reasons ?? []).slice(0, 2)

  return (
    <motion.article
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.04, 0.2) }}
      className="card p-5 hover:shadow-pop hover:border-zinc-300/60 transition-all duration-200 group"
    >
      {/* 顶部标签条 —— 分类色块 + 层级 + 印证 */}
      <div className="flex items-center gap-1.5 flex-wrap mb-3">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${cat.bg} ${cat.text}`}
        >
          {cat.short}
        </span>
        {item.gov_level && <Tag tone="accent">{levelLabel(item.gov_level)}</Tag>}
        {item.category_l2 && <Tag tone="muted">{item.category_l2}</Tag>}
        {(item.industry_tags ?? []).slice(0, 2).map((tag) => (
          <Tag key={tag} tone="info">{tag}</Tag>
        ))}
        {item.corroboration_count > 1 && (
          <Tag tone={corr.tone}>
            <CheckCircle2 className="w-3 h-3 mr-0.5" strokeWidth={1.5} />
            {corr.text} · {item.corroboration_count}
          </Tag>
        )}
      </div>

      {/* 标题 */}
      <Link to={`/announcements/${item.id}`} className="block">
        <h3 className="text-base font-semibold text-ink-900 leading-snug group-hover:text-accent transition-colors truncate-2">
          {item.title}
        </h3>
      </Link>

      {/* 摘要 —— 关键要点列表 */}
      {item.key_points && item.key_points.length > 0 && (
        <ul className="mt-2.5 space-y-1">
          {item.key_points.slice(0, 3).map((point, i) => (
            <li key={i} className="flex items-start gap-1.5 text-sm text-ink-600 leading-relaxed">
              <span className="text-accent mt-1.5 shrink-0 w-1 h-1 rounded-full bg-current" />
              <span className="truncate-2">{point}</span>
            </li>
          ))}
        </ul>
      )}

      {/* 命中说明 —— 这条为什么被搜出来 */}
      {showMatch && reasons.length > 0 && (
        <div className="mt-3 flex items-start gap-1.5 rounded-md bg-accent-soft px-2.5 py-1.5 text-xs text-ink-600 leading-relaxed">
          <Target className="w-3.5 h-3.5 mt-0.5 shrink-0 text-accent" strokeWidth={1.5} />
          <span className="truncate-2">
            {item.match_score ? `匹配 ${item.match_score} · ` : ''}
            {reasons.join(' · ')}
          </span>
        </div>
      )}

      {/* 关键结构化字段 —— 金额/截止日期 突出显示 */}
      {(item.amount || item.deadline) && (
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          {item.amount && (
            <div className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-sky-50 text-sky-700 text-xs font-medium">
              <Coins className="w-3.5 h-3.5" strokeWidth={1.5} />
              {item.amount}
            </div>
          )}
          {item.deadline && (
            <div className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-orange-50 text-orange-700 text-xs font-medium">
              <Clock className="w-3.5 h-3.5" strokeWidth={1.5} />
              截止 {formatDate(item.deadline)}
            </div>
          )}
        </div>
      )}

      {/* 元信息行 */}
      <div className="mt-3.5 flex items-center gap-3 text-xs text-ink-400 flex-wrap">
        <span className="inline-flex items-center gap-1">
          <Building2 className="w-3.5 h-3.5" strokeWidth={1.5} />
          {item.issuing_body}
        </span>
        {item.region && (
          <span className="inline-flex items-center gap-1">
            <MapPin className="w-3.5 h-3.5" strokeWidth={1.5} />
            {item.region}
          </span>
        )}
        {item.publish_date && (
          <span className="inline-flex items-center gap-1">
            <Calendar className="w-3.5 h-3.5" strokeWidth={1.5} />
            {formatDate(item.publish_date)}
          </span>
        )}
      </div>

      {/* 底部操作栏 —— 原文直链突出 + 详情链接 */}
      <div className="mt-3.5 pt-3 border-t border-zinc-100 flex items-center justify-between">
        <a
          href={item.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:text-accent-dark transition-colors group/link"
        >
          <ExternalLink className="w-3.5 h-3.5" strokeWidth={1.5} />
          <span className="truncate max-w-[180px]">{domain}</span>
          <ArrowUpRight className="w-3 h-3 opacity-0 group-hover/link:opacity-100 transition-opacity" strokeWidth={1.5} />
        </a>
        <Link
          to={`/announcements/${item.id}`}
          className="text-xs text-ink-400 hover:text-ink-700 transition-colors"
        >
          查看详情 →
        </Link>
      </div>
    </motion.article>
  )
}

// 从 URL 提取域名
function extractDomain(url: string): string {
  try {
    const u = new URL(url)
    return u.hostname
  } catch {
    return url
  }
}
