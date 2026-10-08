import { create } from 'zustand'
import { DEFAULT_THEME, getTheme, type Theme, type ThemeId } from '@/shared/config/themes'

const STORAGE_KEY = 'beacon_theme'

function hexToRgbTriplet(hex: string): string {
  const raw = hex.replace('#', '')
  const full = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)).join(' ')
}

// 写 CSS 变量 + data-theme —— tailwind 的 accent/topbar 色都读这些变量，
// 主题切换因此一次性生效到全站（含卡片、标签、图表）。
export function applyTheme(id: ThemeId): void {
  const theme = getTheme(id)
  const root = document.documentElement
  root.dataset.theme = id
  root.style.setProperty('--accent', hexToRgbTriplet(theme.accent))
  root.style.setProperty('--accent-hover', hexToRgbTriplet(theme.accentHover))
  root.style.setProperty('--top', hexToRgbTriplet(theme.top))
  root.style.setProperty('--side', hexToRgbTriplet(theme.side))
}

function initialId(): ThemeId {
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved ? (saved as ThemeId) : DEFAULT_THEME
}

applyTheme(initialId())

interface ThemeState {
  id: ThemeId
  theme: Theme
  set: (id: ThemeId) => void
}

export const useTheme = create<ThemeState>((set) => ({
  id: initialId(),
  theme: getTheme(initialId()),
  set: (id: ThemeId) => {
    localStorage.setItem(STORAGE_KEY, id)
    applyTheme(id)
    set({ id, theme: getTheme(id) })
  },
}))
