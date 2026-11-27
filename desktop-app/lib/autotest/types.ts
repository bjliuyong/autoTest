export type GateMode = 'client' | 'server' | 'both'
export type LogSource = 'ws' | 'file'
export type VerdictStatus = 'PASS' | 'FAIL' | 'TIMEOUT' | 'ERROR'
export type AnalysisStatus = 'none' | 'pending' | 'running' | 'done' | 'failed' | 'skipped'
export type BatchStatus = 'running' | 'completed' | 'stopped'

export interface AppConfig {
  run: {
    caseTimeoutSeconds: number
    maxBufferLines: number
    captureToleranceLines: number
    sliceIncludePassengerLines: boolean
    maxSliceLines: number
    sliceLeadLines: number
    singleSessionFallback: boolean
    httpApiEnabled: boolean
    httpApiHost: string
    httpApiPort: number
  }
  gate: {
    mode: GateMode
    url: string
    listenHost: string
    listenPort: number
    listenPath: string
    reconnectSeconds: number
    maxReconnectSeconds: number
    sendAck: boolean
    subscription: string | null
    fieldMap: Record<string, string>
    roundKeys: string[]
    roundSplit: string | null
    roundTtlSeconds: number
  }
  logs: {
    source: LogSource
    url: string
    messageFormat: 'auto' | 'text' | 'json'
    textFields: string[]
    subscription: string | null
    filePath: string
    filePrefix: string
    fileDateFormat: string
    fileExtension: string
    fileEncoding: 'auto' | 'utf-8' | 'gbk'
    pollIntervalMs: number
    fromBeginning: boolean
    followRotation: boolean
    maxFieldChars: number
    dropUndecodable: boolean
  }
  patterns: {
    sessionCreate: string[]
    nodeFailure: string[]
    passengerExit: string[]
    passengerMarker: string
  }
  judge: {
    nodeAliases: Record<string, string>
    passengerKeys: string[]
    contradictionAsFailure: boolean
  }
  dsh: {
    enabled: boolean
    command: string
    profile: string
    timeoutSeconds: number
    codeAnalysis: boolean
    codePath: string
    codeSubdir: string
    allowCodeFix: boolean
    branchTemplate: string
  }
}

export const DEFAULT_APP_CONFIG: AppConfig = {
  run: {
    caseTimeoutSeconds: 30,
    maxBufferLines: 200000,
    captureToleranceLines: 5,
    sliceIncludePassengerLines: true,
    maxSliceLines: 1500,
    sliceLeadLines: 60,
    singleSessionFallback: true,
    httpApiEnabled: true,
    httpApiHost: '127.0.0.1',
    httpApiPort: 8767,
  },
  gate: {
    mode: 'both',
    url: 'ws://127.0.0.1:1880',
    listenHost: '0.0.0.0',
    listenPort: 8765,
    listenPath: '/gate',
    reconnectSeconds: 2,
    maxReconnectSeconds: 10,
    sendAck: false,
    subscription: null,
    fieldMap: {},
    roundKeys: [
      'serial',
      'Serial',
      'serialNo',
      'serial_no',
      'requestId',
      'request_id',
      'capture.serial',
      'capture.cert_no',
      'cert_no',
    ],
    roundSplit: '_',
    roundTtlSeconds: 600,
  },
  logs: {
    source: 'ws',
    url: 'ws://127.0.0.1:8766/logs',
    messageFormat: 'auto',
    textFields: ['line', 'text', 'message', 'msg', 'content', 'log'],
    subscription: null,
    filePath: '',
    filePrefix: '',
    fileDateFormat: 'YYYY-MM-DD',
    fileExtension: '.log',
    fileEncoding: 'auto',
    pollIntervalMs: 500,
    fromBeginning: false,
    followRotation: true,
    maxFieldChars: 100,
    dropUndecodable: true,
  },
  patterns: {
    sessionCreate: [
      '\\[流程引擎\\]\\s*创建新会话[：:\\s]*\\[?(?<sid>[^\\s\\[\\]，,。;；]+)\\]?',
    ],
    nodeFailure: [
      '会话ID\\[(?<sid>[^\\]]+)\\]\\s*[-—－–:：]\\s*节点\\s*(?<node>.+?)\\s*执行失败',
    ],
    passengerExit: [
      '旅客\\[(?<key>[^\\]]+)\\][^\\n]{0,16}?离开通道',
      '旅客\\[(?<key>[^\\]]+)\\][^\\n]{0,16}?(?:已放行|开闸成功|闸门已开)',
      '离开通道',
      '(?:已放行|开闸成功|闸门已开)',
    ],
    passengerMarker: '旅客\\[(?<key>[^\\]]+)\\]',
  },
  judge: {
    nodeAliases: {},
    passengerKeys: ['cert_no', 'case_id', 'passenger_name', 'id_no', 'name', 'passengerName', 'certNo'],
    contradictionAsFailure: true,
  },
  dsh: {
    enabled: false,
    command: 'dsh',
    profile: 'headless',
    timeoutSeconds: 300,
    codeAnalysis: true,
    codePath: '',
    codeSubdir: '',
    allowCodeFix: false,
    branchTemplate: '{time}-修复{node}error',
  },
}

