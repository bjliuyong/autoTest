import { useState, type FormEvent } from 'react'
import { CheckCircle2, Globe, Lock, Server, ShieldCheck, User, XCircle } from 'lucide-react'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/app/components/ui/card'
import { Input } from '@/app/components/ui/input'
import { Label } from '@/app/components/ui/label'
import { conveyor } from '@/conveyor/client'
import type { AuthSession } from '@/lib/autotest/types'

interface LoginPageProps {
  initialServerUrl: string
  onLoggedIn: (session: AuthSession) => void
}

export function LoginPage({ initialServerUrl, onLoggedIn }: LoginPageProps) {
  const [serverUrl, setServerUrl] = useState(initialServerUrl || 'http://127.0.0.1:3100')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123')
  const [loading, setLoading] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [healthInfo, setHealthInfo] = useState<{ ok: boolean; text: string } | null>(null)

  const handleCheckServer = async () => {
    setChecking(true)
    setHealthInfo(null)
    try {
      const res = await conveyor.autotest.checkServerHealth(serverUrl)
      if (res.ok) {
        setHealthInfo({
          ok: true,
          text: `服务器在线（存储=${res.dbDriver ?? 'shared'}，当前 ${res.onlineCount ?? 0} 人在线）`,
        })
      } else {
        setHealthInfo({ ok: false, text: `连接失败：${res.error || '无法访问'}` })
      }
    } finally {
      setChecking(false)
    }
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const session = await conveyor.autotest.login({
        serverUrl: serverUrl.trim(),
        username: username.trim(),
        password,
      })
      onLoggedIn(session)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请检查服务器地址与账号密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md border-border/80 shadow-lg">
        <CardHeader className="space-y-3 text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-brand text-white shadow-sm">
            <ShieldCheck className="size-6" />
          </div>
          <div>
            <CardTitle className="text-xl font-semibold tracking-tight">Gate AutoTest Studio</CardTitle>
            <CardDescription className="mt-1 text-xs">
              闸机自动化测试协作平台 · 多人连线共享数据与 RBAC 权限管理
            </CardDescription>
          </div>
        </CardHeader>

        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5 text-xs">
                <Server className="size-3.5 text-muted-foreground" />
                共享后端服务器地址
              </Label>
              <div className="flex gap-2">
                <Input
                  value={serverUrl}
                  onChange={(e) => setServerUrl(e.target.value)}
                  placeholder="http://127.0.0.1:3100 或局域网 IP"
                  className="font-mono text-xs"
                  required
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleCheckServer}
                  disabled={checking}
                  className="shrink-0 gap-1 text-xs"
                >
                  <Globe className="size-3.5" />
                  {checking ? '检测中' : '测试连接'}
                </Button>
              </div>
              {healthInfo && (
                <div
                  className={`flex items-center gap-1.5 text-[11px] ${
                    healthInfo.ok ? 'text-emerald-500' : 'text-rose-500'
                  }`}
                >
                  {healthInfo.ok ? <CheckCircle2 className="size-3.5" /> : <XCircle className="size-3.5" />}
                  <span>{healthInfo.text}</span>
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5 text-xs">
                <User className="size-3.5 text-muted-foreground" />
                用户名
              </Label>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                className="text-xs"
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5 text-xs">
                <Lock className="size-3.5 text-muted-foreground" />
                密码
              </Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                className="text-xs"
                required
              />
            </div>

            {error && (
              <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-500">
                {error}
              </div>
            )}

            <Button
              type="submit"
              disabled={loading}
              className="w-full bg-brand text-xs font-medium text-white hover:bg-brand-hover"
            >
              {loading ? '正在连接并登录…' : '登录共享测试平台'}
            </Button>

            <div className="rounded-lg border border-border/70 bg-secondary/40 p-3 text-[11px] text-muted-foreground">
              <div className="font-medium text-foreground/80">默认测试账号</div>
              <div className="mt-1 flex justify-between font-mono">
                <span>管理员：admin / admin123</span>
                <button
                  type="button"
                  onClick={() => {
                    setUsername('admin')
                    setPassword('admin123')
                  }}
                  className="text-brand hover:underline"
                >
                  填入
                </button>
              </div>
              <div className="mt-0.5 flex justify-between font-mono">
                <span>测试员：tester / tester123</span>
                <button
                  type="button"
                  onClick={() => {
                    setUsername('tester')
                    setPassword('tester123')
                  }}
                  className="text-brand hover:underline"
                >
                  填入
                </button>
              </div>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
