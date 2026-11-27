import { EventEmitter } from 'events'
import fs from 'fs'
import path from 'path'
import { app } from 'electron'
import {
  DEFAULT_APP_CONFIG,
  type AnalysisResult,
  type AnalysisStatus,
  type AppConfig,
  type AuditLogEntry,
  type AuthSession,
  type BatchStatus,
  type DashboardStats,
  type DatabaseInfo,
  type OnlineUser,
  type PermissionDef,
  type RoleItem,
  type SystemLogEntry,
  type TestBatch,
  type TestCaseRecord,
  type UserProfile,
  type VerdictStatus,
} from './types'

interface SavedSession {
  serverUrl: string
  token: string | null
}

export class SharedApiClient extends EventEmitter {
  private serverUrl = 'http://127.0.0.1:3100'
  private token: string | null = null
  private user: UserProfile | null = null
  private allPermissions: PermissionDef[] = []
  private onlineUsers: OnlineUser[] = []
  private syncConnected = false
  private lastDataVersion = 0
  private pollTimer: NodeJS.Timeout | null = null
  private sessionFile = ''

  init(): void {
    const dir = app.getPath('userData')
    fs.mkdirSync(dir, { recursive: true })
    this.sessionFile = path.join(dir, 'server-session.json')
    if (fs.existsSync(this.sessionFile)) {
      try {
        const saved = JSON.parse(fs.readFileSync(this.sessionFile, 'utf-8')) as Partial<SavedSession>
        if (saved.serverUrl) this.serverUrl = saved.serverUrl.replace(/\/+$/, '')
        if (saved.token) this.token = saved.token
      } catch {
        // ignore
      }
    }
  }

  private persistSession(): void {
    if (!this.sessionFile) return
    try {
      fs.writeFileSync(
        this.sessionFile,
        JSON.stringify({ serverUrl: this.serverUrl, token: this.token }, null, 2),
        'utf-8'
      )
    } catch {
      // ignore
    }
  }

  getServerUrl(): string {
    return this.serverUrl
  }

  setServerUrl(url: string): string {
    const cleaned = url.trim().replace(/\/+$/, '') || 'http://127.0.0.1:3100'
    this.serverUrl = cleaned
    this.persistSession()
    return this.serverUrl
  }

