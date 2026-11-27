import crypto from 'crypto'
import type { ExpectedResult, GateCapture } from './types'

const TRUE_TOKENS = new Set([
  'true',
  '1',
  'yes',
  'y',
  'on',
  'pass',
  'allowed',
  'allow',
  '允许',
  '允许通行',
  '通行',
])

const FALSE_TOKENS = new Set([
  'false',
  '0',
  'no',
  'n',
  'off',
  'deny',
  'denied',
  'block',
  '禁止',
  '不允许',
  '不通行',
  '拦截',
])

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ProtocolError'
  }
}

export class NotACaseMessage extends ProtocolError {
  constructor(message: string) {
    super(message)
    this.name = 'NotACaseMessage'
  }
}

export function dig(data: Record<string, unknown>, dotted: string): unknown {
  let current: unknown = data
  for (const part of dotted.split('.')) {
    if (current === null || typeof current !== 'object' || !(part in (current as Record<string, unknown>))) {
      return undefined
    }
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

function firstPresent(data: Record<string, unknown>, names: string[]): unknown {
  for (const name of names) {
    if (name in data && data[name] !== undefined && data[name] !== null) {
      return data[name]
    }
  }
  return undefined
}

function parseBool(value: unknown, fieldName: string): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return Boolean(value)
  if (typeof value === 'string') {
    const token = value.trim().toLowerCase()
    if (TRUE_TOKENS.has(token)) return true
    if (FALSE_TOKENS.has(token)) return false
  }
  throw new ProtocolError(`字段 ${fieldName} 无法解析成「是否允许通行」：${JSON.stringify(value)}`)
}

function parseTimestamp(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    const ms = value < 1e12 ? value * 1000 : value
    const d = new Date(ms)
    if (!Number.isNaN(d.getTime())) return d.toISOString()
  }
  if (typeof value === 'string' && value.trim()) {
    const d = new Date(value.trim())
    if (!Number.isNaN(d.getTime())) return d.toISOString()
  }
  return new Date().toISOString()
}

export function applyFieldMap(
  payload: Record<string, unknown>,
  mapping?: Record<string, string> | null
): Record<string, unknown> {
  if (!mapping || Object.keys(mapping).length === 0) {
    return { ...payload }
  }
  const result: Record<string, unknown> = { ...payload }
  for (const [target, source] of Object.entries(mapping)) {
    if (typeof source !== 'string' || !source.trim()) continue
    const value = dig(payload, source.trim())
    if (value !== undefined && value !== null) {
      result[target] = value
    }
  }
  return result
}

export function parseGateMessage(payload: unknown): GateCapture {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new ProtocolError(`闸机消息必须是 JSON 对象`)
  }
  const obj = payload as Record<string, unknown>
  const msgType = String(obj.type ?? '').trim()
  const knownTypes = new Set(['', 'gate.capture', 'capture', 'case', 'test.case', 'gate', 'gate.case'])
  if (msgType && !knownTypes.has(msgType.toLowerCase())) {
    const looksLikeCase = 'expected' in obj || 'allow_pass' in obj || 'capture' in obj
    if (!looksLikeCase) {
      throw new NotACaseMessage(`不是用例报文（type=${msgType}）`)
    }
  }

  const expectedRaw = obj.expected
  const expectedMap: Record<string, unknown> =
    expectedRaw && typeof expectedRaw === 'object' && !Array.isArray(expectedRaw)
      ? (expectedRaw as Record<string, unknown>)
      : obj

  const allowValue = firstPresent(expectedMap, [
    'allow_pass',
    'allowPass',
    'allow',
    'allowed',
    'pass',
    'expect_allow',
    '是否允许通行',
  ])
  if (allowValue === undefined) {
    throw new NotACaseMessage('没有 expected.allow_pass，不是用例报文')
  }
  const allowPass = parseBool(allowValue, 'expected.allow_pass')

  const nodeValue = firstPresent(expectedMap, [
    'failed_node',
    'failedNode',
    'fail_node',
    'node',
    '失败节点',
    '节点',
  ])
  const failedNode =
    nodeValue !== undefined && nodeValue !== null && String(nodeValue).trim()
      ? String(nodeValue).trim()
      : null

  const captureRaw = obj.capture
  let capture: Record<string, unknown>
  if (captureRaw && typeof captureRaw === 'object' && !Array.isArray(captureRaw)) {
    capture = { ...(captureRaw as Record<string, unknown>) }
  } else {
    const excluded = new Set(['type', 'case_id', 'timestamp', 'expected', 'capture'])
    capture = {}
    for (const [k, v] of Object.entries(obj)) {
      if (!excluded.has(k)) capture[k] = v
    }
  }

  let caseId = String(obj.case_id ?? obj.caseId ?? '').trim()
  if (!caseId) {
    const now = new Date()
    const stamp = now.toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)
    caseId = `C-${stamp}-${crypto.randomBytes(3).toString('hex')}`
  }

  let sessionHint = firstPresent(obj, ['session_id', 'sessionId', 'conversation_id', 'trace_id', '会话ID'])
  if (sessionHint === undefined && captureRaw && typeof captureRaw === 'object') {
    sessionHint = firstPresent(captureRaw as Record<string, unknown>, [
      'session_id',
      'sessionId',
      'conversation_id',
      'trace_id',
      '会话ID',
    ])
  }
  const sessionIdHint = sessionHint !== undefined && String(sessionHint).trim() ? String(sessionHint).trim() : null

  const expected: ExpectedResult = { allowPass, failedNode }

  return {
    caseId,
    capturedAt: parseTimestamp(obj.timestamp),
    expected,
    capture,
    raw: { ...obj },
    sessionIdHint,
    logMarker: 0,
    roundKey: null,
    serial: null,
    gateMessages: [],
  }
}

export function getPassengerCandidates(capture: GateCapture, keys: string[]): string[] {
  const values: string[] = []
  for (const key of keys) {
    let val = dig(capture.capture, key)
    if (val === undefined || val === null) {
      val = dig(capture.raw, key)
    }
    if ((typeof val === 'string' || typeof val === 'number') && String(val).trim()) {
      const text = String(val).trim()
      if (!values.includes(text)) values.push(text)
    }
  }
  return values
}

export function extractPhotoData(capture: Record<string, unknown>): string | null {
  const fields = ['photo_base64', 'photo', 'photo_url', 'photo_path', 'face_image', 'image']
  for (const field of fields) {
    const val = capture[field]
    if (typeof val === 'string' && val.trim()) {
      const s = val.trim()
      if (s.startsWith('data:image/') || s.startsWith('http://') || s.startsWith('https://')) {
        return s
      }
      if (s.length > 64 && /^[A-Za-z0-9+/=\s]+$/.test(s)) {
        return `data:image/jpeg;base64,${s.replace(/\s+/g, '')}`
      }
    }
  }
  return null
}

export function buildAck(caseId: string, status: 'accepted' | 'rejected', reason = ''): Record<string, unknown> {
  return { type: 'gate.ack', case_id: caseId, status, reason }
}
