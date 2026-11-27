import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  FileSearch,
  Globe,
  History,
  LogOut,
  Settings,
  Shield,
  ShieldCheck,
  Users,
  Workflow,
} from 'lucide-react'
import { conveyor, queryClient } from '@/conveyor/client'
import { WindowFrame } from './shell'
import { LoginPage } from './pages/login-page'
import { DashboardPage } from './pages/dashboard-page'
import { BatchesPage } from './pages/batches-page'
import { CasesPage } from './pages/cases-page'
import { NodeRedPage } from './pages/nodered-page'
import { SettingsPage } from './pages/settings-page'
import { SystemPage } from './pages/system-page'
import type {
  AppConfig,
  AuthSession,
  EngineStatus,
  OnlineUser,
  SystemLogEntry,
  TestCaseRecord,
  VerdictStatus,
} from '@/lib/autotest/types'
import './styles/app.css'

type NavTab = 'dashboard' | 'batches' | 'cases' | 'nodered' | 'system' | 'settings'

const INITIAL_STATUS: EngineStatus = {
  running: false,
  mode: 'idle',
  activeBatchId: null,
  activeRunCode: null,
  stage: '已停止',
  stageDetail: '点击「启动实时测试」或「一键自测模拟」开始运行',
  gateConnected: false,
  gateClientCount: 0,
  logConnected: false,
  httpApiUrl: null,
  logLineCount: 0,
  sessionCount: 0,
  pendingCases: 0,
  receivedCount: 0,
  passedCount: 0,
  failedCount: 0,
  timeoutCount: 0,
  startedAt: null,
}

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [onlineUsers, setOnlineUsers] = useState<OnlineUser[]>([])
  const [syncConnected, setSyncConnected] = useState(false)

  useEffect(() => {
    conveyor.autotest
      .restoreSession()
      .then((s) => {
        setSession(s)
        setOnlineUsers(s.onlineUsers || [])
        setSyncConnected(s.connected)
      })
      .catch(() => {
        setSession({
          serverUrl: 'http://127.0.0.1:3100',
          connected: false,
          user: null,
          allPermissions: [],
          onlineUsers: [],
        })
      })
      .finally(() => setSessionLoading(false))
  }, [])

  conveyor.autotest.onPresenceChange.useEvent((users) => {
    setOnlineUsers(users)
    setSyncConnected(true)
  })

  if (sessionLoading) {
    return (
      <WindowFrame title="Gate AutoTest Studio — 闸机自动化测试协作平台">
        <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
          正在连接共享后端服务器…
        </div>
      </WindowFrame>
    )
  }

  if (!session?.user) {
    return (
      <WindowFrame title="Gate AutoTest Studio — 登录共享测试平台">
        <LoginPage
          initialServerUrl={session?.serverUrl || 'http://127.0.0.1:3100'}
          onLoggedIn={(next) => {
            setSession(next)
            setOnlineUsers(next.onlineUsers || [])
            setSyncConnected(true)
            void queryClient.invalidateQueries()
          }}
        />
      </WindowFrame>
    )
  }

  return (
    <AuthenticatedWorkspace
      session={session}
      onlineUsers={onlineUsers}
      syncConnected={syncConnected}
      onLogout={async () => {
        const next = await conveyor.autotest.logout()
        setSession(next)
        setOnlineUsers([])
        setSyncConnected(false)
        queryClient.clear()
      }}
    />
  )
}

