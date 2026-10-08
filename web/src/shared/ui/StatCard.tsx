export function StatCard({
  label,
  value,
  tone = 'default',
  hint,
}: {
  label: string
  value: number | string
  tone?: 'default' | 'warn' | 'ok'
  hint?: string
}) {
  const toneClass =
    tone === 'warn' ? 'text-warn' : tone === 'ok' ? 'text-ok' : 'text-ink-900'
  return (
    <div className="card p-5">
      <p className="text-xs text-ink-400 mb-1">{label}</p>
      <p className={`text-2xl font-semibold font-mono ${toneClass}`}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      {hint && <p className="text-xs text-ink-400 mt-1">{hint}</p>}
    </div>
  )
}
