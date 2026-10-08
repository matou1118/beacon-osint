import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/shared/utils/format'

type Variant = 'primary' | 'ghost' | 'outline' | 'subtle'
type Size = 'sm' | 'md' | 'lg'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
}

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover active:scale-[0.98]',
  ghost: 'text-ink-700 hover:bg-zinc-100 active:scale-[0.98]',
  outline: 'border border-zinc-200 text-ink-700 hover:bg-zinc-50 active:scale-[0.98]',
  subtle: 'bg-accent-soft text-accent hover:bg-accent/15 active:scale-[0.98]',
}

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
  lg: 'h-12 px-6 text-base gap-2',
}

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = 'primary', size = 'md', loading, className, children, disabled, ...rest }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center rounded-md font-medium',
        'transition-all duration-150 ease-spring',
        'disabled:opacity-50 disabled:pointer-events-none',
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  ),
)
Button.displayName = 'Button'
