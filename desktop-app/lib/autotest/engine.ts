import { EventEmitter } from 'events'
import fs from 'fs'
import http from 'http'
import path from 'path'
import { spawn } from 'child_process'
import iconv from 'iconv-lite'
import WebSocket, { WebSocketServer } from 'ws'
import { apiClient } from './api-client'
import { Judge } from './judge'
import { buildDynamicLogPath } from './log-path'
import { LogStore, linesToText, sanitizeLogLine } from './logstore'
import {
  NotACaseMessage,
  ProtocolError,
  applyFieldMap,
  buildAck,
  extractPhotoData,
  getPassengerCandidates,
  parseGateMessage,
} from './protocol'
import { RoundTracker, serialValue } from './rounds'
import type {
  AnalysisResult,
  AppConfig,
  EngineStatus,
  GateCapture,
  SystemLogEntry,
  TestCaseRecord,
} from './types'

interface PendingCase {
  capture: GateCapture
  deadlineMs: number
  timer: NodeJS.Timeout
}

function makeRunCode(prefix = 'RUN'): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `${prefix}-${stamp}`
}

/** Generate a clean SVG data URL avatar for self-test cases (cross-platform, zero deps) */
function makeMockPhotoSvg(name: string, accent: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#0f172a"/><stop offset="100%" stop-color="${accent}"/></linearGradient></defs>
    <rect width="320" height="240" fill="url(#g)"/>
    <circle cx="160" cy="92" r="38" fill="rgba(255,255,255,0.22)"/>
    <path d="M96 202c0-35 29-64 64-64s64 29 64 64" fill="rgba(255,255,255,0.22)"/>
    <text x="160" y="226" font-family="sans-serif" font-size="14" fill="rgba(255,255,255,0.85)" text-anchor="middle">${name} · 闸机抓拍模拟</text>
  </svg>`
  return `data:image/svg+xml;base64,${Buffer.from(svg, 'utf-8').toString('base64')}`
}

export class AutoTestEngine extends EventEmitter {
  private running = false
  private mode: 'idle' | 'live' | 'selftest' = 'idle'
  private config: AppConfig | null = null
  private activeBatchId: number | null = null
  private activeRunCode: string | null = null
  private startedAt: string | null = null

  private logStore: LogStore | null = null
  private judge: Judge | null = null
  private rounds: RoundTracker | null = null

  private gateWsClient: WebSocket | null = null
  private gateWsServer: WebSocketServer | null = null
  private gateServerClients = new Set<WebSocket>()
  private gateReconnectTimer: NodeJS.Timeout | null = null

  private logWsClient: WebSocket | null = null
  private logReconnectTimer: NodeJS.Timeout | null = null
  private fileTailTimer: NodeJS.Timeout | null = null
  private fileOffset = 0
  private fileRemainder = Buffer.alloc(0)

  private httpServer: http.Server | null = null
  private httpApiUrl: string | null = null

  private pendingCases = new Map<string, PendingCase>()
  private resolveInterval: NodeJS.Timeout | null = null
  private selfTestTimers: NodeJS.Timeout[] = []

  private gateConnected = false
  private logConnected = false
  private stage = '已停止'
  private stageDetail = '点击「启动测试」或「一键自测」开始运行'
  private receivedCount = 0
  private passedCount = 0
  private failedCount = 0
  private timeoutCount = 0

  getStatus(): EngineStatus {
    return {
      running: this.running,
      mode: this.mode,
      activeBatchId: this.activeBatchId,
      activeRunCode: this.activeRunCode,
      stage: this.stage,
      stageDetail: this.stageDetail,
      gateConnected: this.gateConnected || this.gateServerClients.size > 0,
      gateClientCount: this.gateServerClients.size,
      logConnected: this.logConnected,
      httpApiUrl: this.httpApiUrl,
      logLineCount: this.logStore?.lineCount ?? 0,
      sessionCount: this.logStore?.sessionCount ?? 0,
      pendingCases: this.pendingCases.size,
      receivedCount: this.receivedCount,
      passedCount: this.passedCount,
      failedCount: this.failedCount,
      timeoutCount: this.timeoutCount,
      startedAt: this.startedAt,
    }
  }

  private emitStatus(): void {
    this.emit('status', this.getStatus())
  }

  private log(level: SystemLogEntry['level'], source: string, message: string): void {
    apiClient
      .insertSystemLog({
        batchId: this.activeBatchId,
        level,
        source,
        message,
      })
      .then((entry) => this.emit('log', entry))
      .catch(() => {
        // ignore if server unreachable
      })
  }

  async start(batchName?: string): Promise<EngineStatus> {
    if (this.running) return this.getStatus()

    const config = await apiClient.getConfig()
    this.config = config
    this.running = true
    this.mode = 'live'
    this.startedAt = new Date().toISOString()
    this.receivedCount = 0
    this.passedCount = 0
    this.failedCount = 0
    this.timeoutCount = 0

    this.logStore = new LogStore(config.patterns, config.run)
    this.judge = new Judge(config.judge)
    this.rounds = new RoundTracker({
      keys: config.gate.roundKeys,
      split: config.gate.roundSplit,
      ttlSeconds: config.gate.roundTtlSeconds,
    })

    const runCode = makeRunCode('LIVE')
    const batch = await apiClient.createBatch({
      runCode,
      name: batchName?.trim() || `实时测试 ${runCode}`,
      gateMode: config.gate.mode,
      logSource: config.logs.source,
      configSnapshot: config,
    })
    this.activeBatchId = batch.id
    this.activeRunCode = batch.runCode
    this.emit('batch', batch)

    this.stage = '等待用例'
    this.stageDetail = `闸机(${config.gate.mode}) · 日志(${config.logs.source})`
    this.log('INFO', 'engine', `测试批次 ${batch.runCode} 已启动（闸机=${config.gate.mode}，日志=${config.logs.source}）`)

    // Start Gate channels
    if (config.gate.mode === 'client' || config.gate.mode === 'both') {
      this.connectGateClient(config)
    }
    if (config.gate.mode === 'server' || config.gate.mode === 'both') {
      this.startGateServer(config)
    }

    // Start Log channel
    if (config.logs.source === 'file') {
      this.startLogFileTailer(config)
    } else {
      this.connectLogClient(config)
    }

    // Start HTTP Case Ingestion API
    if (config.run.httpApiEnabled) {
      this.startHttpApi(config)
    }

    // Poll pending cases every 250ms
    this.resolveInterval = setInterval(() => this.pollPendingCases(), 250)
    this.emitStatus()
    return this.getStatus()
  }

  async stop(): Promise<EngineStatus> {
    if (!this.running) return this.getStatus()

    this.running = false
    for (const t of this.selfTestTimers) clearTimeout(t)
    this.selfTestTimers = []

    if (this.resolveInterval) {
      clearInterval(this.resolveInterval)
      this.resolveInterval = null
    }

    // Settle or cancel remaining pending cases
    for (const [key, pending] of this.pendingCases.entries()) {
      clearTimeout(pending.timer)
      this.finalizeCase(key, pending, true)
    }
    this.pendingCases.clear()

    if (this.gateReconnectTimer) {
      clearTimeout(this.gateReconnectTimer)
      this.gateReconnectTimer = null
    }
    if (this.gateWsClient) {
      try {
        this.gateWsClient.close()
      } catch {
        // ignore
      }
      this.gateWsClient = null
    }
    if (this.gateWsServer) {
      for (const c of this.gateServerClients) {
        try {
          c.close()
        } catch {
          // ignore
        }
      }
      this.gateServerClients.clear()
      try {
        this.gateWsServer.close()
      } catch {
        // ignore
      }
      this.gateWsServer = null
    }

    if (this.logReconnectTimer) {
      clearTimeout(this.logReconnectTimer)
      this.logReconnectTimer = null
    }
    if (this.logWsClient) {
      try {
        this.logWsClient.close()
      } catch {
        // ignore
      }
      this.logWsClient = null
    }
    if (this.fileTailTimer) {
      clearInterval(this.fileTailTimer)
      this.fileTailTimer = null
    }

    if (this.httpServer) {
      try {
        this.httpServer.close()
      } catch {
        // ignore
      }
      this.httpServer = null
      this.httpApiUrl = null
    }

    this.gateConnected = false
    this.logConnected = false

    if (this.activeBatchId !== null) {
      const finished = await apiClient
        .finishBatch(this.activeBatchId, this.mode === 'selftest' ? 'completed' : 'stopped')
        .catch(() => null)
      if (finished) this.emit('batch', finished)
      this.log('INFO', 'engine', `测试批次 ${this.activeRunCode} 已停止，共判定 ${this.receivedCount} 条用例`)
    }

    this.mode = 'idle'
    this.stage = '已停止'
    this.stageDetail = '测试已停止，所有数据已同步至共享后端服务器'
    this.emitStatus()
    return this.getStatus()
  }

  // ---------------- Built-in Self-Test Simulator ----------------
  async runSelfTest(): Promise<EngineStatus> {
    if (this.running) {
      await this.stop()
    }

    const config = await apiClient.getConfig()
    // Use shorter timeout (4s) for self-test timeout case so user sees full flow in ~8 seconds
    const selfTestConfig: AppConfig = {
      ...config,
      run: { ...config.run, caseTimeoutSeconds: 4 },
    }
    this.config = selfTestConfig
    this.running = true
    this.mode = 'selftest'
    this.startedAt = new Date().toISOString()
    this.receivedCount = 0
    this.passedCount = 0
    this.failedCount = 0
    this.timeoutCount = 0
    this.gateConnected = true
    this.logConnected = true

    this.logStore = new LogStore(selfTestConfig.patterns, selfTestConfig.run)
    this.judge = new Judge(selfTestConfig.judge)
    this.rounds = new RoundTracker({
      keys: selfTestConfig.gate.roundKeys,
      split: selfTestConfig.gate.roundSplit,
      ttlSeconds: selfTestConfig.gate.roundTtlSeconds,
    })

    const runCode = makeRunCode('SELF')
    const batch = await apiClient.createBatch({
      runCode,
      name: `一键自测模拟 ${runCode}`,
      gateMode: 'simulator',
      logSource: 'simulator',
      configSnapshot: selfTestConfig,
      notes: '内置全链路自测：覆盖正常通行、预期拦截一致、预期允许但被拒、失败节点不一致、日志超时 5 类典型场景',
    })
    this.activeBatchId = batch.id
    this.activeRunCode = batch.runCode
    this.emit('batch', batch)

    this.stage = '自测运行中'
    this.stageDetail = '正在模拟闸机抓拍与流程引擎日志，自动执行判定并写入数据库…'
    this.log('INFO', 'simulator', `启动内置全链路自测批次 ${batch.runCode}（5 条典型场景用例）`)

    this.resolveInterval = setInterval(() => this.pollPendingCases(), 200)
    this.emitStatus()

    const scenarios: {
      delayMs: number
      payload: Record<string, unknown>
      logs: [number, string][]
    }[] = [
      {
        delayMs: 200,
        payload: {
          type: 'gate.capture',
          case_id: 'C-PASS-001',
          serial: 'SN-001_1',
          expected: { allow_pass: true, failed_node: null },
          capture: {
            passenger_name: '张三',
            cert_type: 'ID_CARD',
            cert_no: '110101199003071234',
            gate_id: 'G01',
            photo_base64: makeMockPhotoSvg('张三', '#1d4ed8'),
          },
        },
        logs: [
          [150, '[流程引擎]创建新会话S0001'],
          [300, '[流程引擎]旅客[张三]证件读取成功 证件号=110101199003071234'],
          [450, '[证件核验]会话ID[S0001]-节点证件核验开始 证件号=110101199003071234'],
          [600, '[人脸比对]会话ID[S0001]-节点人脸比对通过 相似度=0.94'],
          [750, '[通行控制]旅客[张三]离开通道 闸机开门方向=入口'],
        ],
      },
      {
        delayMs: 1300,
        payload: {
          type: 'gate.capture',
          case_id: 'C-PASS-002',
          serial: 'SN-002_1',
          expected: { allow_pass: false, failed_node: '黑名单布控' },
          capture: {
            passenger_name: '陈七',
            cert_type: 'PASSPORT',
            cert_no: 'E45829103',
            gate_id: 'G01',
            photo_base64: makeMockPhotoSvg('陈七', '#0f766e'),
          },
        },
        logs: [
          [150, '[流程引擎]创建新会话S0002'],
          [300, '[流程引擎]旅客[陈七]证件读取成功 证件号=E45829103'],
          [500, '[风控引擎]会话ID[S0002]-节点黑名单布控执行失败 原因=命中高风险布控名单'],
          [650, '[流程引擎]会话ID[S0002] 流程结束 结果=拒绝通行'],
        ],
      },
      {
        delayMs: 2400,
        payload: {
          type: 'gate.capture',
          case_id: 'C-DENY-003',
          serial: 'SN-003_1',
          expected: { allow_pass: true, failed_node: null },
          capture: {
            passenger_name: '李四',
            cert_type: 'ID_CARD',
            cert_no: '310101198805124321',
            gate_id: 'G01',
            photo_base64: makeMockPhotoSvg('李四', '#b91c1c'),
          },
        },
        logs: [
          [150, '[流程引擎]创建新会话S0003'],
          [300, '[流程引擎]旅客[李四]证件读取成功 证件号=310101198805124321'],
          [500, '[证件核验]会话ID[S0003]-节点证件核验执行失败 原因=证件有效期已过期 expire=2020-01-01'],
          [650, '[流程引擎]会话ID[S0003] 流程结束 结果=拒绝通行'],
        ],
      },
      {
        delayMs: 3500,
        payload: {
          type: 'gate.capture',
          case_id: 'C-NODE-004',
          serial: 'SN-004_1',
          expected: { allow_pass: false, failed_node: '人脸比对' },
          capture: {
            passenger_name: '王五',
            cert_type: 'ID_CARD',
            cert_no: '440301199512258765',
            gate_id: 'G02',
            photo_base64: makeMockPhotoSvg('王五', '#b45309'),
          },
        },
        logs: [
          [150, '[流程引擎]创建新会话S0004'],
          [300, '[流程引擎]旅客[王五]证件读取成功 证件号=440301199512258765'],
          [500, '[证件核验]会话ID[S0004]-节点证件核验执行失败 原因=证件号码校验位错误'],
          [650, '[流程引擎]会话ID[S0004] 流程结束 结果=拒绝通行'],
        ],
      },
      {
        delayMs: 4600,
        payload: {
          type: 'gate.capture',
          case_id: 'C-TIMEOUT-005',
          serial: 'SN-005_1',
          expected: { allow_pass: true, failed_node: null },
          capture: {
            passenger_name: '赵六',
            cert_type: 'ID_CARD',
            cert_no: '500101199207081234',
            gate_id: 'G02',
            photo_base64: makeMockPhotoSvg('赵六', '#6d28d9'),
          },
        },
        logs: [
          [150, '[流程引擎]创建新会话S0005'],
          [350, '[流程引擎]旅客[赵六]证件读取成功 证件号=500101199207081234'],
          [550, '[人脸比对]会话ID[S0005]-节点人脸比对开始 等待摄像头取流…'],
        ],
      },
    ]

    for (const sc of scenarios) {
      const t = setTimeout(() => {
        if (!this.running) return
        this.handleGateRawMessage(sc.payload)
        for (const [offset, line] of sc.logs) {
          const lt = setTimeout(() => {
            if (!this.running) return
            this.ingestLogText(line)
          }, offset)
          this.selfTestTimers.push(lt)
        }
      }, sc.delayMs)
      this.selfTestTimers.push(t)
    }

    // Auto-finish self-test after timeout case completes (~9.5s)
    const endTimer = setTimeout(() => {
      if (this.running && this.mode === 'selftest') {
        void this.stop()
      }
    }, 9500)
    this.selfTestTimers.push(endTimer)

    return this.getStatus()
  }

  // ---------------- Manual Case Push ----------------
  pushManualCase(payload: Record<string, unknown>): { accepted: boolean; caseId?: string; error?: string } {
    if (!this.running) {
      return { accepted: false, error: '测试引擎未启动，请先启动测试后再推送用例' }
    }
    try {
      const caseId = this.handleGateRawMessage(payload)
      return { accepted: true, caseId: caseId ?? undefined }
    } catch (err) {
      return { accepted: false, error: err instanceof Error ? err.message : String(err) }
    }
  }

  // ---------------- Gate Channels ----------------
  private connectGateClient(config: AppConfig, delaySec = config.gate.reconnectSeconds): void {
    if (!this.running || !config.gate.url) return
    try {
      const ws = new WebSocket(config.gate.url)
      this.gateWsClient = ws
      ws.on('open', () => {
        this.gateConnected = true
        this.log('INFO', 'gate-client', `已连接闸机通道：${config.gate.url}`)
        if (config.gate.subscription) {
          ws.send(config.gate.subscription)
        }
        this.emitStatus()
      })
      ws.on('message', (data) => {
        const text = data.toString('utf-8')
        try {
          const parsed = JSON.parse(text)
          const caseId = this.handleGateRawMessage(parsed)
          if (config.gate.sendAck && caseId) {
            ws.send(JSON.stringify(buildAck(caseId, 'accepted')))
          }
        } catch (err) {
          this.log('WARN', 'gate-client', `解析闸机消息失败：${err instanceof Error ? err.message : String(err)}`)
        }
      })
      ws.on('close', () => {
        this.gateConnected = false
        this.emitStatus()
        if (this.running) {
          const nextDelay = Math.min(config.gate.maxReconnectSeconds, delaySec * 1.5)
          this.gateReconnectTimer = setTimeout(() => this.connectGateClient(config, nextDelay), delaySec * 1000)
        }
      })
      ws.on('error', (err) => {
        this.log('WARN', 'gate-client', `闸机连接异常（${config.gate.url}）：${err.message}`)
      })
    } catch (err) {
      this.log('ERROR', 'gate-client', `创建闸机客户端失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  private startGateServer(config: AppConfig): void {
    try {
      const wss = new WebSocketServer({
        host: config.gate.listenHost || '0.0.0.0',
        port: config.gate.listenPort || 8765,
        path: config.gate.listenPath || '/gate',
      })
      this.gateWsServer = wss
      wss.on('listening', () => {
        this.log(
          'INFO',
          'gate-server',
          `闸机 WS 服务端监听：ws://${config.gate.listenHost}:${config.gate.listenPort}${config.gate.listenPath}`
        )
        this.emitStatus()
      })
      wss.on('connection', (ws) => {
        this.gateServerClients.add(ws)
        this.log('INFO', 'gate-server', `新闸机客户端接入（当前 ${this.gateServerClients.size} 个）`)
        this.emitStatus()
        ws.on('message', (data) => {
          try {
            const parsed = JSON.parse(data.toString('utf-8'))
            const caseId = this.handleGateRawMessage(parsed)
            if (config.gate.sendAck && caseId) {
              ws.send(JSON.stringify(buildAck(caseId, 'accepted')))
            }
          } catch (err) {
            this.log('WARN', 'gate-server', `解析消息失败：${err instanceof Error ? err.message : String(err)}`)
          }
        })
        ws.on('close', () => {
          this.gateServerClients.delete(ws)
          this.emitStatus()
        })
      })
      wss.on('error', (err) => {
        this.log('ERROR', 'gate-server', `闸机服务端端口异常：${err.message}`)
      })
    } catch (err) {
      this.log('ERROR', 'gate-server', `启动闸机服务端失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  private startHttpApi(config: AppConfig): void {
    const server = http.createServer((req, res) => {
      if (req.method === 'GET' && req.url === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ ok: true, status: this.getStatus() }))
        return
      }
      if (req.method === 'POST' && req.url === '/api/case') {
        let body = ''
        req.on('data', (chunk) => {
          body += chunk.toString('utf-8')
        })
        req.on('end', () => {
          try {
            const parsed = JSON.parse(body)
            const items = Array.isArray(parsed) ? parsed : [parsed]
            const caseIds: string[] = []
            for (const item of items) {
              const cid = this.handleGateRawMessage(item)
              if (cid) caseIds.push(cid)
            }
            res.writeHead(200, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ accepted: true, count: caseIds.length, caseIds }))
          } catch (err) {
            res.writeHead(400, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ accepted: false, error: err instanceof Error ? err.message : String(err) }))
          }
        })
        return
      }
      res.writeHead(404, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'Not Found' }))
    })
    server.on('error', (err) => {
      this.log('WARN', 'http-api', `HTTP 推用例接口启动失败：${err.message}`)
    })
    server.listen(config.run.httpApiPort || 8767, config.run.httpApiHost || '127.0.0.1', () => {
      this.httpApiUrl = `http://${config.run.httpApiHost || '127.0.0.1'}:${config.run.httpApiPort || 8767}/api/case`
      this.log('INFO', 'http-api', `HTTP 推用例接口就绪：POST ${this.httpApiUrl}`)
      this.emitStatus()
    })
    this.httpServer = server
  }

  // ---------------- Log Channels ----------------
  private connectLogClient(config: AppConfig, delaySec = 2): void {
    if (!this.running || !config.logs.url) return
    try {
      const ws = new WebSocket(config.logs.url)
      this.logWsClient = ws
      ws.on('open', () => {
        this.logConnected = true
        this.log('INFO', 'log-client', `已连接日志通道：${config.logs.url}`)
        if (config.logs.subscription) {
          ws.send(config.logs.subscription)
        }
        this.emitStatus()
      })
      ws.on('message', (data) => {
        const text = data.toString('utf-8')
        this.handleLogWsFrame(text, config)
      })
      ws.on('close', () => {
        this.logConnected = false
        this.emitStatus()
        if (this.running) {
          const nextDelay = Math.min(10, delaySec * 1.5)
          this.logReconnectTimer = setTimeout(() => this.connectLogClient(config, nextDelay), delaySec * 1000)
        }
      })
      ws.on('error', (err) => {
        this.log('WARN', 'log-client', `日志连接异常（${config.logs.url}）：${err.message}`)
      })
    } catch (err) {
      this.log('ERROR', 'log-client', `创建日志客户端失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  private handleLogWsFrame(raw: string, config: AppConfig): void {
    if (config.logs.messageFormat !== 'text') {
      try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
          if (Array.isArray(parsed.lines)) {
            for (const item of parsed.lines) {
              if (typeof item === 'string') this.ingestLogText(item)
            }
            return
          }
          for (const field of config.logs.textFields) {
            if (typeof parsed[field] === 'string') {
              this.ingestLogText(parsed[field])
              return
            }
          }
        }
      } catch {
        // Fallback to plain text
      }
    }
    this.ingestLogText(raw)
  }

  private startLogFileTailer(config: AppConfig): void {
    const resolveTodayPath = () => {
      const built = buildDynamicLogPath(config.logs, new Date())
      if (!built) return ''
      return path.isAbsolute(built) ? built : path.resolve(process.cwd(), built)
    }

    let currentFilePath = resolveTodayPath()
    if (!currentFilePath) {
      this.log('WARN', 'log-file', '未配置日志文件路径或前缀，请在「系统配置」中设置')
      return
    }
    this.fileOffset = 0
    this.fileRemainder = Buffer.alloc(0)

    if (fs.existsSync(currentFilePath)) {
      const stat = fs.statSync(currentFilePath)
      this.fileOffset = config.logs.fromBeginning ? 0 : stat.size
      this.logConnected = true
      this.log('INFO', 'log-file', `开始跟随日志文件：${currentFilePath}（编码=${config.logs.fileEncoding}）`)
    } else {
      this.log('WARN', 'log-file', `等待日志文件出现：${currentFilePath}`)
    }
    this.emitStatus()

    const poll = () => {
      if (!this.running) return
      // Support midnight date rollover automatically
      const nextPath = resolveTodayPath()
      if (nextPath && nextPath !== currentFilePath) {
        currentFilePath = nextPath
        this.fileOffset = 0
        this.fileRemainder = Buffer.alloc(0)
        this.logConnected = false
        this.log('INFO', 'log-file', `日期切换，自动跟随新日志文件：${currentFilePath}`)
      }
      if (!currentFilePath || !fs.existsSync(currentFilePath)) return
      try {
        const stat = fs.statSync(currentFilePath)
        if (!this.logConnected) {
          this.logConnected = true
          this.fileOffset = 0
          this.log('INFO', 'log-file', `检测到日志文件已创建：${currentFilePath}`)
          this.emitStatus()
        }
        if (stat.size < this.fileOffset && config.logs.followRotation) {
          this.fileOffset = 0
          this.fileRemainder = Buffer.alloc(0)
          this.log('INFO', 'log-file', `检测到日志轮转/截断，重置读取位置`)
        }
        if (stat.size > this.fileOffset) {
          const toRead = Math.min(stat.size - this.fileOffset, 512 * 1024)
          const fd = fs.openSync(currentFilePath, 'r')
          const buf = Buffer.alloc(toRead)
          const bytesRead = fs.readSync(fd, buf, 0, toRead, this.fileOffset)
          fs.closeSync(fd)
          this.fileOffset += bytesRead

          const combined = Buffer.concat([this.fileRemainder, buf.subarray(0, bytesRead)])
          const lastNl = combined.lastIndexOf(0x0a)
          if (lastNl >= 0) {
            const complete = combined.subarray(0, lastNl + 1)
            this.fileRemainder = combined.subarray(lastNl + 1)
            const text = this.decodeBuffer(complete, config.logs.fileEncoding)
            this.ingestLogText(text)
          } else {
            this.fileRemainder = combined
          }
        }
      } catch (err) {
        this.log('WARN', 'log-file', `读取日志文件失败：${err instanceof Error ? err.message : String(err)}`)
      }
    }

    this.fileTailTimer = setInterval(poll, Math.max(200, config.logs.pollIntervalMs || 500))
    poll()
  }

  private decodeBuffer(buf: Buffer, encoding: 'auto' | 'utf-8' | 'gbk'): string {
    if (encoding === 'gbk') return iconv.decode(buf, 'gbk')
    if (encoding === 'utf-8') return buf.toString('utf-8')
    const utf8 = buf.toString('utf-8')
    if (utf8.includes('\uFFFD')) {
      return iconv.decode(buf, 'gbk')
    }
    return utf8
  }

  private ingestLogText(rawText: string): void {
    if (!this.logStore || !this.config) return
    const lines = rawText.split(/\r?\n/)
    const valid: string[] = []
    for (const line of lines) {
      const { text } = sanitizeLogLine(line, this.config.logs.maxFieldChars, this.config.logs.dropUndecodable)
      if (text !== null) valid.push(text)
    }
    if (valid.length > 0) {
      this.logStore.appendMessage(valid.join('\n'))
    }
  }

  // ---------------- Case Pipeline ----------------
  private handleGateRawMessage(rawObj: unknown): string | null {
    if (!this.config || !this.logStore || !this.rounds || !this.running) return null
    if (!rawObj || typeof rawObj !== 'object' || Array.isArray(rawObj)) return null

    const mapped = applyFieldMap(rawObj as Record<string, unknown>, this.config.gate.fieldMap)
    const { key: rKey } = this.rounds.observe(mapped, this.logStore.nextIndex)

    let capture: GateCapture
    try {
      capture = parseGateMessage(mapped)
    } catch (err) {
      if (err instanceof NotACaseMessage) return null
      if (err instanceof ProtocolError) {
        this.log('WARN', 'protocol', err.message)
        return null
      }
      throw err
    }

    capture.roundKey = rKey
    capture.serial = serialValue(mapped, this.config.gate.roundKeys)
    capture.logMarker = this.rounds.markerFor(rKey, this.logStore.nextIndex)
    capture.gateMessages = this.rounds.framesFor(rKey)

    const pendingKey = `${capture.caseId}#${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const timeoutMs = Math.max(2, this.config.run.caseTimeoutSeconds) * 1000
    const deadlineMs = Date.now() + timeoutMs
    const timer = setTimeout(() => {
      const p = this.pendingCases.get(pendingKey)
      if (p) {
        this.pendingCases.delete(pendingKey)
        this.finalizeCase(pendingKey, p, true)
      }
    }, timeoutMs)

    this.pendingCases.set(pendingKey, { capture, deadlineMs, timer })
    this.receivedCount++
    this.stage = `判定中 (${capture.caseId})`
    this.log(
      'INFO',
      'gate',
      `收到用例 ${capture.caseId}（预期：${capture.expected.allowPass ? '允许通行' : `拦截/${capture.expected.failedNode ?? '未指定'}`}）`
    )
    this.emitStatus()
    return capture.caseId
  }

  private pollPendingCases(): void {
    if (!this.logStore || !this.config || !this.judge) return
    for (const [key, pending] of [...this.pendingCases.entries()]) {
      const keys = getPassengerCandidates(pending.capture, this.config.judge.passengerKeys)
      const resolution = this.logStore.resolve(keys, pending.capture.logMarker, pending.capture.sessionIdHint)
      if (resolution.settled) {
        clearTimeout(pending.timer)
        this.pendingCases.delete(key)
        this.finalizeCase(key, pending, false, resolution)
      }
    }
  }

  private async finalizeCase(
    _key: string,
    pending: PendingCase,
    timedOut: boolean,
    preResolution?: ReturnType<LogStore['resolve']>
  ): Promise<void> {
    if (!this.logStore || !this.judge || !this.config || this.activeBatchId === null) return

    const { capture } = pending
    const passengerKeys = getPassengerCandidates(capture, this.config.judge.passengerKeys)
    const resolution =
      preResolution ?? this.logStore.resolve(passengerKeys, capture.logMarker, capture.sessionIdHint)

    const verdict = this.judge.evaluate(capture.expected, resolution, timedOut)
    const logLines = resolution.sid
      ? this.logStore.sliceSession(resolution.sid, passengerKeys, this.config.run.sliceIncludePassengerLines)
      : this.logStore.sliceAround(capture.logMarker)
    const logSlice = linesToText(logLines)

    if (verdict.status === 'PASS') this.passedCount++
    else if (verdict.status === 'TIMEOUT') this.timeoutCount++
    else this.failedCount++

    const cap = capture.capture
    const passengerName = String(cap.passenger_name ?? cap.passengerName ?? cap.name ?? '')
    const certType = String(cap.cert_type ?? cap.certType ?? '')
    const certNo = String(cap.cert_no ?? cap.certNo ?? cap.id_no ?? '')
    const gateId = String(cap.gate_id ?? cap.gateId ?? capture.raw.gate_id ?? '')
    const photoData = extractPhotoData(cap)

    try {
      const { record, batch } = await apiClient.insertCase({
        batchId: this.activeBatchId,
        caseId: capture.caseId,
        serial: capture.serial,
        roundKey: capture.roundKey,
        passengerName,
        certType,
        certNo,
        gateId,
        photoData,
        expectedAllow: capture.expected.allowPass,
        expectedNode: capture.expected.failedNode,
        actualAllow: verdict.actualAllow,
        actualNode: verdict.actualNode,
        actualNodes: verdict.actualNodes,
        verdictStatus: verdict.status,
        passed: verdict.passed,
        timedOut: verdict.timedOut,
        contradictory: resolution.contradictory,
        sessionId: resolution.sid,
        reasons: verdict.reasons,
        resolutionNote: verdict.resolutionNote,
        capture: capture.capture,
        raw: capture.raw,
        gateMessages: capture.gateMessages,
        logSlice,
        analysisStatus: verdict.passed ? 'none' : 'pending',
        capturedAt: capture.capturedAt,
        judgedAt: new Date().toISOString(),
      })

      this.rounds?.forget(capture.roundKey)
      this.log(
        verdict.passed ? 'INFO' : 'WARN',
        'judge',
        `[${verdict.status}] ${record.caseId}（${passengerName || '未知旅客'}）：${verdict.reasons[0] ?? ''}`
      )
      this.emit('case', record)
      if (batch) this.emit('batch', batch)
    } catch (err) {
      this.log('ERROR', 'sync', `上报用例 ${capture.caseId} 到共享后端失败：${err instanceof Error ? err.message : String(err)}`)
    }
    this.stage = this.pendingCases.size > 0 ? `判定中（剩余 ${this.pendingCases.size} 条）` : '等待用例'
    this.emitStatus()
  }

  // ---------------- Failure Analysis (DSH / Built-in Heuristic) ----------------
  async analyzeCase(caseDbId: number): Promise<TestCaseRecord | null> {
    const record = await apiClient.getCase(caseDbId)
    if (!record) return null
    const config = await apiClient.getConfig()

    const runningRec = await apiClient.updateCaseAnalysis(caseDbId, 'running', null)
    if (runningRec) this.emit('case', runningRec)

    // If DSH is enabled, invoke configured CLI; otherwise run fast local structured diagnostic
    if (config.dsh.enabled && config.dsh.command) {
      try {
        const result = await this.runExternalDsh(record, config)
        const updated = await apiClient.updateCaseAnalysis(caseDbId, 'done', result)
        if (updated) this.emit('case', updated)
        return updated
      } catch (err) {
        const fallback = this.buildLocalDiagnostic(record, `外部 DSH 调用失败（${err instanceof Error ? err.message : String(err)}），已自动回退本地规则诊断`)
        const updated = await apiClient.updateCaseAnalysis(caseDbId, 'done', fallback)
        if (updated) this.emit('case', updated)
        return updated
      }
    }

    const diagnostic = this.buildLocalDiagnostic(record)
    const updated = await apiClient.updateCaseAnalysis(caseDbId, 'done', diagnostic)
    if (updated) this.emit('case', updated)
    return updated
  }

  private runExternalDsh(record: TestCaseRecord, config: AppConfig): Promise<AnalysisResult> {
    return new Promise((resolve, reject) => {
      const prompt = `请分析闸机自动化测试失败用例 ${record.caseId}：预期=${record.expectedAllow ? '允许' : `拒绝(${record.expectedNode})`}，实际=${record.actualAllow === null ? '超时' : record.actualAllow ? '允许' : `拒绝(${record.actualNode})`}，判定原因=${record.reasons.join('；')}。日志切片：\n${record.logSlice.slice(0, 3000)}`
      const child = spawn(config.dsh.command, ['--profile', config.dsh.profile || 'headless', prompt], {
        shell: process.platform === 'win32',
        timeout: Math.max(30, config.dsh.timeoutSeconds || 120) * 1000,
      })
      let stdout = ''
      let stderr = ''
      child.stdout.on('data', (d) => (stdout += d.toString()))
      child.stderr.on('data', (d) => (stderr += d.toString()))
      child.on('error', reject)
      child.on('close', (code) => {
        if (code !== 0 && !stdout.trim()) {
          reject(new Error(stderr.trim() || `exit code ${code}`))
          return
        }
        resolve({
          status: 'done',
          rootCause: stdout.trim().slice(0, 300) || '已完成 DSH 分析',
          possibleCauses: [record.reasons[0] ?? '详见 DSH 原始输出'],
          solutions: ['请结合 DSH 输出与源码核对对应节点逻辑'],
          codeChanges: [],
          confidence: 'medium',
          summary: stdout.trim().slice(0, 1200),
          analyzedAt: new Date().toISOString(),
        })
      })
    })
  }

  private buildLocalDiagnostic(record: TestCaseRecord, extraNote?: string): AnalysisResult {
    const logLines = record.logSlice.split('\n').filter(Boolean)
    const errorLine = logLines.find((l) => l.includes('失败') || l.includes('原因=') || l.includes('错误')) ?? ''

    if (record.verdictStatus === 'TIMEOUT') {
      return {
        status: 'done',
        rootCause: '流程在超时窗口内未产生终态日志（既未节点失败也未离开通道）',
        possibleCauses: [
          '上游设备/算法服务（如人脸比对摄像头取流、核验接口）阻塞或无响应',
          '待测系统日志文案发生变更，当前 patterns.passengerExit 正则未能匹配到放行日志',
          '超时阈值（caseTimeoutSeconds）设置偏小，流程尚未执行完毕',
        ],
        solutions: [
          '检查日志切片最后一行停留的业务节点，排查对应服务耗时或死锁',
          '在「系统配置 -> 判定与正则」中核对日志关键字并适当调大单用例超时时间',
        ],
        codeChanges: [],
        confidence: 'medium',
        summary: `${extraNote ? `${extraNote}。` : ''}用例 ${record.caseId} 在会话 ${record.sessionId ?? '未知'} 中超时未收敛，最后一条日志：${logLines[logLines.length - 1] ?? '无'}。`,
        analyzedAt: new Date().toISOString(),
      }
    }

    if (record.expectedAllow && record.actualAllow === false) {
      return {
        status: 'done',
        rootCause: `预期放行但在节点「${record.actualNode ?? '未知'}」被拦截${errorLine ? `：${errorLine}` : ''}`,
        possibleCauses: [
          errorLine ? `日志直接报错线索：${errorLine}` : '证件或生物特征校验条件过严导致误拒',
          '测试用例采集数据（如证件有效期、阈值配置）与待测环境基线不一致',
        ],
        solutions: [
          `定位「${record.actualNode ?? '拦截节点'}」的校验分支与阈值配置`,
          '核对测试证件数据是否满足当前环境的合法性要求',
        ],
        codeChanges: [],
        confidence: 'high',
        summary: `${extraNote ? `${extraNote}。` : ''}用例 ${record.caseId} 预期允许通行，但实际在「${record.actualNode ?? '未知'}」节点执行失败。`,
        analyzedAt: new Date().toISOString(),
      }
    }

    return {
      status: 'done',
      rootCause: `实际拦截节点「${record.actualNode ?? '无'}」与预期节点「${record.expectedNode ?? '无'}」不一致`,
      possibleCauses: [
        errorLine ? `前置节点提前失败：${errorLine}` : '流程编排中前置校验节点优先触发拦截',
        '日志节点名称与用例预期节点名称存在别名差异（可通过 nodeAliases 映射）',
      ],
      solutions: [
        '检查流程节点执行顺序，确认前置节点是否误拦截',
        '若仅是命名差异，在「系统配置 -> 节点别名映射」中添加对应映射规则',
      ],
      codeChanges: [],
      confidence: 'high',
      summary: `${extraNote ? `${extraNote}。` : ''}用例 ${record.caseId} 预期在「${record.expectedNode ?? '无'}」拦截，实际在「${record.actualNode ?? '无'}」拦截。`,
      analyzedAt: new Date().toISOString(),
    }
  }
}

export const engine = new AutoTestEngine()
