import type { ReactNode } from 'react'
import { cn } from '@/shared/utils/format'

type Tone = 'default' | 'accent' | 'ok' | 'warn' | 'danger' | 'info' | 'muted'

const tones: Record<Tone, string> = {
  default: 'bg-zinc-100 text-ink-700',
  accent: 'bg-accent-soft text-accent',
  ok: 'bg-emerald-50 text-ok',
  warn: 'bg-amber-50 text-warn',
  danger: 'bg-red-50 text-danger',
  info: 'bg-blue-50 text-info',
  muted: 'bg-zinc-100 text-ink-400',
}

export function Tag({
  tone = 'default',
  children,
  className,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return <span className={cn('chip', tones[tone], className)}>{children}</span>
}
