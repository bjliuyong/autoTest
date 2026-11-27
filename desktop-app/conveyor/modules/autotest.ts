import fs from 'fs'
import path from 'path'
import { BrowserWindow, dialog, shell } from 'electron'
import { z } from 'zod'
import { createEmitter } from 'electron-conveyor/main'
import { defineModule, query, command, event } from '../init'
import { apiClient } from '@/lib/autotest/api-client'
import { engine } from '@/lib/autotest/engine'
import { nodeRedManager } from '@/lib/autotest/nodered-manager'
import { buildBatchHtmlReport } from '@/lib/autotest/report-export'
import type {
  AppConfig,
  EngineStatus,
  OnlineUser,
  SystemLogEntry,
  TestBatch,
  TestCaseRecord,
  VerdictStatus,
} from '@/lib/autotest/types'

export const autotestModule = defineModule({
  // ---- Auth & Shared Server Connection ----
  restoreSession: query(() => apiClient.restoreSession()),
  getSession: query(() => apiClient.getSessionSnapshot()),
  checkServerHealth: command(z.string().optional(), ({ input }) => apiClient.checkHealth(input)),
  login: command(
    z.object({ serverUrl: z.string(), username: z.string(), password: z.string() }),
    ({ input }) => apiClient.login(input.serverUrl, input.username, input.password)
  ),
  logout: command(async () => {
    await engine.stop()
    return apiClient.logout()
  }),

  // ---- Engine Lifecycle ----
  getStatus: query(() => engine.getStatus()),
  startEngine: command(z.object({ batchName: z.string().optional() }).optional(), ({ input }) =>
    engine.start(input?.batchName)
  ),
  stopEngine: command(() => engine.stop()),
  runSelfTest: command(() => engine.runSelfTest()),
  pushManualCase: command(z.record(z.string(), z.unknown()), ({ input }) => engine.pushManualCase(input)),

  // ---- Dashboard & Server Info ----
  getDashboardStats: query(() => apiClient.getDashboardStats()),
  getDatabaseInfo: query(() => apiClient.getStorageInfo()),

  // ---- Batches ----
  listBatches: query(
    z.object({ limit: z.number().optional(), offset: z.number().optional() }).optional(),
    ({ input }) => apiClient.listBatches(input?.limit ?? 50, input?.offset ?? 0)
  ),
  getBatch: query(z.number(), ({ input }) => apiClient.getBatch(input)),
  deleteBatch: command(z.number(), ({ input }) => apiClient.deleteBatch(input)),
  exportBatchReport: command(
    z.object({ batchId: z.number(), format: z.enum(['html', 'json']) }),
    async ({ input, ctx }) => {
      const batch = await apiClient.getBatch(input.batchId)
      if (!batch) return { exported: false, error: '批次不存在' }
      const { items } = await apiClient.listCases({ batchId: input.batchId, limit: 5000, offset: 0 })
      const ext = input.format === 'html' ? 'html' : 'json'
      const defaultName = `${batch.runCode}-report.${ext}`
      const res = await dialog.showSaveDialog(ctx.window ?? BrowserWindow.getFocusedWindow()!, {
        title: '导出测试批次报告',
        defaultPath: defaultName,
        filters: [
          input.format === 'html'
            ? { name: 'HTML 报告', extensions: ['html'] }
            : { name: 'JSON 数据', extensions: ['json'] },
        ],
      })
      if (res.canceled || !res.filePath) return { exported: false }
      const content =
        input.format === 'html'
          ? buildBatchHtmlReport(batch, items)
          : JSON.stringify({ batch, cases: items }, null, 2)
      fs.writeFileSync(res.filePath, content, 'utf-8')
      shell.showItemInFolder(path.resolve(res.filePath))
      return { exported: true, filePath: res.filePath }
    }
  ),

  // ---- Cases ----
  listCases: query(
    z
      .object({
        batchId: z.number().optional(),
        status: z.enum(['ALL', 'PASS', 'FAIL', 'TIMEOUT', 'ERROR']).optional(),
        keyword: z.string().optional(),
        limit: z.number().optional(),
        offset: z.number().optional(),
      })
      .optional(),
    ({ input }) =>
      apiClient.listCases({
        batchId: input?.batchId,
        status: input?.status as VerdictStatus | 'ALL' | undefined,
        keyword: input?.keyword,
        limit: input?.limit ?? 100,
        offset: input?.offset ?? 0,
      })
  ),
  getCase: query(z.number(), ({ input }) => apiClient.getCase(input)),
  deleteCase: command(z.number(), ({ input }) => apiClient.deleteCase(input)),
  analyzeCase: command(z.number(), ({ input }) => engine.analyzeCase(input)),

  // ---- System Logs ----
  listSystemLogs: query(
    z.object({ batchId: z.number().nullable().optional(), limit: z.number().optional() }).optional(),
    ({ input }) => apiClient.listSystemLogs(input?.batchId ?? null, input?.limit ?? 150)
  ),

  // ---- Shared Config ----
  getConfig: query(() => apiClient.getConfig()),
  saveConfig: command(z.custom<AppConfig>(), ({ input }) => apiClient.saveConfig(input)),
  resetConfig: command(() => apiClient.resetConfig()),
  pickLogFile: command(async ({ ctx }) => {
    const res = await dialog.showOpenDialog(ctx.window ?? BrowserWindow.getFocusedWindow()!, {
      title: '选择待测系统日志文件',
      properties: ['openFile'],
      filters: [
        { name: '日志文件', extensions: ['log', 'txt'] },
        { name: '所有文件', extensions: ['*'] },
      ],
    })
    if (res.canceled || res.filePaths.length === 0) return null
    return res.filePaths[0]
  }),
  pickLogDir: command(async ({ ctx }) => {
    const res = await dialog.showOpenDialog(ctx.window ?? BrowserWindow.getFocusedWindow()!, {
      title: '选择待测系统日志目录',
      properties: ['openDirectory'],
    })
    if (res.canceled || res.filePaths.length === 0) return null
    return res.filePaths[0]
  }),

  // ---- System Management (RuoYi-style RBAC) ----
  listUsers: query(() => apiClient.listUsers()),
  createUser: command(
    z.object({
      username: z.string(),
      nickname: z.string(),
      password: z.string(),
      roleCode: z.string(),
      department: z.string().optional(),
      email: z.string().optional(),
    }),
    ({ input }) => apiClient.createUser(input)
  ),
  updateUser: command(
    z.object({
      id: z.number(),
      nickname: z.string().optional(),
      roleCode: z.string().optional(),
      department: z.string().optional(),
      email: z.string().optional(),
      status: z.enum(['active', 'disabled']).optional(),
    }),
    ({ input }) => {
      const { id, ...rest } = input
      return apiClient.updateUser(id, rest)
    }
  ),
  resetUserPassword: command(z.object({ id: z.number(), password: z.string() }), ({ input }) =>
    apiClient.resetUserPassword(input.id, input.password)
  ),
  deleteUser: command(z.number(), ({ input }) => apiClient.deleteUser(input)),

  listRoles: query(() => apiClient.listRoles()),
  createRole: command(
    z.object({
      code: z.string(),
      name: z.string(),
      description: z.string().optional(),
      permissions: z.array(z.string()),
    }),
    ({ input }) => apiClient.createRole(input)
  ),
  updateRole: command(
    z.object({
      code: z.string(),
      name: z.string().optional(),
      description: z.string().optional(),
      permissions: z.array(z.string()).optional(),
    }),
    ({ input }) => {
      const { code, ...rest } = input
      return apiClient.updateRole(code, rest)
    }
  ),
  deleteRole: command(z.string(), ({ input }) => apiClient.deleteRole(input)),

  listAuditLogs: query(z.number().optional(), ({ input }) => apiClient.listAuditLogs(input ?? 150)),

  // ---- Node-RED Visual Flow Integration ----
  noderedStatus: query(() => nodeRedManager.getStatus()),
  noderedSetUrl: command(z.string(), async ({ input }) => {
    nodeRedManager.setUrl(input)
    return nodeRedManager.getStatus()
  }),
  noderedStart: command(z.number().optional(), ({ input }) => nodeRedManager.startEmbedded(input ?? 1880)),
  noderedStop: command(() => nodeRedManager.stopEmbedded()),
  noderedListFlows: query(() => nodeRedManager.listFlows()),
  noderedRunFlow: command(z.string(), ({ input }) => nodeRedManager.runFlow(input)),
  noderedTriggerInject: command(z.string(), ({ input }) => nodeRedManager.triggerInject(input)),
  noderedToggleFlow: command(z.object({ flowId: z.string(), disabled: z.boolean() }), ({ input }) =>
    nodeRedManager.toggleFlow(input.flowId, input.disabled)
  ),
  noderedCreateFlow: command(z.string(), ({ input }) => nodeRedManager.createFlow(input)),
  noderedOpenExternal: command(() => {
    shell.openExternal(nodeRedManager.getUrl())
    return true
  }),

  // ---- Real-time Main -> Renderer Push Events ----
  onStatusChange: event(z.custom<EngineStatus>()),
  onCaseJudged: event(z.custom<TestCaseRecord>()),
  onBatchUpdated: event(z.custom<TestBatch>()),
  onLogEntry: event(z.custom<SystemLogEntry>()),
  onTeamSync: event(z.object({ event: z.string(), data: z.unknown() })),
  onPresenceChange: event(z.array(z.custom<OnlineUser>())),
})

export function setupAutotestEvents(win: BrowserWindow): void {
  const emit = createEmitter(autotestModule, win)
  const onStatus = (s: EngineStatus) => emit.onStatusChange(s)
  const onCase = (c: TestCaseRecord) => emit.onCaseJudged(c)
  const onBatch = (b: TestBatch) => emit.onBatchUpdated(b)
  const onLog = (l: SystemLogEntry) => emit.onLogEntry(l)
  const onSync = (msg: { event: string; data: unknown }) => emit.onTeamSync(msg)
  const onPresence = (users: OnlineUser[]) => emit.onPresenceChange(users)

  engine.on('status', onStatus)
  engine.on('case', onCase)
  engine.on('batch', onBatch)
  engine.on('log', onLog)
  apiClient.on('sync', onSync)
  apiClient.on('presence', onPresence)

  win.on('closed', () => {
    engine.off('status', onStatus)
    engine.off('case', onCase)
    engine.off('batch', onBatch)
    engine.off('log', onLog)
    apiClient.off('sync', onSync)
    apiClient.off('presence', onPresence)
  })
}
