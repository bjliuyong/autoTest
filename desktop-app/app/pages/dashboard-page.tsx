import {
  Activity,
  CheckCircle2,
  Database,
  Layers,
  Play,
  Radio,
  Sparkles,
  Square,
  Terminal,
  XCircle,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/app/components/ui/card'
import type { DashboardStats, EngineStatus, SystemLogEntry, TestCaseRecord } from '@/lib/autotest/types'

interface DashboardPageProps {
  status: EngineStatus
  stats: DashboardStats | undefined
  logs: SystemLogEntry[]
  onStart: () => void
  onStop: () => void
  onSelfTest: () => void
  onInspectCase: (record: TestCaseRecord) => void
  busy: boolean
}

function verdictBadge(status: TestCaseRecord['verdictStatus']) {
  if (status === 'PASS') {
    return (
      <Badge className="bg-emerald-500/15 text-emerald-500 hover:bg-emerald-500/20 border-emerald-500/30">
        通过
      </Badge>
    )
  }
  if (status === 'TIMEOUT') {
    return (
      <Badge className="bg-amber-500/15 text-amber-500 hover:bg-amber-500/20 border-amber-500/30">
        超时
      </Badge>
    )
  }
  return (
    <Badge className="bg-rose-500/15 text-rose-500 hover:bg-rose-500/20 border-rose-500/30">
      不通过
    </Badge>
  )
}

function fmtTime(iso: string) {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

export function DashboardPage({
  status,
  stats,
  logs,
  onStart,
  onStop,
  onSelfTest,
  onInspectCase,
  busy,
}: DashboardPageProps) {
  const pieData = [
    { name: '通过', value: stats?.passedCases ?? 0, color: '#10b981' },
    { name: '失败', value: stats?.failedCases ?? 0, color: '#f43f5e' },
    { name: '超时', value: stats?.timeoutCases ?? 0, color: '#f59e0b' },
  ].filter((d) => d.value > 0)

  const nodeData = stats?.failedNodeStats ?? []

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-6">
      {/* Top Engine Control Hero */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-card p-5 shadow-xs">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <span className="relative flex size-2.5">
              {status.running && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              )}
              <span
                className={`relative inline-flex size-2.5 rounded-full ${
                  status.running ? 'bg-emerald-500' : 'bg-muted-foreground/40'
                }`}
              />
            </span>
            <h1 className="text-lg font-semibold tracking-tight">测试监控大盘</h1>
            <Badge variant="outline" className="font-mono text-xs">
              {status.running ? (status.mode === 'selftest' ? '自测模式' : '实时监听中') : '待机'}
            </Badge>
            {status.activeRunCode && (
              <Badge variant="secondary" className="font-mono text-xs">
                {status.activeRunCode}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {status.stage} · {status.stageDetail}
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Button
            variant="outline"
            size="sm"
            onClick={onSelfTest}
            disabled={busy}
            className="gap-1.5 border-brand/30 text-brand hover:bg-brand/10 hover:text-brand"
          >
            <Sparkles className="size-3.5" />
            一键自测模拟
          </Button>
          {status.running ? (
            <Button variant="destructive" size="sm" onClick={onStop} disabled={busy} className="gap-1.5">
              <Square className="size-3.5 fill-current" />
              停止测试
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={onStart}
              disabled={busy}
              className="gap-1.5 bg-brand text-white hover:bg-brand-hover"
            >
              <Play className="size-3.5 fill-current" />
              启动实时测试
            </Button>
          )}
        </div>
      </div>

      {/* Live Channel Status Strip */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="flex items-center justify-between rounded-lg border border-border bg-card/60 px-3.5 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Radio className="size-3.5" /> 闸机通道
          </span>
          <span className={status.gateConnected ? 'font-medium text-emerald-500' : 'text-muted-foreground'}>
            {status.gateConnected ? `在线 (${status.gateClientCount || 1})` : '未连接'}
          </span>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border bg-card/60 px-3.5 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Activity className="size-3.5" /> 日志通道
          </span>
          <span className={status.logConnected ? 'font-medium text-emerald-500' : 'text-muted-foreground'}>
            {status.logConnected ? `已接入 · ${status.logLineCount} 行` : '未连接'}
          </span>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border bg-card/60 px-3.5 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Layers className="size-3.5" /> 活跃会话 / 待判
          </span>
          <span className="font-mono font-medium">
            {status.sessionCount} / {status.pendingCases}
          </span>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border bg-card/60 px-3.5 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Database className="size-3.5" /> 数据存储
          </span>
          <span className="font-medium text-brand">共享后端实时同步</span>
        </div>
      </div>

      {/* 4 KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs">
              <span>累计测试用例</span>
              <Layers className="size-4 text-muted-foreground" />
            </CardDescription>
            <CardTitle className="font-mono text-2xl">{stats?.totalCases ?? 0}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            当前批次已收 {status.receivedCount} 条
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs">
              <span>综合通过率</span>
              <CheckCircle2 className="size-4 text-emerald-500" />
            </CardDescription>
            <CardTitle className="font-mono text-2xl text-emerald-500">
              {stats ? `${stats.passRate.toFixed(1)}%` : '0.0%'}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            累计通过 {stats?.passedCases ?? 0} 条
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs">
              <span>失败 / 超时用例</span>
              <XCircle className="size-4 text-rose-500" />
            </CardDescription>
            <CardTitle className="font-mono text-2xl text-rose-500">
              {stats?.failedCases ?? 0}{' '}
              <span className="text-base font-normal text-amber-500">/ {stats?.timeoutCases ?? 0}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            支持一键追溯日志切片与根因分析
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs">
              <span>历史测试批次</span>
              <Database className="size-4 text-brand" />
            </CardDescription>
            <CardTitle className="font-mono text-2xl">{stats?.totalBatches ?? 0}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            跨平台本地 SQLite 永久存档
          </CardContent>
        </Card>
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">用例判定结果分布</CardTitle>
            <CardDescription className="text-xs">历史全部用例通过 / 失败 / 超时占比</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            {pieData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                暂无测试数据，点击右上角「一键自测模拟」立即体验
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={52}
                    outerRadius={80}
                    paddingAngle={4}
                  >
                    {pieData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--card)',
                      borderColor: 'var(--border)',
                      borderRadius: '8px',
                      fontSize: '12px',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">异常 / 拦截节点分布</CardTitle>
            <CardDescription className="text-xs">按实际失败节点或超时原因统计</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            {nodeData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                暂无失败记录
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={nodeData} layout="vertical" margin={{ left: 16, right: 16, top: 8, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} />
                  <YAxis
                    type="category"
                    dataKey="node"
                    width={92}
                    tick={{ fontSize: 11, fill: 'var(--foreground)' }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--card)',
                      borderColor: 'var(--border)',
                      borderRadius: '8px',
                      fontSize: '12px',
                    }}
                  />
                  <Bar dataKey="count" name="异常次数" fill="#f43f5e" radius={[0, 4, 4, 0]} barSize={18} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Bottom Live Stream Row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="flex flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">最新用例判定流</CardTitle>
            <CardDescription className="text-xs">点击任意记录可直接跳转用例全链路追溯详情</CardDescription>
          </CardHeader>
          <CardContent className="flex-1">
            <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
              {(stats?.recentCases ?? []).length === 0 ? (
                <div className="py-10 text-center text-xs text-muted-foreground">暂无判定记录</div>
              ) : (
                stats!.recentCases.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onInspectCase(item)}
                    className="flex w-full items-center justify-between rounded-lg border border-border/60 bg-secondary/40 px-3 py-2 text-left transition-colors hover:border-brand/40 hover:bg-accent"
                  >
                    <div className="min-w-0 flex-1 pr-3">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-semibold">{item.caseId}</span>
                        <span className="truncate text-xs text-muted-foreground">
                          {item.passengerName || '未知旅客'}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {item.reasons[0] || item.resolutionNote || '-'}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {verdictBadge(item.verdictStatus)}
                      <span className="font-mono text-[11px] text-muted-foreground">{fmtTime(item.judgedAt)}</span>
                    </div>
                  </button>
                ))
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="flex flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Terminal className="size-4 text-muted-foreground" />
              实时系统日志
            </CardTitle>
            <CardDescription className="text-xs">引擎调度、通道状态与判定日志（同步持久化）</CardDescription>
          </CardHeader>
          <CardContent className="flex-1">
            <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-border bg-background/80 p-3 font-mono text-[11px]">
              {logs.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">暂无运行日志</div>
              ) : (
                logs.slice(-60).map((log) => (
                  <div key={log.id} className="flex gap-2 leading-relaxed">
                    <span className="shrink-0 text-muted-foreground">{fmtTime(log.createdAt)}</span>
                    <span
                      className={`shrink-0 font-semibold ${
                        log.level === 'ERROR'
                          ? 'text-rose-500'
                          : log.level === 'WARN'
                            ? 'text-amber-500'
                            : 'text-brand'
                      }`}
                    >
                      [{log.level}]
                    </span>
                    <span className="break-all text-foreground/90">{log.message}</span>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
