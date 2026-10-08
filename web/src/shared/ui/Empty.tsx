import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

export function Empty({
  title = '暂无数据',
  hint,
  action,
}: {
  title?: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      <div className="w-12 h-12 rounded-lg bg-zinc-100 flex items-center justify-center mb-4">
        <Inbox className="w-6 h-6 text-ink-400" strokeWidth={1.5} />
      </div>
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {hint && <p className="text-xs text-ink-400 mt-1 max-w-xs">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