export interface ExpectedResult {
  allowPass: boolean
  failedNode: string | null
}

export interface GateCapture {
  caseId: string
  capturedAt: string
  expected: ExpectedResult
  capture: Record<string, unknown>
  raw: Record<string, unknown>
  sessionIdHint: string | null
  logMarker: number
  roundKey: string | null
  serial: string | null
  gateMessages: Record<string, unknown>[]
}

export interface Resolution {
  settled: boolean
  sid: string | null
  allowPass: boolean | null
  failedNodes: string[]
  exitSeen: boolean
  contradictory: boolean
  matchedKeys: string[]
  terminalIndex: number | null
  note: string
}

export interface Verdict {
  status: VerdictStatus
  passed: boolean
  reasons: string[]
  expectedAllow: boolean | null
  actualAllow: boolean | null
  expectedNode: string | null
  actualNode: string | null
  actualNodes: string[]
  resolutionNote: string
  timedOut: boolean
}

export interface AnalysisResult {
  status: string
  rootCause: string
  possibleCauses: string[]
  solutions: string[]
  codeChanges: string[]
  confidence: 'high' | 'medium' | 'low' | string
  summary: string
  branchName?: string
  commitHash?: string
  analyzedAt?: string
}

export interface TestBatch {
  id: number
  runCode: string
  name: string
  status: BatchStatus
  gateMode: string
  logSource: string
  totalCases: number
  passedCases: number
  failedCases: number
  timeoutCases: number
  passRate: number
  createdBy?: string
  startedAt: string
  endedAt: string | null
  durationMs: number
  notes: string
}

export interface TestCaseRecord {
  id: number
  batchId: number
  batchRunCode?: string
  caseId: string
  serial: string | null
  roundKey: string | null
  passengerName: string
  certType: string
  certNo: string
  gateId: string
  photoData: string | null
  expectedAllow: boolean
  expectedNode: string | null
  actualAllow: boolean | null
  actualNode: string | null
  actualNodes: string[]
  verdictStatus: VerdictStatus
  passed: boolean
  timedOut: boolean
  contradictory: boolean
  sessionId: string | null
  reasons: string[]
  resolutionNote: string
  capture: Record<string, unknown>
  raw: Record<string, unknown>
  gateMessages: Record<string, unknown>[]
  logSlice: string
  analysisStatus: AnalysisStatus
  analysis: AnalysisResult | null
  createdBy?: string
  capturedAt: string
  judgedAt: string
}

export interface SystemLogEntry {
  id: number
  batchId: number | null
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG'
  source: string
  message: string
  operator?: string
  createdAt: string
}

export interface EngineStatus {
  running: boolean
  mode: 'idle' | 'live' | 'selftest'
  activeBatchId: number | null
  activeRunCode: string | null
  stage: string
  stageDetail: string
  gateConnected: boolean
  gateClientCount: number
  logConnected: boolean
  httpApiUrl: string | null
  logLineCount: number
  sessionCount: number
  pendingCases: number
  receivedCount: number
  passedCount: number
  failedCount: number
  timeoutCount: number
  startedAt: string | null
}

export interface DashboardStats {
  totalBatches: number
  totalCases: number
  passedCases: number
  failedCases: number
  timeoutCases: number
  passRate: number
  failedNodeStats: { node: string; count: number }[]
  recentBatches: TestBatch[]
  recentCases: TestCaseRecord[]
}

export interface DatabaseInfo {
  driver: string
  location: string
  sizeBytes: number
  batchCount: number
  caseCount: number
  logCount: number
  userCount: number
  onlineUsers: OnlineUser[]
}

export interface PermissionDef {
  code: string
  label: string
}

export interface RoleItem {
  id: number
  code: string
  name: string
  description: string
  permissions: string[]
  isSystem: boolean
  createdAt: string
}

export interface UserProfile {
  id: number
  username: string
  nickname: string
  roleCode: string
  roleName: string
  permissions: string[]
  department: string
  email: string
  status: 'active' | 'disabled'
  lastLoginAt: string | null
  createdAt: string
}

export interface AuditLogEntry {
  id: number
  userId: number | null
  username: string
  module: string
  action: string
  detail: string
  ip: string
  createdAt: string
}

export interface OnlineUser {
  userId: number
  username: string
  nickname: string
  roleCode: string
  roleName: string
  connectedAt: string
}

export interface AuthSession {
  serverUrl: string
  connected: boolean
  user: UserProfile | null
  allPermissions: PermissionDef[]
  onlineUsers: OnlineUser[]
}

export interface NodeRedInjectNode {
  id: string
  name: string
}

export interface NodeRedFlowTab {
  id: string
  label: string
  disabled: boolean
  info: string
  nodeCount: number
  injectNodes: NodeRedInjectNode[]
}

export interface NodeRedStatus {
  url: string
  reachable: boolean
  managedRunning: boolean
  embeddedAvailable: boolean
  port: number
  error?: string
}
