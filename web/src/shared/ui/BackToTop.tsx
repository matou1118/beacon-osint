import { useEffect, useState } from 'react'
import { ArrowUp } from 'lucide-react'

// 全站回顶按钮。挂在 AppShell 里，所有页面自动获得 —— 页面一长就需要它。
export function BackToTop({ threshold = 320 }: { threshold?: number }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > threshold)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [threshold])

  if (!visible) return null

  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      aria-label="回到顶部"
      className="fixed z-30 right-4 lg:right-6 bottom-20 lg:bottom-6 w-10 h-10 rounded-full bg-surface border border-zinc-200 shadow-pop text-ink-600 hover:text-accent hover:border-accent/40 transition-all"
    >
      <ArrowUp className="w-4 h-4 mx-auto" strokeWidth={1.75} />
    </button>
  )
}