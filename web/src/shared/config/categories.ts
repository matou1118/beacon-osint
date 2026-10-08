// 分类体系配置 —— 图标/颜色/描述/分组
// 与数据库实际分类对齐（21 个分类）

export interface CategoryConfig {
  key: string
  label: string
  short: string
  icon: string // lucide icon name
  color: string // hex for charts
  bg: string // tailwind bg class
  text: string // tailwind text class
  desc: string
  group: string
}

export const CATEGORY_CONFIG: CategoryConfig[] = [
  // ── 政策法规组 ──
  { key: '行业政策', label: '行业政策', short: '行业', icon: 'Scale', color: '#0d9488', bg: 'bg-teal-50', text: 'text-teal-700', desc: '行业法规、标准、监管政策', group: 'policy' },
  { key: '政策法规', label: '政策法规', short: '法规', icon: 'ScrollText', color: '#0f766e', bg: 'bg-teal-50', text: 'text-teal-700', desc: '法律法规、行政法规、地方性法规', group: 'policy' },
  { key: '政策发布', label: '政策发布', short: '发布', icon: 'Megaphone', color: '#14b8a6', bg: 'bg-teal-50', text: 'text-teal-700', desc: '政策文件发布、通知印发', group: 'policy' },
  { key: '规划计划', label: '规划计划', short: '规划', icon: 'Map', color: '#5eead4', bg: 'bg-teal-50', text: 'text-teal-700', desc: '发展规划、专项计划、实施方案', group: 'policy' },
  { key: '政策解读', label: '政策解读', short: '解读', icon: 'BookOpen', color: '#2dd4bf', bg: 'bg-teal-50', text: 'text-teal-700', desc: '政策解读、答记者问、图解政策', group: 'policy' },

  // ── 公示公告组 ──
  { key: '公示公告', label: '公示公告', short: '公示', icon: 'ClipboardList', color: '#1d4ed8', bg: 'bg-blue-50', text: 'text-blue-700', desc: '公示、公告、通知公告', group: 'notice' },

  // ── 行政审批组 ──
  { key: '行政审批', label: '行政审批', short: '审批', icon: 'FileCheck', color: '#4338ca', bg: 'bg-indigo-50', text: 'text-indigo-700', desc: '行政许可、资质认定、审批结果', group: 'approve' },

  // ── 招标采购组 ──
  { key: '招标信息', label: '招标信息', short: '招标', icon: 'FileText', color: '#ca8a04', bg: 'bg-yellow-50', text: 'text-yellow-700', desc: '工程招标、政府采购、项目采购', group: 'bid' },

  // ── 招商商机组 ──
  { key: '招商', label: '招商政策', short: '招商', icon: 'Handshake', color: '#ea580c', bg: 'bg-orange-50', text: 'text-orange-700', desc: '园区招商、投资促进、营商环境', group: 'business' },
  { key: '商业机会', label: '商业机会', short: '商机', icon: 'TrendingUp', color: '#dc2626', bg: 'bg-red-50', text: 'text-red-700', desc: '项目合作、专精特新、企业培育', group: 'business' },

  // ── 新闻动态组 ──
  { key: '新闻动态', label: '新闻动态', short: '新闻', icon: 'Newspaper', color: '#64748b', bg: 'bg-slate-50', text: 'text-slate-700', desc: '政务动态、项目进展、民生资讯', group: 'news' },

  // ── 补贴资金组 ──
  { key: '行业补贴', label: '行业补贴', short: '补贴', icon: 'Coins', color: '#0284c7', bg: 'bg-sky-50', text: 'text-sky-700', desc: '财政补贴、专项资金、奖补政策', group: 'subsidy' },

  // ── 招生公招组 ──
  { key: '招生信息', label: '招生信息', short: '招生', icon: 'GraduationCap', color: '#7c3aed', bg: 'bg-violet-50', text: 'text-violet-700', desc: '高职单招、军校、公费定向、职业院校', group: 'admission' },
  { key: '公招信息', label: '公招信息', short: '公招', icon: 'ShieldCheck', color: '#6366f1', bg: 'bg-indigo-50', text: 'text-indigo-700', desc: '公务员招录、公安司法院校', group: 'admission' },
  { key: '公务员考试', label: '公务员考试', short: '公考', icon: 'BadgeCheck', color: '#8b5cf6', bg: 'bg-violet-50', text: 'text-violet-700', desc: '公务员考试公告、职位表', group: 'admission' },
  { key: '军队招录', label: '军队招录', short: '军校', icon: 'Medal', color: '#a78bfa', bg: 'bg-violet-50', text: 'text-violet-700', desc: '军队院校招生、定向培养', group: 'admission' },

  // ── 人才政策组 ──
  { key: '人才政策', label: '人才政策', short: '人才', icon: 'Users', color: '#0891b2', bg: 'bg-cyan-50', text: 'text-cyan-700', desc: '人才引进、培养计划、住房补贴', group: 'talent' },

  // ── 招聘就业组 ──
  { key: '招聘信息', label: '招聘信息', short: '招聘', icon: 'Briefcase', color: '#16a34a', bg: 'bg-green-50', text: 'text-green-700', desc: '事业单位招聘、人才引进', group: 'recruit' },

  // ── 养老社区组 ──
  { key: '养老与社区', label: '养老与社区', short: '养老', icon: 'HeartHandshake', color: '#be185d', bg: 'bg-pink-50', text: 'text-pink-700', desc: '高龄津贴、老年助餐、适老化改造', group: 'elderly' },

  // ── 活动通知组 ──
  { key: '活动通知', label: '活动通知', short: '活动', icon: 'CalendarDays', color: '#65a30d', bg: 'bg-lime-50', text: 'text-lime-700', desc: '培训、展会、申报通知', group: 'activity' },

  // ── 其他 ──
  { key: '非公告体裁', label: '非公告', short: '其他', icon: 'FileQuestion', color: '#a1a1aa', bg: 'bg-zinc-50', text: 'text-zinc-600', desc: '非公告体裁内容', group: 'other' },
  { key: '其他', label: '其他', short: '其他', icon: 'CircleDashed', color: '#a1a1aa', bg: 'bg-zinc-50', text: 'text-zinc-600', desc: '未分类信息', group: 'other' },
]

