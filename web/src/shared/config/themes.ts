// 6 套拼色主题 —— 与后端 theme_config.py 同一套身份色
// （side/top 取后端 --side-bg/--top-bg 原值；palette 取后端 accent 系列）。
// accent 是浅色内容区的交互/文字色：取顶栏同色系加深一档，
// 保证白底小字对比度（#ea580c/#65a30d 这类 600 级在白底不够）。

export type ThemeId =
  | 'indigo_violet'
  | 'teal_coral'
  | 'moss_lime'
  | 'plum_rose'
  | 'slate_amber'
  | 'navy_sky'

export interface Theme {
  id: ThemeId
  label: string
  desc: string
  /** 内容区交互/文字强调色（白底可读） */
  accent: string
  accentHover: string
  /** 顶栏拼色块 —— 后端 --top-bg */
  top: string
  /** 侧栏/抽屉拼色块 —— 后端 --side-bg */
  side: string
  /** 图表系列色 */
  palette: string[]
}

export const THEMES: Theme[] = [
  {
    id: 'indigo_violet',
    label: '蓝紫拼色',
    desc: '靛蓝侧栏 + 紫罗兰顶栏，电光青强调',
    accent: '#7c3aed',
    accentHover: '#6d28d9',
    top: '#7c3aed',
    side: '#312e81',
    palette: ['#7c3aed', '#22d3ee', '#f472b6', '#a78bfa', '#0ea5c4'],
  },
  {
    id: 'teal_coral',
    label: '青碧珊瑚',
    desc: '深青侧栏 + 珊瑚橙顶栏，清爽有活力',
    accent: '#c2410c',
    accentHover: '#9a3412',
    top: '#ea580c',
    side: '#115e59',
    palette: ['#ea580c', '#2dd4bf', '#fbbf24', '#fb923c', '#14b8a6'],
  },
  {
    id: 'moss_lime',
    label: '苔绿青柠',
    desc: '苔绿侧栏 + 青柠顶栏，自然冷静',
    accent: '#4d7c0f',
    accentHover: '#3f6212',
    top: '#65a30d',
    side: '#166534',
    palette: ['#65a30d', '#4ade80', '#a3e635', '#15803d', '#84cc16'],
  },
  {
    id: 'plum_rose',
    label: '紫檀玫红',
    desc: '紫檀侧栏 + 玫红顶栏，沉稳带暖意',
    accent: '#be123c',
    accentHover: '#9f1239',
    top: '#e11d48',
    side: '#6b21a8',
    palette: ['#e11d48', '#c084fc', '#f9a8d4', '#a855f7', '#fb7185'],
  },
  {
    id: 'slate_amber',
    label: '石墨鎏金',
    desc: '石墨侧栏 + 琥珀顶栏，经典指挥台',
    accent: '#b45309',
    accentHover: '#92400e',
    top: '#d97706',
    side: '#334155',
    palette: ['#d97706', '#fbbf24', '#fde68a', '#94a3b8', '#b45309'],
  },
  {
    id: 'navy_sky',
    label: '深海天青',
    desc: '海军蓝侧栏 + 天青顶栏，深海工位',
    accent: '#0369a1',
    accentHover: '#075985',
    top: '#0284c7',
    side: '#1e40af',
    palette: ['#0284c7', '#38bdf8', '#7dd3fc', '#60a5fa', '#0369a1'],
  },
]

export const DEFAULT_THEME: ThemeId = 'indigo_violet'

const THEME_MAP = new Map(THEMES.map((t) => [t.id, t]))

export function getTheme(id: string | null | undefined): Theme {
  return THEME_MAP.get(id as ThemeId) ?? THEME_MAP.get(DEFAULT_THEME)!
}
