import { useEffect, useState, type ReactNode } from 'react'

export interface Section {
  id: string
  label: string
  badge?: number | string
}

// 长页面的吸顶段内导航。页面内容一多，"信息丰富"就等于"很长"，
// 没有锚点就只能在滚动里找。
export function SectionNav({ sections }: { sections: Section[] }) {
  const [active, setActive] = useState(sections[0]?.id ?? '')

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const hit = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (hit) setActive(hit.target.id)
      },
      // 顶部吸顶栏高 56px；提前 120px 触发，切换才不会滞后
      { rootMargin: '-120px 0px -70% 0px', threshold: 0 },
    )
    for (const s of sections) {
      const el = document.getElementById(s.id)
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [sections])

  return (
    <nav className="sticky top-14 z-20 -mx-4 px-4 py-2 bg-base/90 backdrop-blur-sm border-b border-zinc-200/60 mb-4">
      <div className="flex items-center gap-1 overflow-x-auto">
        {sections.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className={`shrink-0 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
              active === s.id
                ? 'bg-accent-soft text-accent'
                : 'text-ink-600 hover:bg-zinc-100 hover:text-ink-900'
            }`}
          >
            {s.label}
            {s.badge !== undefined && (
              <span className="ml-1.5 font-mono text-ink-400">{s.badge}</span>
            )}
          </a>
        ))}
      </div>
    </nav>
  )
}

export function Section({ id, title, extra, children }: { id: string; title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-32">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  )
}