// 快速查找
export const CATEGORY_MAP = new Map(CATEGORY_CONFIG.map((c) => [c.key, c]))

// 获取分类配置，未命中返回默认
export function getCategoryConfig(key: string | null | undefined): CategoryConfig {
  if (!key) return CATEGORY_CONFIG[CATEGORY_CONFIG.length - 1]
  return CATEGORY_MAP.get(key) ?? CATEGORY_CONFIG[CATEGORY_CONFIG.length - 1]
}

// 分类组（更高层聚合）—— 顺序即展示顺序
export interface CategoryGroup {
  key: string
  label: string
  categories: string[]
}

export const CATEGORY_GROUPS: CategoryGroup[] = [
  { key: 'all', label: '全部', categories: [] },
  { key: 'policy', label: '政策法规', categories: ['行业政策', '政策法规', '政策发布', '规划计划', '政策解读'] },
  { key: 'notice', label: '公示公告', categories: ['公示公告'] },
  { key: 'bid', label: '招标采购', categories: ['招标信息'] },
  { key: 'business', label: '招商商机', categories: ['招商', '商业机会'] },
  { key: 'approve', label: '行政审批', categories: ['行政审批'] },
  { key: 'news', label: '新闻动态', categories: ['新闻动态'] },
  { key: 'subsidy', label: '补贴资金', categories: ['行业补贴'] },
  { key: 'admission', label: '招生公招', categories: ['招生信息', '公招信息', '公务员考试', '军队招录'] },
  { key: 'talent', label: '人才政策', categories: ['人才政策'] },
  { key: 'recruit', label: '招聘就业', categories: ['招聘信息'] },
  { key: 'elderly', label: '养老社区', categories: ['养老与社区'] },
  { key: 'activity', label: '活动通知', categories: ['活动通知'] },
  { key: 'other', label: '其他', categories: ['非公告体裁', '其他'] },
]