function AuthenticatedWorkspace({
  session,
  onlineUsers,
  syncConnected,
  onLogout,
}: {
  session: AuthSession
  onlineUsers: OnlineUser[]
  syncConnected: boolean
  onLogout: () => Promise<void>
}) {
  const user = session.user!
  const hasPerm = (code: string) => user.roleCode === 'admin' || user.permissions.includes(code)
  const canSystem = hasPerm('system:user') || hasPerm('system:role') || hasPerm('system:audit')

  const [activeTab, setActiveTab] = useState<NavTab>('dashboard')
  const [status, setStatus] = useState<EngineStatus>(INITIAL_STATUS)
  const [liveLogs, setLiveLogs] = useState<SystemLogEntry[]>([])
  const [busy, setBusy] = useState(false)

  // Cases filter state
  const [selectedBatchId, setSelectedBatchId] = useState<number | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState<VerdictStatus | 'ALL'>('ALL')
  const [keyword, setKeyword] = useState('')
  const [selectedCaseId, setSelectedCaseId] = useState<number | null>(null)
  const [analyzingId, setAnalyzingId] = useState<number | null>(null)

  const statsQuery = conveyor.autotest.getDashboardStats.useQuery()
  const batchesQuery = conveyor.autotest.listBatches.useQuery({ input: { limit: 100, offset: 0 } })
  const casesQuery = conveyor.autotest.listCases.useQuery({
    input: {
      batchId: selectedBatchId,
      status: statusFilter,
      keyword: keyword || undefined,
      limit: 200,
      offset: 0,
    },
  })
  const configQuery = conveyor.autotest.getConfig.useQuery()
  const dbInfoQuery = conveyor.autotest.getDatabaseInfo.useQuery()
  const usersQuery = conveyor.autotest.listUsers.useQuery({ enabled: canSystem && hasPerm('system:user') })
  const rolesQuery = conveyor.autotest.listRoles.useQuery({ enabled: canSystem })
  const auditQuery = conveyor.autotest.listAuditLogs.useQuery({
    input: 150,
    enabled: canSystem && hasPerm('system:audit'),
  })

  const refreshAll = useCallback(() => {
    void queryClient.invalidateQueries()
  }, [])

  useEffect(() => {
    conveyor.autotest.getStatus().then(setStatus).catch(() => {})
    conveyor.autotest
      .listSystemLogs({ limit: 80 })
      .then(setLiveLogs)
      .catch(() => {})
  }, [])

  // Local engine events + Remote team real-time sync events
  conveyor.autotest.onStatusChange.useEvent((s) => setStatus(s))
  conveyor.autotest.onCaseJudged.useEvent(() => refreshAll())
  conveyor.autotest.onBatchUpdated.useEvent(() => refreshAll())
  conveyor.autotest.onLogEntry.useEvent((entry) => {
    setLiveLogs((prev) => [...prev.slice(-119), entry])
  })
  conveyor.autotest.onTeamSync.useEvent((msg) => {
    if (msg.event === 'log:created' && msg.data && typeof msg.data === 'object') {
      const logEntry = msg.data as SystemLogEntry
      setLiveLogs((prev) => (prev.some((x) => x.id === logEntry.id) ? prev : [...prev.slice(-119), logEntry]))
    }
    refreshAll()
  })

  const handleStart = async () => {
    setBusy(true)
    try {
      const s = await conveyor.autotest.startEngine({})
      setStatus(s)
      refreshAll()
    } finally {
      setBusy(false)
    }
  }

  const handleStop = async () => {
    setBusy(true)
    try {
      const s = await conveyor.autotest.stopEngine()
      setStatus(s)
      refreshAll()
    } finally {
      setBusy(false)
    }
  }

  const handleSelfTest = async () => {
    setBusy(true)
    try {
      const s = await conveyor.autotest.runSelfTest()
      setStatus(s)
      refreshAll()
    } finally {
      setBusy(false)
    }
  }

  const handleInspectCase = (record: TestCaseRecord) => {
    setSelectedBatchId(undefined)
    setStatusFilter('ALL')
    setKeyword('')
    setSelectedCaseId(record.id)
    setActiveTab('cases')
  }

  const handleViewBatchCases = (batchId: number) => {
    setSelectedBatchId(batchId)
    setStatusFilter('ALL')
    setKeyword('')
    setSelectedCaseId(null)
    setActiveTab('cases')
  }

  const handleExportBatch = async (batchId: number, format: 'html' | 'json') => {
    await conveyor.autotest.exportBatchReport({ batchId, format })
  }

  const handleDeleteBatch = async (batchId: number) => {
    await conveyor.autotest.deleteBatch(batchId)
    if (selectedBatchId === batchId) setSelectedBatchId(undefined)
    refreshAll()
  }

  const handleAnalyzeCase = async (id: number) => {
    setAnalyzingId(id)
    try {
      await conveyor.autotest.analyzeCase(id)
      refreshAll()
    } finally {
      setAnalyzingId(null)
    }
  }

  const handleDeleteCase = async (id: number) => {
    await conveyor.autotest.deleteCase(id)
    if (selectedCaseId === id) setSelectedCaseId(null)
    refreshAll()
  }

  const handleSaveConfig = async (cfg: AppConfig) => {
    await conveyor.autotest.saveConfig(cfg)
    refreshAll()
  }

  const handleResetConfig = async () => {
    await conveyor.autotest.resetConfig()
    refreshAll()
  }

  const navItems: { id: NavTab; label: string; icon: typeof Activity; show: boolean }[] = [
    { id: 'dashboard', label: '监控大盘', icon: Activity, show: true },
    { id: 'batches', label: '历史批次', icon: History, show: true },
    { id: 'cases', label: '用例追溯', icon: FileSearch, show: true },
    { id: 'nodered', label: '流程编排', icon: Workflow, show: true },
    { id: 'system', label: '系统管理', icon: Shield, show: canSystem },
    { id: 'settings', label: '系统配置', icon: Settings, show: true },
  ]

  return (
    <WindowFrame title="Gate AutoTest Studio — 闸机自动化测试协作平台">
      <div className="flex h-full overflow-hidden">
        {/* Sidebar */}
        <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card/80">
          <div className="flex items-center gap-2.5 border-b border-border px-4 py-4">
            <div className="flex size-8 items-center justify-center rounded-lg bg-brand text-white shadow-xs">
              <ShieldCheck className="size-4" />
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold tracking-tight">Gate AutoTest</div>
              <div className="truncate text-[10px] text-muted-foreground">多人连线共享测试平台</div>
            </div>
          </div>

          <nav className="flex-1 space-y-1 p-2.5">
            {navItems
              .filter((item) => item.show)
              .map((item) => {
                const Icon = item.icon
                const active = activeTab === item.id
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setActiveTab(item.id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors ${
                      active
                        ? 'bg-brand/12 text-brand'
                        : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                    }`}
                  >
                    <Icon className="size-4 shrink-0" />
                    <span>{item.label}</span>
                  </button>
                )
              })}
          </nav>

          {/* Bottom User + Team Online Status */}
          <div className="space-y-2 border-t border-border p-3 text-[11px]">
            <div className="space-y-1.5 rounded-lg border border-border/80 bg-secondary/50 p-2.5">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Globe className="size-3" /> 共享连线
                </span>
                <span className={syncConnected ? 'font-medium text-emerald-500' : 'text-amber-500'}>
                  {syncConnected ? '实时同步中' : '重连中'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Users className="size-3" /> 在线成员
                </span>
                <span className="font-mono text-foreground">{Math.max(1, onlineUsers.length)} 人</span>
              </div>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border/70 bg-background/70 px-2.5 py-2">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{user.nickname}</div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {user.roleName} · {user.username}
                </div>
              </div>
              <button
                type="button"
                onClick={onLogout}
                title="退出登录"
                className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-500"
              >
                <LogOut className="size-3.5" />
              </button>
            </div>
          </div>
        </aside>

        {/* Main View */}
        <section className="min-w-0 flex-1 overflow-hidden bg-background">
          {activeTab === 'dashboard' && (
            <DashboardPage
              status={status}
              stats={statsQuery.data}
              logs={liveLogs}
              onStart={handleStart}
              onStop={handleStop}
              onSelfTest={handleSelfTest}
              onInspectCase={handleInspectCase}
              busy={busy || !hasPerm('engine:control')}
            />
          )}
          {activeTab === 'batches' && (
            <BatchesPage
              batches={batchesQuery.data ?? []}
              onViewBatchCases={handleViewBatchCases}
              onExportBatch={handleExportBatch}
              onDeleteBatch={handleDeleteBatch}
            />
          )}
          {activeTab === 'cases' && (
            <CasesPage
              cases={casesQuery.data?.items ?? []}
              batches={batchesQuery.data ?? []}
              selectedBatchId={selectedBatchId}
              onSelectBatchId={setSelectedBatchId}
              statusFilter={statusFilter}
              onStatusFilterChange={setStatusFilter}
              keyword={keyword}
              onKeywordChange={setKeyword}
              selectedCaseId={selectedCaseId}
              onSelectCaseId={setSelectedCaseId}
              onAnalyzeCase={handleAnalyzeCase}
              onDeleteCase={handleDeleteCase}
              analyzingId={analyzingId}
            />
          )}
          {activeTab === 'nodered' && <NodeRedPage />}
          {activeTab === 'system' && canSystem && (
            <SystemPage
              currentUser={user}
              users={usersQuery.data ?? []}
              roles={rolesQuery.data?.roles ?? []}
              allPermissions={rolesQuery.data?.allPermissions ?? session.allPermissions}
              auditLogs={auditQuery.data ?? []}
              onRefresh={refreshAll}
            />
          )}
          {activeTab === 'settings' && (
            <SettingsPage
              config={configQuery.data}
              dbInfo={dbInfoQuery.data}
              serverUrl={session.serverUrl}
              onSave={handleSaveConfig}
              onReset={handleResetConfig}
              onPickLogFile={() => conveyor.autotest.pickLogFile()}
              onPickLogDir={() => conveyor.autotest.pickLogDir()}
            />
          )}
        </section>
      </div>
    </WindowFrame>
  )
}
