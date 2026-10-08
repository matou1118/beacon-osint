import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate, Link } from 'react-router-dom'
import { Compass, Phone, KeyRound, ArrowRight, AlertCircle, Info } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { Button } from '@/shared/ui/Button'
import { useAuth } from '@/shared/store/auth'

export function LoginPage() {
  const navigate = useNavigate()
  const login = useAuth((s) => s.login)
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'phone' | 'code'>('phone')
  const [error, setError] = useState<string | null>(null)

  const sendCodeMut = useMutation({
    mutationFn: (p: string) => api.auth.sendCode(p),
    onSuccess: () => {
      setStep('code')
      setError(null)
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : '发送失败'),
  })

  const loginMut = useMutation({
    mutationFn: () => api.auth.login(phone, code),
    onSuccess: (res) => {
      login(res.token, res.user_id)
      navigate('/subscriptions')
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : '登录失败'),
  })

  return (
    <div className="max-w-md mx-auto py-8">
      <div className="text-center mb-8">
        <div className="w-12 h-12 rounded-xl bg-accent flex items-center justify-center mx-auto mb-4">
          <Compass className="w-7 h-7 text-white" strokeWidth={1.75} />
        </div>
        <h1 className="text-xl font-semibold text-ink-900">登录灯塔 OSINT</h1>
        <p className="text-sm text-ink-400 mt-1">订阅个性化信息推送</p>
      </div>

      <div className="card p-6 space-y-4">
        {/* 手机号 */}
        <div>
          <label className="text-xs font-medium text-ink-700 mb-1.5 block">手机号</label>
          <div className="relative">
            <Phone
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-400"
              strokeWidth={1.5}
            />
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="请输入手机号"
              disabled={step === 'code'}
              className="w-full h-11 pl-10 pr-4 rounded-md border border-zinc-200 bg-surface text-sm placeholder:text-ink-400 focus:border-accent/40 focus:ring-2 focus:ring-accent/10 disabled:bg-zinc-50"
            />
          </div>
        </div>

        {/* 验证码 */}
        {step === 'code' && (
          <div>
            <label className="text-xs font-medium text-ink-700 mb-1.5 block">验证码</label>
            <div className="relative">
              <KeyRound
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-400"
                strokeWidth={1.5}
              />
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="请输入验证码"
                maxLength={6}
                className="w-full h-11 pl-10 pr-4 rounded-md border border-zinc-200 bg-surface text-sm placeholder:text-ink-400 focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
              />
            </div>
          </div>
        )}

        {error && (
          <p className="text-xs text-danger flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5" strokeWidth={1.5} />
            {error}
          </p>
        )}

        {step === 'phone' ? (
          <Button
            className="w-full"
            size="lg"
            loading={sendCodeMut.isPending}
            onClick={() => sendCodeMut.mutate(phone)}
            disabled={phone.length < 6}
          >
            获取验证码
            <ArrowRight className="w-4 h-4" strokeWidth={1.5} />
          </Button>
        ) : (
          <Button
            className="w-full"
            size="lg"
            loading={loginMut.isPending}
            onClick={() => loginMut.mutate()}
            disabled={code.length < 4}
          >
            登录
          </Button>
        )}
      </div>

      {/* 开发期提示 */}
      <div className="mt-4 p-3 rounded-md bg-blue-50 border border-blue-100 flex gap-2">
        <Info className="w-4 h-4 text-info shrink-0 mt-0.5" strokeWidth={1.5} />
        <p className="text-xs text-info leading-relaxed">
          当前 SMS 服务默认未启用。开发环境可通过后端 <code className="font-mono">X-User-Id</code> 头直接访问订阅接口。
        </p>
      </div>

      <p className="text-center text-xs text-ink-400 mt-6">
        <Link to="/" className="hover:text-accent">
          ← 返回首页
        </Link>
      </p>
    </div>
  )
}
