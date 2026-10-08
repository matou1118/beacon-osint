import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  Plus,
  Trash2,
  Power,
  MapPin,
  Tag as TagIcon,
  KeyRound,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import type { Subscription } from '@/shared/types'
import { Button } from '@/shared/ui/Button'
import { Tag } from '@/shared/ui/Tag'
import { Empty } from '@/shared/ui/Empty'
import { useAuth } from '@/shared/store/auth'
import { cn } from '@/shared/utils/format'

export function SubscriptionsPage() {
  const isAuth = useAuth((s) => s.isAuthenticated)
  const logout = useAuth((s) => s.logout)
  const userId = useAuth((s) => s.userId)
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)

  const { data: subs, isLoading, isError, refetch } = useQuery({
    queryKey: ['subscriptions'],
    queryFn: api.subscriptions.list,
    enabled: isAuth,
  })

  const { data: categories } = useQuery({
    queryKey: ['categories'],
    queryFn: api.categories,
    staleTime: 600_000,
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.subscriptions.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['subscriptions'] }),
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.subscriptions.push(id, { push_frequency: 'daily', enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['subscriptions'] }),
  })

  if (!isAuth) {
    return (
      <Empty title="请先登录" hint="登录后可管理您的订阅" />
    )
  }

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink-900 tracking-tight flex items-center gap-2">
            <Bell className="w-6 h-6 text-accent" strokeWidth={1.5} />
            订阅中心
          </h1>
          <p className="text-sm text-ink-400 mt-1">
            管理个性化信息订阅 · 匹配可解释 · 推送可关闭
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-400 hidden sm:inline">{userId}</span>
          <Button variant="ghost" size="sm" onClick={logout}>
            退出
          </Button>
        </div>
      </div>

      {/* 新建按钮 */}
      {!showForm && (
        <Button onClick={() => setShowForm(true)}>
          <Plus className="w-4 h-4" strokeWidth={1.5} />
          新建订阅
        </Button>
      )}

      {/* 新建表单 */}
      {showForm && (
        <SubscriptionForm
          categories={categories?.l1 ?? []}
          onDone={() => setShowForm(false)}
        />
      )}

      {/* 订阅列表 */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card p-5 h-24 animate-pulse-soft bg-zinc-100/60" />
          ))}
        </div>
      ) : isError ? (
        <div className="card p-4 flex items-start justify-between gap-3 border-danger/30 bg-danger/5">
          <span className="text-sm text-ink-700 inline-flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" strokeWidth={1.5} />
            订阅加载失败，后端服务可能未启动
          </span>
          <button onClick={() => refetch()} className="text-xs text-accent hover:underline shrink-0">
            重试
          </button>
        </div>
      ) : !subs || subs.length === 0 ? (
        <Empty
          title="暂无订阅"
          hint="创建订阅后，系统将按您的条件筛选并推送匹配的公告"
        />
      ) : (
        <div className="space-y-3">
          {subs.map((sub) => (
            <SubscriptionCard
              key={sub.id}
              sub={sub}
              onDelete={() => deleteMut.mutate(sub.id)}
              onToggle={() => toggleMut.mutate({ id: sub.id, enabled: !sub.enabled })}
              deleting={deleteMut.isPending}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SubscriptionCard({
  sub,
  onDelete,
  onToggle,
  deleting,
}: {
  sub: Subscription
  onDelete: () => void
  onToggle: () => void
  deleting: boolean
}) {
  return (
    <article className="card p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0 space-y-2">
          {/* 条件标签 */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {sub.gov_levels.map((l) => (
              <Tag key={l} tone="accent">
                {l}
              </Tag>
            ))}
            {sub.region && (
              <Tag>
                <MapPin className="w-3 h-3" strokeWidth={1.5} />
                {sub.region}
              </Tag>
            )}
            {sub.categories.map((c) => (
              <Tag key={c}>
                <TagIcon className="w-3 h-3" strokeWidth={1.5} />
                {c}
              </Tag>
            ))}
            {sub.target_group && <Tag tone="muted">{sub.target_group}</Tag>}
            {sub.target_industry && <Tag tone="muted">{sub.target_industry}</Tag>}
            {sub.keywords.map((k) => (
              <Tag key={k} tone="info">
                <KeyRound className="w-3 h-3" strokeWidth={1.5} />
                {k}
              </Tag>
            ))}
          </div>

          {/* 元信息 */}
          <div className="flex items-center gap-3 text-xs text-ink-400">
            <span>创建于 {sub.created_at ?? '—'}</span>
            <span>·</span>
            <span>推送频率：{sub.push_frequency === 'daily' ? '每日' : sub.push_frequency === 'weekly' ? '每周' : '关闭'}</span>
          </div>
        </div>

        {/* 操作 */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={onToggle}
            className={cn(
              'tap rounded-md p-2 transition-colors',
              sub.enabled
                ? 'text-ok hover:bg-emerald-50'
                : 'text-ink-400 hover:bg-zinc-100',
            )}
            title={sub.enabled ? '已启用' : '已停用'}
          >
            <Power className="w-4 h-4" strokeWidth={1.5} />
          </button>
          <button
            onClick={onDelete}
            disabled={deleting}
            className="tap rounded-md p-2 text-ink-400 hover:bg-red-50 hover:text-danger transition-colors disabled:opacity-50"
            title="删除"
          >
            <Trash2 className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>
      </div>
    </article>
  )
}

function SubscriptionForm({
  categories,
  onDone,
}: {
  categories: string[]
  onDone: () => void
}) {
  const qc = useQueryClient()
  const [govLevels, setGovLevels] = useState<string[]>([])
  const [selectedCats, setSelectedCats] = useState<string[]>([])
  const [keywords, setKeywords] = useState('')
  const [error, setError] = useState<string | null>(null)

  const createMut = useMutation({
    mutationFn: () =>
      api.subscriptions.create({
        gov_levels: govLevels,
        categories: selectedCats,
        keywords: keywords
          .split(/[\s,，]+/)
          .map((s) => s.trim())
          .filter(Boolean),
        push_frequency: 'daily',
        enabled: true,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
      onDone()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : '创建失败'),
  })

  const LEVELS = ['国家部委', '省级', '市级', '县级']

  return (
    <div className="card p-6 space-y-5">
      <h3 className="text-sm font-semibold text-ink-900">新建订阅</h3>

      {/* 层级 */}
      <div>
        <label className="text-xs font-medium text-ink-700 mb-2 block">行政层级</label>
        <div className="flex flex-wrap gap-1.5">
          {LEVELS.map((l) => (
            <button
              key={l}
              onClick={() =>
                setGovLevels((prev) =>
                  prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l],
                )
              }
              className={cn(
                'px-2.5 h-7 rounded-md text-xs font-medium transition-colors',
                govLevels.includes(l)
                  ? 'bg-accent text-white'
                  : 'bg-zinc-100 text-ink-600 hover:bg-zinc-200/60',
              )}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      {/* 分类 */}
      <div>
        <label className="text-xs font-medium text-ink-700 mb-2 block">关注分类</label>
        <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
          {categories.map((c) => (
            <button
              key={c}
              onClick={() =>
                setSelectedCats((prev) =>
                  prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
                )
              }
              className={cn(
                'px-2.5 h-7 rounded-md text-xs font-medium transition-colors',
                selectedCats.includes(c)
                  ? 'bg-accent text-white'
                  : 'bg-zinc-100 text-ink-600 hover:bg-zinc-200/60',
              )}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* 关键词 */}
      <div>
        <label className="text-xs font-medium text-ink-700 mb-2 block">
          关键词（空格或逗号分隔）
        </label>
        <input
          type="text"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          placeholder="如：补贴 招聘 招生"
          className="w-full h-10 px-3 rounded-md border border-zinc-200 bg-surface text-sm placeholder:text-ink-400 focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
        />
      </div>

      {error && (
        <p className="text-xs text-danger flex items-center gap-1.5">
          <AlertCircle className="w-3.5 h-3.5" strokeWidth={1.5} />
          {error}
        </p>
      )}

      <div className="flex gap-2">
        <Button
          onClick={() => createMut.mutate()}
          loading={createMut.isPending}
          disabled={govLevels.length === 0 && selectedCats.length === 0 && !keywords.trim()}
        >
          <CheckCircle2 className="w-4 h-4" strokeWidth={1.5} />
          创建
        </Button>
        <Button variant="ghost" onClick={onDone}>
          取消
        </Button>
      </div>
    </div>
  )
}
