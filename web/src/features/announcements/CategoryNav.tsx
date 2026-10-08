import { useRef } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { CATEGORY_GROUPS } from '@/shared/config/categories'
import { getCategoryConfig } from '@/shared/config/categories'

interface CategoryNavProps {
  active: string
  onChange: (key: string) => void
  counts: Record<string, number>
}

export function CategoryNav({ active, onChange, counts }: CategoryNavProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  const scroll = (dir: 'left' | 'right') => {
    if (!scrollRef.current) return
    const amount = 200
    scrollRef.current.scrollBy({ left: dir === 'left' ? -amount : amount, behavior: 'smooth' })
  }

  return (
    <div className="relative">
      {/* 左右滚动按钮 */}
      <button
        onClick={() => scroll('left')}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-10 w-7 h-7 rounded-full bg-white border border-zinc-200 shadow-sm flex items-center justify-center text-ink-400 hover:text-ink-700 hover:border-zinc-300 transition-colors"
        aria-label="向左滚动"
      >
        <ChevronLeft className="w-4 h-4" strokeWidth={1.5} />
      </button>
      <button
        onClick={() => scroll('right')}
        className="absolute right-0 top-1/2 -translate-y-1/2 z-10 w-7 h-7 rounded-full bg-white border border-zinc-200 shadow-sm flex items-center justify-center text-ink-400 hover:text-ink-700 hover:border-zinc-300 transition-colors"
        aria-label="向右滚动"
      >
        <ChevronRight className="w-4 h-4" strokeWidth={1.5} />
      </button>

      <div
        ref={scrollRef}
        className="flex items-center gap-2 overflow-x-auto py-1 px-9 scroll-smooth"
        style={{ scrollbarWidth: 'none' }}
      >
        {CATEGORY_GROUPS.map((group) => {
          const isActive = active === group.key
          const count =
            group.key === 'all'
              ? counts._all ?? 0
              : group.categories.reduce((sum, cat) => sum + (counts[cat] ?? 0), 0)

          // 取第一个分类的配置作为颜色代表
          const config = group.categories.length > 0 ? getCategoryConfig(group.categories[0]) : null

          return (
            <button
              key={group.key}
              onClick={() => onChange(group.key)}
              className={`shrink-0 px-3.5 py-2 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-2 ${
                isActive
                  ? config
                    ? `${config.bg} ${config.text} ring-1 ring-current/20`
                    : 'bg-teal-50 text-teal-700 ring-1 ring-teal-200'
                  : 'bg-zinc-50 text-ink-500 hover:bg-zinc-100 hover:text-ink-700'
              }`}
            >
              <span>{group.label}</span>
              {count > 0 && (
                <span
                  className={`text-[11px] tabular-nums px-1.5 py-0.5 rounded-full ${
                    isActive ? 'bg-white/60' : 'bg-white'
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