  async checkHealth(targetUrl?: string): Promise<{ ok: boolean; dbDriver?: string; onlineCount?: number; error?: string }> {
    const base = (targetUrl || this.serverUrl).trim().replace(/\/+$/, '')
    try {
      const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(4000) })
      if (!res.ok) return { ok: false, error: `HTTP ${res.status}` }
      const data = (await res.json()) as { ok: boolean; dbDriver?: string; onlineCount?: number }
      return data
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : '无法连接服务器' }
    }
  }

  private async request<T>(method: string, route: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    const res = await fetch(`${this.serverUrl}${route}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(15000),
    })
    const data = (await res.json().catch(() => ({}))) as T & { error?: string }
    if (!res.ok) {
      if (res.status === 401) {
        this.token = null
        this.user = null
        this.disconnectSync()
        this.persistSession()
      }
      throw new Error(data.error || `请求失败 (${res.status})`)
    }
    return data
  }

  async restoreSession(): Promise<AuthSession> {
    if (!this.token) {
      return this.getSessionSnapshot()
    }
    try {
      const me = await this.request<{
        user: UserProfile
        allPermissions: PermissionDef[]
        onlineUsers: OnlineUser[]
      }>('GET', '/api/auth/me')
      this.user = me.user
      this.allPermissions = me.allPermissions
      this.onlineUsers = me.onlineUsers || []
      this.connectSync()
    } catch {
      this.user = null
      this.token = null
      this.persistSession()
    }
    return this.getSessionSnapshot()
  }

  async login(serverUrl: string, username: string, password: string): Promise<AuthSession> {
    this.setServerUrl(serverUrl)
    const data = await this.request<{
      token: string
      user: UserProfile
      allPermissions: PermissionDef[]
    }>('POST', '/api/auth/login', { username, password })
    this.token = data.token
    this.user = data.user
    this.allPermissions = data.allPermissions
    this.persistSession()
    this.connectSync()
    return this.getSessionSnapshot()
  }

  logout(): AuthSession {
    this.token = null
    this.user = null
    this.onlineUsers = []
    this.disconnectSync()
    this.persistSession()
    return this.getSessionSnapshot()
  }

  getSessionSnapshot(): AuthSession {
    return {
      serverUrl: this.serverUrl,
      connected: this.syncConnected,
      user: this.user,
      allPermissions: this.allPermissions,
      onlineUsers: this.onlineUsers,
    }
  }

  private connectSync(): void {
    this.disconnectSync()
    if (!this.token) return
    const poll = async () => {
      if (!this.token) return
      try {
        const res = await this.request<{ version: number; onlineUsers: OnlineUser[] }>('GET', '/api/sync/poll')
        this.syncConnected = true
        this.onlineUsers = res.onlineUsers || []
        this.emit('presence', this.onlineUsers)
        if (this.lastDataVersion !== 0 && res.version !== this.lastDataVersion) {
          this.emit('sync', { event: 'data:changed', data: { version: res.version } })
        }
        this.lastDataVersion = res.version
      } catch {
        this.syncConnected = false
      }
    }
    void poll()
    this.pollTimer = setInterval(() => {
      void poll()
    }, 3000)
  }

  disconnectSync(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    }
    this.syncConnected = false
    this.lastDataVersion = 0
  }

  // ---- AutoTest Shared Data APIs ----
  async getDashboardStats(): Promise<DashboardStats> {
    return this.request<DashboardStats>('GET', '/api/dashboard')
  }

  async getStorageInfo(): Promise<DatabaseInfo> {
    return this.request<DatabaseInfo>('GET', '/api/storage-info')
  }

  async getConfig(): Promise<AppConfig> {
    try {
      const cfg = await this.request<AppConfig | null>('GET', '/api/config')
      return cfg ?? { ...DEFAULT_APP_CONFIG }
    } catch {
      return { ...DEFAULT_APP_CONFIG }
    }
  }

  async saveConfig(config: AppConfig): Promise<AppConfig> {
    return this.request<AppConfig>('PUT', '/api/config', config)
  }

  async resetConfig(): Promise<AppConfig> {
    const res = await this.request<AppConfig | null>('POST', '/api/config/reset')
    return res ?? { ...DEFAULT_APP_CONFIG }
  }

  async createBatch(input: {
    runCode: string
    name: string
    gateMode: string
    logSource: string
    configSnapshot: AppConfig
    notes?: string
  }): Promise<TestBatch> {
    return this.request<TestBatch>('POST', '/api/batches', input)
  }

  async finishBatch(batchId: number, status: BatchStatus = 'completed'): Promise<TestBatch | null> {
    return this.request<TestBatch | null>('POST', `/api/batches/${batchId}/finish`, { status })
  }

  async getBatch(id: number): Promise<TestBatch | null> {
    return this.request<TestBatch | null>('GET', `/api/batches/${id}`)
  }

  async listBatches(limit = 50, offset = 0): Promise<TestBatch[]> {
    return this.request<TestBatch[]>('GET', `/api/batches?limit=${limit}&offset=${offset}`)
  }

  async deleteBatch(id: number): Promise<boolean> {
    await this.request('DELETE', `/api/batches/${id}`)
    return true
  }

  async insertCase(input: Record<string, unknown>): Promise<{ record: TestCaseRecord; batch: TestBatch | null }> {
    return this.request<{ record: TestCaseRecord; batch: TestBatch | null }>('POST', '/api/cases', input)
  }

  async updateCaseAnalysis(
    id: number,
    status: AnalysisStatus,
    analysis: AnalysisResult | null
  ): Promise<TestCaseRecord | null> {
    return this.request<TestCaseRecord | null>('PUT', `/api/cases/${id}/analysis`, { status, analysis })
  }

  async getCase(id: number): Promise<TestCaseRecord | null> {
    return this.request<TestCaseRecord | null>('GET', `/api/cases/${id}`)
  }

  async listCases(filter?: {
    batchId?: number
    status?: VerdictStatus | 'ALL'
    keyword?: string
    limit?: number
    offset?: number
  }): Promise<{ items: TestCaseRecord[]; total: number }> {
    const qs = new URLSearchParams()
    if (filter?.batchId) qs.set('batchId', String(filter.batchId))
    if (filter?.status) qs.set('status', filter.status)
    if (filter?.keyword) qs.set('keyword', filter.keyword)
    qs.set('limit', String(filter?.limit ?? 100))
    qs.set('offset', String(filter?.offset ?? 0))
    return this.request<{ items: TestCaseRecord[]; total: number }>('GET', `/api/cases?${qs.toString()}`)
  }

  async deleteCase(id: number): Promise<boolean> {
    await this.request('DELETE', `/api/cases/${id}`)
    return true
  }

  async insertSystemLog(entry: {
    batchId: number | null
    level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG'
    source: string
    message: string
  }): Promise<SystemLogEntry> {
    return this.request<SystemLogEntry>('POST', '/api/system-logs', entry)
  }

  async listSystemLogs(batchId?: number | null, limit = 150): Promise<SystemLogEntry[]> {
    const qs = new URLSearchParams({ limit: String(limit) })
    if (batchId) qs.set('batchId', String(batchId))
    return this.request<SystemLogEntry[]>('GET', `/api/system-logs?${qs.toString()}`)
  }

  // ---- System Management (RuoYi-style) ----
  async listUsers(): Promise<UserProfile[]> {
    return this.request<UserProfile[]>('GET', '/api/system/users')
  }
  async createUser(data: {
    username: string
    nickname: string
    password: string
    roleCode: string
    department?: string
    email?: string
  }): Promise<UserProfile> {
    return this.request<UserProfile>('POST', '/api/system/users', data)
  }
  async updateUser(
    id: number,
    data: { nickname?: string; roleCode?: string; department?: string; email?: string; status?: 'active' | 'disabled' }
  ): Promise<UserProfile> {
    return this.request<UserProfile>('PUT', `/api/system/users/${id}`, data)
  }
  async resetUserPassword(id: number, password: string): Promise<boolean> {
    await this.request('POST', `/api/system/users/${id}/reset-password`, { password })
    return true
  }
  async deleteUser(id: number): Promise<boolean> {
    await this.request('DELETE', `/api/system/users/${id}`)
    return true
  }

  async listRoles(): Promise<{ roles: RoleItem[]; allPermissions: PermissionDef[] }> {
    return this.request<{ roles: RoleItem[]; allPermissions: PermissionDef[] }>('GET', '/api/system/roles')
  }
  async createRole(data: { code: string; name: string; description?: string; permissions: string[] }): Promise<RoleItem> {
    return this.request<RoleItem>('POST', '/api/system/roles', data)
  }
  async updateRole(
    code: string,
    data: { name?: string; description?: string; permissions?: string[] }
  ): Promise<RoleItem> {
    return this.request<RoleItem>('PUT', `/api/system/roles/${code}`, data)
  }
  async deleteRole(code: string): Promise<boolean> {
    await this.request('DELETE', `/api/system/roles/${code}`)
    return true
  }

  async listAuditLogs(limit = 150): Promise<AuditLogEntry[]> {
    return this.request<AuditLogEntry[]>('GET', `/api/system/audit-logs?limit=${limit}`)
  }
}

export const apiClient = new SharedApiClient()
