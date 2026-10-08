import { clsx, type ClassValue } from 'clsx'

export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs)
}

// 日期格式化 —— ISO → 中文短格式
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const now = new Date()
  const diff = (now.getTime() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 604800) return `${Math.floor(diff / 86400)} 天前`
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 行政层级标签
export function levelLabel(level: string | null | undefined): string {
  if (!level) return ''
  const map: Record<string, string> = {
    national: '国家部委',
    provincial: '省级',
    municipal: '市级',
    county: '县级',
  }
  return map[level] ?? level
}

// 验证状态徽章
export function corroborationLabel(status: string): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  switch (status) {
    case 'corroborated':
      return { text: '多源印证', tone: 'ok' }
    case 'single_source':
      return { text: '单源', tone: 'muted' }
    case 'conflict':
      return { text: '源冲突', tone: 'warn' }
    default:
      return { text: status, tone: 'muted' }
  }
}
