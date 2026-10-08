import { NavLink, useLocation } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useState, type ReactNode } from 'react'
import {
  Home,
  Search,
  Flame,
  Network,
  Bell,
  User,
  Menu,
  X,
  Compass,
  Radio,
  Palette,
  Check,
} from 'lucide-react'
import { cn } from '@/shared/utils/format'
import { useAuth } from '@/shared/store/auth'
import { useTheme } from '@/shared/store/theme'
import { THEMES } from '@/shared/config/themes'
import { BackToTop } from '@/shared/ui/BackToTop'

const nav = [
  { to: '/', label: '要情', icon: Home, end: true },
  { to: '/search', label: '检索', icon: Search },
  { to: '/heat', label: '热点', icon: Flame },
  { to: '/graph', label: '图谱', icon: Network },
  { to: '/ops/sources', label: '源健康', icon: Radio },
  { to: '/subscriptions', label: '订阅', icon: Bell },
]

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [themeOpen, setThemeOpen] = useState(false)
  const isAuth = useAuth((s) => s.isAuthenticated)
  const themeId = useTheme((s) => s.id)
  const setTheme = useTheme((s) => s.set)

  return (
    <div className="min-h-[100dvh] flex flex-col bg-base">
      {/* 顶栏 —— 拼色实色块（后端主题的 --top-bg 身份色） */}
      <header className="sticky top-0 z-40 bg-topbar text-white pt-safe shadow-[0_1px_3px_rgba(0,0,0,0.12)]">
        <div className="mx-auto max-w-[1400px] h-14 px-4 flex items-center gap-4">
          {/* 移动端菜单按钮 */}
          <button
            className="lg:hidden tap -ml-2 rounded-md text-white/80 hover:bg-white/15"
            onClick={() => setMobileNavOpen(true)}
            aria-label="打开菜单"
          >
            <Menu className="w-5 h-5" strokeWidth={1.5} />
          </button>

          {/* Logo */}
          <NavLink to="/" className="flex items-center gap-2 shrink-0">
            <div className="w-8 h-8 rounded-lg bg-white/20 flex items-center justify-center">
              <Compass className="w-5 h-5 text-white" strokeWidth={1.75} />
            </div>
            <div className="hidden sm:block">
              <span className="text-sm font-semibold text-white">灯塔 OSINT</span>
              <span className="text-xs text-white/60 ml-1.5">Beacon</span>
            </div>
          </NavLink>

          {/* 桌面导航 */}
          <nav className="hidden lg:flex items-center gap-1 ml-4">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    'px-3 h-9 rounded-md text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-white/20 text-white'
                      : 'text-white/75 hover:text-white hover:bg-white/15',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex-1" />

          {/* 主题切换 —— 与后端一致的 6 套拼色模板 */}
          <div className="relative">
            <button
              className="tap flex items-center justify-center w-9 h-9 rounded-md text-white/85 hover:bg-white/15"
              onClick={() => setThemeOpen((v) => !v)}
              aria-label="切换主题"
              title="切换主题"
            >
              <Palette className="w-4 h-4" strokeWidth={1.5} />
            </button>
            {themeOpen && (
              <>
                <div
                  className="fixed inset-0 z-40"
                  onClick={() => setThemeOpen(false)}
                  aria-hidden
                />
                <div className="absolute right-0 top-full mt-2 w-64 card p-1.5 shadow-pop z-50 animate-fade-in">
                  <div className="px-2 py-1.5 text-xs text-ink-400">主题风格</div>
                  {THEMES.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => {
                        setTheme(t.id)
                        setThemeOpen(false)
                      }}
                      className={cn(
                        'w-full flex items-center gap-2.5 px-2 py-2 rounded-md text-left transition-colors',
                        t.id === themeId ? 'bg-accent-soft' : 'hover:bg-zinc-100',
                      )}
                    >
                      <span className="flex shrink-0">
                        <span className="w-3.5 h-3.5 rounded-sm" style={{ background: t.side }} />
                        <span className="w-3.5 h-3.5 rounded-sm -ml-1" style={{ background: t.top }} />
                        <span className="w-3.5 h-3.5 rounded-sm -ml-1" style={{ background: t.accent }} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-xs font-medium text-ink-900">{t.label}</span>
                        <span className="block text-[10px] text-ink-400 truncate-1">{t.desc}</span>
                      </span>
                      {t.id === themeId && (
                        <Check className="w-3.5 h-3.5 text-accent ml-auto shrink-0" strokeWidth={2} />
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* 用户区 */}
          <NavLink
            to={isAuth ? '/subscriptions' : '/login'}
            className="tap flex items-center gap-2 px-3 h-9 rounded-md text-sm text-white/85 hover:bg-white/15"
          >
            <User className="w-4 h-4" strokeWidth={1.5} />
            <span className="hidden sm:inline">{isAuth ? '我的' : '登录'}</span>
          </NavLink>
        </div>
      </header>

      {/* 移动端抽屉导航 */}
      <AnimatePresence>
        {mobileNavOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 bg-ink-900/30 backdrop-blur-sm lg:hidden"
              onClick={() => setMobileNavOpen(false)}
            />
            <motion.aside
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
              className="fixed left-0 top-0 bottom-0 z-50 w-64 bg-sidebar text-white lg:hidden"
            >
              <div className="h-14 px-4 flex items-center justify-between border-b border-white/10">
                <span className="text-sm font-semibold">导航</span>
                <button
                  className="tap rounded-md text-white/70 hover:bg-white/15"
                  onClick={() => setMobileNavOpen(false)}
                  aria-label="关闭菜单"
                >
                  <X className="w-5 h-5" strokeWidth={1.5} />
                </button>
              </div>
              <nav className="p-2">
                {nav.map((item) => {
                  const Icon = item.icon
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.end}
                      onClick={() => setMobileNavOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          'flex items-center gap-3 px-3 h-11 rounded-md text-sm font-medium transition-colors',
                          isActive
                            ? 'bg-white/15 text-white'
                            : 'text-white/75 hover:bg-white/10 hover:text-white',
                        )
                      }
                    >
                      <Icon className="w-4 h-4" strokeWidth={1.5} />
                      {item.label}
                    </NavLink>
                  )
                })}
              </nav>
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* 主内容 */}
      <main className="flex-1 mx-auto w-full max-w-[1400px] px-4 py-6 pb-24 lg:pb-8">
        <AnimatePresence mode="wait">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            {children}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* 移动端底部 Tab Bar */}
      <nav className="fixed bottom-0 inset-x-0 z-30 bg-surface/90 backdrop-blur-md border-t border-zinc-200/60 lg:hidden pb-safe">
        <div className="flex items-center justify-around h-14">
          {nav.map((item) => {
            const Icon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    'flex flex-col items-center justify-center gap-0.5 tap text-[10px] font-medium transition-colors',
                    isActive ? 'text-accent' : 'text-ink-400',
                  )
                }
              >
                <Icon className="w-5 h-5" strokeWidth={1.5} />
                {item.label}
              </NavLink>
            )
          })}
        </div>
      </nav>

      <BackToTop />
    </div>
  )
}
