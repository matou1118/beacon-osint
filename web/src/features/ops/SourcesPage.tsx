import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Activity,
  Radio,
  Clock,
  CircleSlash,
  Stethoscope,
  Search,
  ArrowUpDown,
} from 'lucide-react'
import { api } from '@/shared/api/client'
import { Empty } from '@/shared/ui/Empty'
import { StatCard } from '@/shared/ui/StatCard'
import { BarH, Donut, ChartCard, TONE } from '@/shared/ui/charts'
import { SectionNav, Section, type Section as NavSection } from '@/shared/ui/SectionNav'
import { formatDate } from '@/shared/utils/format'

const CADENCE_LABEL: Record<string, string> = {
  every_2h: '每 2 小时',
  every_4h: '每 4 小时',
  every_6h: '每 6 小时',
  daily: '每日',
  weekly: '每周',
}

const CADENCE_HINT: Record<string, string> = {
  T0: '主力源',
  T1: '低产源',
  T2: '需渲染',
  T3: '链接形态可疑',
  T4: '长眠源',
  T5: '不可采',
}

type SortKey = 'consecutive_failures' | 'days_since_output' | 'records' | 'dry_streak' | 'source_id'

export function SourcesPage() {
  const [q, setQ] = useState('')
  const [onlyError, setOnlyError] = useState(true)
  const [sort, setSort] = useState<SortKey>('consecutive_failures')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['ops', 'sources'],
    queryFn: api.ops.sources,
    refetchInterval: 30_000,
  })

  const { data: doctor } = useQuery({
    queryKey: ['ops', 'doctor'],
    queryFn: api.ops.doctor,
    refetchInterval: 60_000,
  })

  const { data: lighthouse } = useQuery({
    queryKey: ['ops', 'lighthouse'],
    queryFn: api.ops.lighthouse,
    refetchInterval: 30_000,
  })

  const all = data?.sources ?? []

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let out = all.filter((s) => (onlyError ? s.status !== 'ok' : true))
    if (needle) {
      out = out.filter(
        (s) =>
          s.source_id.toLowerCase().includes(needle) ||
          (s.tier_reason ?? '').toLowerCase().includes(needle) ||
          (s.collect_tier ?? '').toLowerCase().includes(needle),
      )
    }
    const dir = sort === 'source_id' ? 1 : -1
    return [...out].sort((a, b) => {
      const av = a[sort]
      const bv = b[sort]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      return av > bv ? dir : av < bv ? -dir : 0
    })
  }, [all, q, onlyError, sort])

  const sections: NavSection[] = [
    { id: 'overview', label: '总览' },
    { id: 'stall', label: '停摆诊断', badge: doctor?.collective?.collectable_now === 0 ? '停摆' : undefined },
    { id: 'cadence', label: '采集节奏' },
    { id: 'policy', label: '策略分层' },
    { id: 'rounds', label: '灯塔轮次' },
    { id: 'sources', label: '源清单', badge: rows.length },
  ]

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="card p-5 h-24 animate-pulse-soft bg-zinc-100/60" />
        ))}
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="space-y-6">
        <PageHeader />
        <Empty
          title="源状态加载失败"
          hint="后端服务可能未启动，请检查 api 服务"
          action={
            <button onClick={() => refetch()} className="text-xs text-accent hover:underline">
              重试
            </button>
          }
        />
      </div>
    )
  }

  const { totals, tiers, buckets, collect } = data
  const healthyHighFreq = tiers.filter((t) => t.cadence_tier !== 'daily').reduce((a, t) => a + t.ok, 0)
  const errorTiers = tiers.filter((t) => t.error > 0)

  return (
    <div className="space-y-6">
      <PageHeader />
      <SectionNav sections={sections} />

      <Section id="overview" title="总览">
        {/* 台账自身的时效。先说这个，否则下面所有数字都会被当成现状：
            source_sync_state 曾停止写入 8 天，而采集一直正常跑，
            页面却把 8 天前的快照显示成「55 个源正在失败」。 */}
        {totals.state_stale && (
          <div className="mb-4 card p-4 border-danger/40 bg-danger/5 flex items-start gap-3">
            <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" strokeWidth={1.5} />
            <p className="text-sm text-ink-700 leading-relaxed">
              <span className="font-semibold text-danger">源台账数据已过期</span>
              ：source_sync_state 最后一次写入是{' '}
              <span className="font-mono">{formatDate(totals.last_write_at)}</span>
              （{formatAge(totals.state_age_seconds)}前）。下面的失败数是
              <span className="font-semibold text-ink-900">历史快照</span>
              ，不代表此刻状态 —— 采集本身可能一直在正常跑，请以「灯塔轮次」和「停摆诊断」两段为准。
            </p>
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          <StatCard label="采集源总数" value={totals.total} hint={`健康 ${totals.ok}`} />
          <StatCard
            label="异常源"
            value={totals.error}
            tone={totals.error > 0 ? 'warn' : 'ok'}
            hint={`${Math.round((totals.error / Math.max(totals.total, 1)) * 100)}% 占比`}
          />
          <StatCard
            label="已过采集时点"
            value={totals.overdue}
            tone={totals.overdue > 0 ? 'warn' : 'default'}
            hint="该跑没跑"
          />
          <StatCard
            label="长期失败"
            value={totals.failed_20plus}
            tone={totals.failed_20plus > 0 ? 'warn' : 'default'}
            hint="连续 ≥20 次"
          />
          <StatCard
            label="从未成功"
            value={totals.never_succeeded}
            tone={totals.never_succeeded > 0 ? 'warn' : 'default'}
            hint="采集以来零成功"
          />
          <StatCard
            label="策略档位"
            value={collect.total ?? 0}
            hint="source_tiers 覆盖"
          />
        </div>

        {errorTiers.length > 0 && healthyHighFreq > 0 && (
          <div className="mt-4 card p-4 flex items-start gap-3 border-warn/30 bg-warn/5">
            <AlertTriangle className="w-4 h-4 text-warn shrink-0 mt-0.5" strokeWidth={1.5} />
            <p className="text-sm text-ink-700 leading-relaxed">
              <span className="font-semibold">
                {errorTiers.map((t) => CADENCE_LABEL[t.cadence_tier] ?? t.cadence_tier).join('、')}
              </span>{' '}
              档共 {errorTiers.reduce((a, t) => a + t.error, 0)} 个源异常，其余 {healthyHighFreq}{' '}
              个高频档源零失败 —— 问题集中在单一节奏档，不是全局性故障。
            </p>
          </div>
        )}
      </Section>

      <StallPanel doctor={doctor} />

      <Section id="cadence" title="节奏与失败分布">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <ChartCard title="按回访节奏" total={totals.total} hint="扇区名后缀是该档异常源数">
            <Donut
              height={210}
              data={tiers.map((t) => ({
                name:
                  t.error > 0
                    ? `${CADENCE_LABEL[t.cadence_tier] ?? t.cadence_tier}（错 ${t.error}）`
                    : (CADENCE_LABEL[t.cadence_tier] ?? t.cadence_tier),
                value: t.ok + t.error,
              }))}
            />
          </ChartCard>
          <ChartCard title="按连续失败次数" total={totals.total}>
            <BarH
              height={210}
              data={buckets.map((b) => ({
                name: `连续 ${b.label} 次`,
                value: b.count,
                color: b.label === '0' ? TONE.ok : b.label === '20+' ? TONE.warn : undefined,
              }))}
            />
          </ChartCard>
          <ChartCard title="按空轮连续次数" total={collect.total}>
            <BarH
              height={210}
              data={(collect.dry_streak ?? []).map((d) => ({
                name: `${d.streak} 轮无新增`,
                value: d.count,
                color: d.streak >= 4 ? TONE.warn : undefined,
              }))}
            />
          </ChartCard>
        </div>
      </Section>

      <Section id="policy" title="采集策略分层" extra={<p className="text-xs text-ink-400">T0–T5 六档自动路由 · 更新 {formatDate(collect.updated_at)}</p>}>
        <ChartCard title="档位分布" total={collect.total}>
          <BarH
            height={200}
            data={(collect.distribution ?? []).map((d) => ({
              name: `${d.tier} ${CADENCE_HINT[d.tier] ?? ''}`.trim(),
              value: d.count,
            }))}
          />
        </ChartCard>
        <div className="card mt-4 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-zinc-200/60 text-left">
                  <Th>档</Th>
                  <Th>定位</Th>
                  <Th>回访间隔</Th>
                  <Th>深度</Th>
                  <Th>条数上限</Th>
                  <Th>路由</Th>
                </tr>
              </thead>
              <tbody>
                {(collect.policy ?? []).map((p) => (
                  <tr key={p.tier} className="border-b border-zinc-100 last:border-0">
                    <Td mono>{p.tier}</Td>
                    <Td muted>{p.label}</Td>
                    <Td mono>
                      {p.revisit_seconds ? formatInterval(p.revisit_seconds) : '—'}
                    </Td>
                    <Td mono>{p.depth ?? '—'}</Td>
                    <Td mono>{p.max_items ?? '—'}</Td>
                    <Td muted>
                      {(p.routes ?? []).length ? p.routes.join(' / ') : '仅探活'}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <LighthouseSection status={lighthouse} />

      <Section
        id="sources"
        title="源清单"
        extra={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400" strokeWidth={1.5} />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜源标识 / 判定理由"
                className="w-48 h-8 pl-8 pr-3 rounded-md border border-zinc-200 bg-surface text-xs text-ink-900 placeholder:text-ink-400 focus:border-accent focus:outline-none"
              />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-ink-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={onlyError}
                onChange={(e) => setOnlyError(e.target.checked)}
                className="accent-accent"
              />
              只看异常
            </label>
          </div>
        }
      >
        <div className="mb-2 flex items-center gap-1.5 text-xs text-ink-400">
          <ArrowUpDown className="w-3 h-3" strokeWidth={1.5} />
          排序
          {(
            [
              ['consecutive_failures', '连续失败'],
              ['days_since_output', '距上次产出'],
              ['records', '库内条数'],
              ['dry_streak', '空轮次数'],
              ['source_id', '源标识'],
            ] as Array<[SortKey, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setSort(key)}
              className={`px-2 py-1 rounded transition-colors ${
                sort === key ? 'bg-accent-soft text-accent' : 'hover:bg-zinc-100'
              }`}
            >
              {label}
            </button>
          ))}
          <span className="ml-auto font-mono">{rows.length} / {all.length}</span>
        </div>

        {rows.length === 0 ? (
          <Empty title="没有匹配的源" hint={onlyError ? '取消"只看异常"查看全部源' : '换个关键词试试'} />
        ) : (
          <div className="card overflow-hidden">
            <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-surface">
                  <tr className="border-b border-zinc-200/60 text-left">
                    <Th>源标识</Th>
                    <Th>档</Th>
                    <Th>节奏</Th>
                    <Th>失败</Th>
                    <Th>库内</Th>
                    <Th>距产出</Th>
                    <Th>最后成功</Th>
                    <Th>判定</Th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr
                      key={s.source_id}
                      className={`border-b border-zinc-100 last:border-0 hover:bg-zinc-50/60 ${
                        s.status !== 'ok' && s.consecutive_failures >= 20 ? 'bg-warn/5' : ''
                      }`}
                    >
                      <Td mono>
                        {s.source_id}
                        {s.overdue && <span className="ml-1.5 text-[10px] text-warn">过期</span>}
                      </Td>
                      <Td mono>{s.collect_tier ?? '—'}</Td>
                      <Td muted>{CADENCE_LABEL[s.cadence_tier] ?? s.cadence_tier}</Td>
                      <Td>
                        <span
                          className={`font-mono font-semibold ${
                            s.status !== 'ok' && s.consecutive_failures >= 20
                              ? 'text-warn'
                              : 'text-ink-700'
                          }`}
                        >
                          {s.consecutive_failures}
                        </span>
                      </Td>
                      <Td mono>{s.records ?? '—'}</Td>
                      <Td mono>{s.days_since_output ?? '—'}</Td>
                      <Td muted>
                        {s.last_success_at ? (
                          <span className="inline-flex items-center gap-1">
                            <Clock className="w-3 h-3" strokeWidth={1.5} />
                            {formatDate(s.last_success_at)}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-warn">
                            <CircleSlash className="w-3 h-3" strokeWidth={1.5} />
                            从未
                          </span>
                        )}
                      </Td>
                      <Td>
                        <span className="block max-w-[22rem] text-xs text-ink-600 leading-relaxed">
                          {s.tier_reason ?? s.last_error ?? '—'}
                        </span>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Section>
    </div>
  )
}

function StallPanel({ doctor }: { doctor?: import('@/shared/types').SourceDoctor }) {
  if (!doctor || !doctor.available) {
    return null
  }
  const col = doctor.collective ?? {}
  const stalled = col.collectable_now === 0

  return (
    <Section id="stall" title="停摆诊断">
      <div
        className={`card p-5 border ${stalled ? 'border-danger/40 bg-danger/5' : 'border-ok/30 bg-ok/5'}`}
      >
        <div className="flex items-start gap-3 mb-3">
          <Stethoscope
            className={`w-5 h-5 shrink-0 mt-0.5 ${stalled ? 'text-danger' : 'text-ok'}`}
            strokeWidth={1.5}
          />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink-900">{col.verdict ?? '—'}</p>
            <p className="text-xs text-ink-400 mt-0.5">
              体检 {formatDate(doctor.updated_at)} · 共 {doctor.total ?? 0} 源 · 需处置{' '}
              {doctor.by_status?.warn ?? 0} · 免处置 {doctor.by_status?.ok ?? 0}
            </p>
          </div>
        </div>
        {col.hint && (
          <p className="text-sm text-ink-700 leading-relaxed border-l-2 border-danger/40 pl-3">
            {col.hint}
          </p>
        )}
        {(doctor.flagged ?? []).length > 0 && (
          <details className="mt-3 group">
            <summary className="text-xs text-accent cursor-pointer select-none">
              查看 {doctor.flagged!.length} 个待处置源的处置建议
            </summary>
            <div className="mt-2 space-y-2 max-h-64 overflow-y-auto">
              {doctor.flagged!.slice(0, 50).map((f) => (
                <div key={f.source_id} className="text-xs border-l-2 border-zinc-200 pl-3 py-0.5">
                  <p className="font-mono text-ink-700">{f.source_id}</p>
                  <p className="text-ink-600">{f.reason}</p>
                  {f.hint && <p className="text-ink-400 mt-0.5">{f.hint}</p>}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </Section>
  )
}

function LighthouseSection({ status }: { status?: import('@/shared/types').LighthouseStatus }) {
  return (
    <Section id="rounds" title="灯塔轮次">
      {!status || !status.available ? (
        <div className="card p-5">
          <p className="text-sm text-ink-600">{status?.reason ?? '没有 lighthouse 状态快照'}</p>
        </div>
      ) : (
        <LighthousePanel status={status} />
      )}
    </Section>
  )
}

function LighthousePanel({ status }: { status: import('@/shared/types').LighthouseStatus }) {
  const rounds = status.round_log ?? []
  const maxGain = Math.max(...rounds.map((r) => r.gained ?? 0), 1)
  const idle = (status.rounds_total ?? 0) - (status.rounds_productive ?? 0)
  const findings = [...new Set(status.self_iteration.flatMap((s) => s.findings ?? []))]

  return (
    <div className="card p-5">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-5">
        <StatCard label="已完成轮次" value={status.rounds_done ?? 0} />
        <StatCard label="有产出轮次" value={`${status.rounds_productive ?? 0}/${status.rounds_total ?? 0}`} tone={idle > 0 ? 'warn' : 'ok'} />
        <StatCard label="累计入库" value={status.total_gained ?? 0} tone="ok" />
        <StatCard label="库内条数" value={status.db_now ?? 0} />
        <StatCard label="进程 PID" value={status.pid ?? '—'} />
      </div>

      <p className="text-xs text-ink-400 mb-2 flex items-center gap-1.5">
        <Activity className="w-3.5 h-3.5" strokeWidth={1.5} />
        各轮新增条数
      </p>
      <div className="flex items-end gap-1.5 h-24 mb-4">
        {rounds.map((r) => (
          <div key={r.round} className="flex-1 flex flex-col items-center gap-1 min-w-0">
            <span className="text-[10px] font-mono text-ink-400">
              {(r.gained ?? 0) > 0 ? r.gained : ''}
            </span>
            <div
              className={`w-full rounded-t ${(r.gained ?? 0) > 0 ? 'bg-accent' : 'bg-zinc-100'}`}
              style={{
                height: `${Math.max(((r.gained ?? 0) / maxGain) * 64, (r.gained ?? 0) > 0 ? 3 : 8)}px`,
              }}
            />
            <span className="text-[10px] font-mono text-ink-300">#{r.round}</span>
          </div>
        ))}
      </div>

      <div className="pt-4 border-t border-zinc-100 space-y-2">
        <p className="text-xs text-ink-600">
          {rounds.length} 轮中 <span className="font-mono font-semibold text-warn">{idle}</span>{' '}
          轮零产出 —— 空转说明采集已饱和或选择器失效。
        </p>
        {findings.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-ink-400">自迭代发现</span>
            {findings.map((f) => (
              <span key={f} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-100 text-ink-600">
                {f}
              </span>
            ))}
          </div>
        )}
        {/* status.json 只在轮次之间写，一轮 30 分钟里它停在开轮时的值 */}
        <p className="text-xs text-ink-400">
          快照 {formatDate(status.snapshot_at)} · 轮次间不刷新
        </p>
        {status.last_error && <p className="text-xs text-danger">最近错误：{status.last_error}</p>}
      </div>
    </div>
  )
}

function PageHeader() {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink-900 tracking-tight flex items-center gap-2">
        <Radio className="w-6 h-6 text-accent" strokeWidth={1.5} />
        采集源健康
      </h1>
      <p className="text-sm text-ink-400 mt-1">
        {[
          '来源台账',
          '停摆诊断',
          '采集节奏',
          '策略分层 T0–T5',
          '灯塔轮次',
          '例外清单',
        ].join(' · ')}
      </p>
    </div>
  )
}

function formatAge(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '未知'
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} 分钟`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时`
  return `${Math.round(seconds / 86400)} 天`
}

function formatInterval(seconds: number): string {
  if (seconds % 604800 === 0) return `${seconds / 604800} 周`
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  return `${Math.round(seconds / 60)} 分`
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2.5 text-xs font-medium text-ink-400 whitespace-nowrap">{children}</th>
}

function Td({ children, muted, mono }: { children: React.ReactNode; muted?: boolean; mono?: boolean }) {
  return (
    <td
      className={`px-3 py-2.5 whitespace-nowrap ${
        mono ? 'font-mono text-xs text-ink-700' : muted ? 'text-xs text-ink-600' : 'text-ink-900'
      }`}
    >
      {children}
    </td>
  )
}