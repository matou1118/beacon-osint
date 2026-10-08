// 后端 API 类型定义 —— 与 storage/models.py + api/services 对齐

export interface ApiEnvelope<T> {
  code: number
  data: T
  message?: string
  errors?: unknown
}

export interface Announcement {
  id: string
  title: string
  issuing_body: string
  gov_level: string | null
  region: string | null
  region_province: string | null
  region_city: string | null
  region_county: string | null
  category_l1: string | null
  category_l2: string | null
  target_group: string | null
  target_industry: string | null
  industry_focus?: boolean
  industry_tags?: string[]
  amount: string | null
  deadline: string | null
  key_points: string[]
  source_url: string
  publish_date: string | null
  media_type: string | null
  media_tags: string[]
  media_cluster_id: string | null
  media_heat: number | null
  corroboration_status: string
  corroboration_count: number
  corroboration_reason: string | null
  corroborating_sources: CorroboratingSource[]
  // 详情页额外字段
  raw_text?: string
  content_hash?: string
  needs_review?: boolean
  // 匹配结果（match_service 注入，字段名以后端为准）
  match_score?: number
  match_reasons?: string[]
}

export interface CorroboratingSource {
  source_id?: string
  domain?: string
  url?: string
  source_type?: string
}

export interface AnnouncementList {
  total: number
  page: number
  page_size: number
  items: Announcement[]
}

export interface CountRow {
  name: string
  value: number
}

// GET /api/announcements/stats —— 当前筛选条件下的全量汇总（检索页图表）
export interface AnnouncementStats {
  total: number
  category_l1: CountRow[]
  category_l2: CountRow[]
  gov_level: CountRow[]
  province: CountRow[]
  month: CountRow[]
}

export interface Categories {
  l1: string[]
  elderly_sub: string[]
  admission_sub: string[]
}

export interface Regions {
  provinces: string[]
  cities: Record<string, string[]>
  counties: Record<string, string[]>
}

// GET /api/stats —— 全库聚合（口径与列表一致：排除 needs_review/已归档）
export interface StatsOverview {
  total: number
  corroborated: number
  provinces: number
  categories: number
  cat_data: Array<{ name: string; value: number }>
  ind_data: Array<{ name: string; value: number }>
  prov_ind_data: Array<{ prov: string; ind: string; value: number }>
  prov_data: Array<{ name: string; value: number }>
  year_data: Array<{ year: string; count: number }>
  gov_level: Array<{ name: string; value: number }>
  day_data: Array<{ day: string; count: number }>
}

export interface Subscription {
  id: number
  gov_levels: string[]
  region: string | null
  categories: string[]
  target_group: string | null
  target_industry: string | null
  keywords: string[]
  push_frequency: 'daily' | 'weekly' | 'none'
  enabled: boolean
  created_at: string | null
}

// GET /api/heat/topics —— 与 storage/db.py::_heat_topic 同一套字段
export interface HeatTopic {
  cluster_id: string
  report_date?: string
  title?: string
  category_l1?: string | null
  category_l2?: string | null
  heat_score?: number
  source_count?: number
  source_names?: string[]
  latest_publish?: string | null
  article_urls?: string[]
  terms?: string[]
  heat_reason?: string | null
  summary?: string | null
}

export interface HeatTopicsData {
  date: string
  topics: HeatTopic[]
}

// GET /api/heat/trend —— 按 report_date 聚合（升序），混着月粒度日期是数据事实
export interface HeatTrendPoint {
  date: string
  count: number
  heat: number
}

export interface HeatTrendData {
  trend: HeatTrendPoint[]
}

export interface GraphSummary {
  nodes: number
  edges: number
  evidence: number
  needs_review: number
  node_types: Record<string, number>
  edge_types: Record<string, number>
}

// GET /api/graph/nodes/{id}
export interface GraphNodeBrief {
  id: string
  type?: string
  name?: string
}

export interface GraphNeighbor {
  edge_type: string
  direction: 'outgoing' | 'incoming'
  node: GraphNodeBrief
  needs_review?: boolean
}

export interface GraphNodeDetail {
  node: GraphNodeBrief & { attributes?: Record<string, unknown> }
  neighbors: GraphNeighbor[]
}

export interface FeedbackPayload {
  announcement_id: number
  useful: boolean
  comment?: string
}

export interface SourceHealthTotals {
  total: number
  ok: number
  error: number
  failed_20plus: number
  never_succeeded: number
  overdue: number
  /** source_sync_state 自身最后一次写入的时间 —— 用来判断下面这些数字有多旧 */
  last_write_at?: string | null
  state_age_seconds?: number | null
  /** 写者脱钩（超过一天没写）。此时页面上的失败数全是历史快照，不是现状。 */
  state_stale?: boolean
}

export interface SourceTier {
  cadence_tier: string
  interval_seconds: number
  ok: number
  error: number
}

export interface SourceHealthRow {
  source_id: string
  cadence_tier: string
  status: string
  consecutive_failures: number
  last_attempt_at: string | null
  last_success_at: string | null
  next_due_at: string | null
  last_error: string | null
  overdue: boolean
  collect_tier?: string | null
  tier_reason?: string | null
  records?: number | null
  dry_streak?: number | null
  days_since_output?: number | null
  render_share?: number | null
  dup_body_records?: number | null
  newest_publish?: string | null
}

export interface CollectPolicy {
  available: boolean
  updated_at?: string
  total?: number
  policy: Array<{
    tier: string
    label?: string
    revisit_seconds?: number
    depth?: number
    max_items?: number
    routes: string[]
  }>
  distribution: Array<{ tier: string; count: number }>
  dry_streak: Array<{ streak: number; count: number }>
}

export interface SourceDoctor {
  available: boolean
  reason?: string
  updated_at?: string
  at?: string
  total?: number
  by_status: Record<string, number>
  by_tier: Record<string, number>
  collective: Record<string, unknown> & { verdict?: string; hint?: string; collectable_now?: number }
  flagged: Array<{
    source_id: string
    status: string
    tier?: number
    reason?: string
    hint?: string
    evidence?: Record<string, unknown>
  }>
}

export interface SourceHealth {
  totals: SourceHealthTotals
  tiers: SourceTier[]
  buckets: Array<{ label: string; count: number }>
  sources: SourceHealthRow[]
  collect: CollectPolicy
}

export interface LighthouseRound {
  round: number
  minutes?: number
  exit?: number
  gained?: number
  skipped?: number
  depth?: number
  at?: string
}

export interface LighthouseStatus {
  available: boolean
  reason?: string
  path?: string
  snapshot_at?: string
  pid?: number
  started_at?: string
  deadline?: string
  rounds_planned?: number
  rounds_done?: number
  rounds_total?: number
  rounds_productive?: number
  total_gained?: number
  db_start?: number
  db_now?: number
  last_error?: string
  round_log: LighthouseRound[]
  self_iteration: Array<{
    findings?: string[]
    stages?: string[]
    auto_actions?: string[]
    buckets?: Record<string, number>
    report?: string
  }>
}

export interface LoginResult {
  token: string
  user_id: string
  expires_in: number
}
