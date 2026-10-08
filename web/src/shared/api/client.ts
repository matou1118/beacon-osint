import type {
  ApiEnvelope,
  Announcement,
  AnnouncementList,
  AnnouncementStats,
  Categories,
  FeedbackPayload,
  GraphNodeBrief,
  GraphNodeDetail,
  GraphSummary,
  HeatTopic,
  HeatTopicsData,
  HeatTrendData,
  LighthouseStatus,
  LoginResult,
  Regions,
  SourceHealth,
  SourceDoctor,
  StatsOverview,
  Subscription,
} from '@/shared/types'
import {
  mockAnnouncementList,
  mockAnnouncements,
  mockCategories,
  mockGraphNode,
  mockHeatTopics,
  mockLoginResult,
  mockRegions,
  mockSubscriptions,
} from './mock'

// 后端 API 基础地址。开发时直连 8000（跨域，靠后端 CORS 白名单）；
// 生产构建用 VITE_API_BASE 指向同源或正式域名。
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

// 后端不可用时是否回退 mock 数据。仅在显式开启时生效（VITE_USE_MOCK=1），
// 默认关闭 —— 否则接口一挂就静默显示假数据，比报错更坏：
// 用户分不清哪条是真数据，违反 PRODUCT.md「每个数字可溯源」。
async function withMock<T>(real: () => Promise<T>, mock: () => T): Promise<T> {
  if (import.meta.env.VITE_USE_MOCK !== '1') return real()
  try {
    return await real()
  } catch {
    return mock()
  }
}

export class ApiError extends Error {
  constructor(
    public code: number,
    message: string,
    public status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const TOKEN_KEY = 'beacon_token'
const USER_KEY = 'beacon_user'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string, userId: string) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, userId)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getUserId(): string | null {
  return localStorage.getItem(USER_KEY)
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (options.headers) {
    const incoming = new Headers(options.headers)
    incoming.forEach((v, k) => {
      headers[k] = v
    })
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  } catch (e) {
    throw new ApiError(-1, '网络连接失败，请检查后端服务是否启动', 0)
  }

  if (res.status === 401) {
    clearToken()
    throw new ApiError(401, '登录已过期，请重新登录', 401)
  }

  let body: ApiEnvelope<T>
  try {
    body = (await res.json()) as ApiEnvelope<T>
  } catch {
    throw new ApiError(res.status, `服务异常 (${res.status})`, res.status)
  }

  if (body.code !== 0) {
    throw new ApiError(body.code, body.message ?? '请求失败', res.status)
  }
  return body.data
}

// ── 端点定义 ──────────────────────────────────────────────

function toQuery(params: Record<string, unknown>): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) continue
    if (Array.isArray(v)) {
      for (const item of v) qs.append(k, String(item))
    } else {
      qs.append(k, String(v))
    }
  }
  const suffix = qs.toString()
  return suffix ? `?${suffix}` : ''
}

export const api = {
  // 公告
  announcements: {
    list: (params: Record<string, unknown>) =>
      withMock(
        () => request<AnnouncementList>(`/api/announcements${toQuery(params)}`),
        () => mockAnnouncementList(params),
      ),
    // 当前筛选的全量汇总（图表用）
    stats: (params: Record<string, unknown>) =>
      request<AnnouncementStats>(`/api/announcements/stats${toQuery(params)}`),
    // CSV 下载地址（全量、不分页）—— 用 <a href> 走浏览器下载，后端带 Content-Disposition
    exportUrl: (params: Record<string, unknown>) =>
      `${API_BASE}/api/announcements/export${toQuery(params)}`,
    detail: (id: string) =>
      withMock(
        () => request<Announcement>(`/api/announcements/${id}`),
        () => mockAnnouncements.find((a) => a.id === id) ?? mockAnnouncements[0],
      ),
  },

  // 基础数据
  categories: () => withMock(() => request<Categories>('/api/categories'), () => mockCategories),
  regions: () => withMock(() => request<Regions>('/api/regions'), () => mockRegions),

  // 全库统计（仪表盘用）。后端不可用时抛错，由调用方回退本地推导。
  stats: {
    overview: () => request<StatsOverview>('/api/stats'),
  },

  // 订阅
  subscriptions: {
    list: () => withMock(() => request<Subscription[]>('/api/subscriptions'), () => mockSubscriptions),
    create: (payload: Partial<Subscription>) =>
      withMock(
        () =>
          request<Subscription>('/api/subscriptions', {
            method: 'POST',
            body: JSON.stringify(payload),
          }),
        () => ({ ...mockSubscriptions[0], ...payload, id: Date.now() } as Subscription),
      ),
    update: (id: number, payload: Partial<Subscription>) =>
      withMock(
        () =>
          request<Subscription>(`/api/subscriptions/${id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          }),
        () => ({ ...mockSubscriptions[0], ...payload, id } as Subscription),
      ),
    remove: (id: number) =>
      withMock(
        () => request<{ deleted: boolean }>(`/api/subscriptions/${id}`, { method: 'DELETE' }),
        () => ({ deleted: true }),
      ),
    push: (id: number, payload: { push_frequency: string; enabled: boolean }) =>
      withMock(
        () =>
          request<Subscription>(`/api/subscriptions/${id}/push`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          }),
        () => ({ ...mockSubscriptions[0], ...payload, id } as Subscription),
      ),
  },

  // 反馈
  feedback: (payload: FeedbackPayload) =>
    withMock(
      () =>
        request<unknown>('/api/feedback', {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
      () => ({ ok: true }),
    ),

  // 热频
  heat: {
    /** 有话题的报告日期（倒序）—— 日期切换只在这些里选 */
    dates: () => request<{ dates: string[] }>('/api/heat/dates'),
    topics: (date?: string, limit = 50) => {
      const qs = new URLSearchParams()
      if (date) qs.set('date', date)
      qs.set('limit', String(limit))
      return withMock(
        () => request<HeatTopicsData>(`/api/heat/topics?${qs}`),
        () => mockHeatTopics,
      )
    },
    detail: (clusterId: string) =>
      withMock(
        () => request<HeatTopic>(`/api/heat/topics/${encodeURIComponent(clusterId)}`),
        () => mockHeatTopics.topics[0],
      ),
    trend: () => request<HeatTrendData>('/api/heat/trend'),
  },

  // 图谱
  graph: {
    summary: () => request<GraphSummary>('/api/graph/summary'),
    /** 按名称搜节点 —— 图谱页唯一入口（后端没有全量节点列表） */
    search: (keyword: string, limit = 20) =>
      withMock(
        () =>
          request<{ nodes: GraphNodeBrief[] }>(
            `/api/graph/nodes?keyword=${encodeURIComponent(keyword)}&limit=${limit}`,
          ),
        () => ({ nodes: [] }),
      ),
    node: (nodeId: string) =>
      withMock(
        () => request<GraphNodeDetail>(`/api/graph/nodes/${encodeURIComponent(nodeId)}`),
        () => mockGraphNode(nodeId),
      ),
  },

  // 采集运维
  ops: {
    sources: () => request<SourceHealth>('/api/ops/sources'),
    lighthouse: () => request<LighthouseStatus>('/api/ops/lighthouse'),
    doctor: () => request<SourceDoctor>('/api/ops/doctor'),
  },

  // 鉴权
  auth: {
    sendCode: (phone: string) =>
      withMock(
        () =>
          request<{ sent: boolean }>('/api/auth/send-code', {
            method: 'POST',
            body: JSON.stringify({ phone }),
          }),
        () => ({ sent: true }),
      ),
    login: (phone: string, code: string) =>
      withMock(
        () =>
          request<LoginResult>('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify({ phone, code }),
          }),
        () => mockLoginResult,
      ),
  },

  // 健康
  health: () => request<{ status: string; service: string }>('/health'),
}
