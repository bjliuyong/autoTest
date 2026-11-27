import type { AppConfig, Resolution } from './types'

const WRAPPERS = new Set('[](){}<>（）【】〔〕「」『』〈〉"\'“”‘’ \t\r\n:：,，。;；、-—_=|/\\')

export function cleanId(value: string | null | undefined): string {
  let text = String(value ?? '').trim()
  while (text.length > 0 && WRAPPERS.has(text[0])) {
    text = text.slice(1)
  }
  while (text.length > 0 && WRAPPERS.has(text[text.length - 1])) {
    text = text.slice(0, -1)
  }
  return text.trim()
}

function clip(text: string, limit = 60): string {
  const value = String(text ?? '')
    .split(/\s+/)
    .join(' ')
    .trim()
  return value.length <= limit ? value : `${value.slice(0, limit)}…`
}

export function sanitizeLogLine(
  line: string,
  maxFieldChars = 100,
  dropUndecodable = true
): { text: string | null; shrunk: boolean } {
  const cleaned = line.replace(/\r$/, '')
  if (!cleaned.trim()) return { text: null, shrunk: false }
  if (dropUndecodable) {
    const replacementCount = (cleaned.match(/\uFFFD/g) ?? []).length
    if (replacementCount >= 3 && replacementCount / cleaned.length > 0.15) {
      return { text: null, shrunk: false }
    }
  }
  if (maxFieldChars <= 0) return { text: cleaned, shrunk: false }

  let shrunk = false
  const re = new RegExp(`([A-Za-z0-9+/=_-]{${maxFieldChars + 1},})`, 'g')
  const out = cleaned.replace(re, (match) => {
    shrunk = true
    return `${match.slice(0, maxFieldChars)}…（长字段共 ${match.length} 字符）`
  })
  return { text: out, shrunk }
}

/** Convert Python named groups (?P<name>...) to JS (?<name>...) */
function compileRegex(pattern: string): RegExp | null {
  if (!pattern || !pattern.trim()) return null
  try {
    const jsPattern = pattern.replace(/\(\?P<([^>]+)>/g, '(?<$1>')
    return new RegExp(jsPattern)
  } catch {
    return null
  }
}

function compileAll(patterns: string[]): RegExp[] {
  const list: RegExp[] = []
  for (const p of patterns) {
    const r = compileRegex(p)
    if (r) list.push(r)
  }
  return list
}

function firstMatch(regexes: RegExp[], text: string): RegExpExecArray | null {
  for (const re of regexes) {
    const m = re.exec(text)
    if (m) return m
  }
  return null
}

export interface LogLine {
  index: number
  ts: number
  text: string
}

interface SessionEvent {
  index: number
  ts: number
  kind: 'failure' | 'exit'
  text: string
  sid: string | null
  node: string | null
  key: string | null
}

interface SessionState {
  sid: string
  createIndex: number
  createTs: number
  createText: string
  failureEvents: SessionEvent[]
  matchedKeys: Set<string>
  identities: Set<string>
  scanCursor: number
  windowEnd: number | null
}

function sessionCovers(session: SessionState, index: number): boolean {
  if (index < session.createIndex) return false
  return session.windowEnd === null || index <= session.windowEnd
}

export class LogStore {
  private readonly createRes: RegExp[]
  private readonly failureRes: RegExp[]
  private readonly exitRes: RegExp[]
  private readonly markerRe: RegExp | null
  private readonly sidRe = /会话ID\[(?<sid>[^\]]+)\]/g

  private readonly maxLines: number
  private readonly tolerance: number
  private readonly maxSlice: number
  private readonly singleFallback: boolean
  private readonly sliceLead: number

  private lines: LogLine[] = []
  private _nextIndex = 0
  private sessions = new Map<string, SessionState>()
  private order: string[] = []
  private firstSeen = new Map<string, number>()
  private failures: SessionEvent[] = []
  private exits: SessionEvent[] = []
  private claimed = new Set<string>()

  constructor(patterns: AppConfig['patterns'], run: AppConfig['run']) {
    this.createRes = compileAll(patterns.sessionCreate)
    this.failureRes = compileAll(patterns.nodeFailure)
    this.exitRes = compileAll(patterns.passengerExit)
    const marker = compileRegex(patterns.passengerMarker || '旅客\\[(?<key>[^\\]]+)\\]')
    this.markerRe = marker ? new RegExp(marker.source, 'g') : null

    this.maxLines = Math.max(1000, run.maxBufferLines)
    this.tolerance = Math.max(0, run.captureToleranceLines)
    this.maxSlice = Math.max(50, run.maxSliceLines)
    this.singleFallback = run.singleSessionFallback
    this.sliceLead = Math.max(0, run.sliceLeadLines)
  }

  get lineCount(): number {
    return this._nextIndex
  }

  get sessionCount(): number {
    return this.sessions.size
  }

  get nextIndex(): number {
    return this._nextIndex
  }

  lineAt(index: number): LogLine | null {
    if (this.lines.length === 0) return null
    const pos = index - this.lines[0].index
    if (pos < 0 || pos >= this.lines.length) return null
    return this.lines[pos]
  }

  linesBetween(start: number, end: number): LogLine[] {
    if (end < start) return []
    const res: LogLine[] = []
    for (let i = start; i <= end; i++) {
      const line = this.lineAt(i)
      if (line) res.push(line)
    }
    return res
  }

  appendMessage(payload: string, ts = Date.now()): number[] {
    const indexes: number[] = []
    const rawLines = payload.split(/\r?\n/)
    for (const raw of rawLines) {
      const text = raw.replace(/\r$/, '')
      if (!text.trim()) continue
      indexes.push(this.appendLine(text, ts))
    }
    return indexes
  }

  private appendLine(text: string, ts: number): number {
    const index = this._nextIndex++
    const line: LogLine = { index, ts, text }
    this.lines.push(line)
    if (this.lines.length > this.maxLines) {
      this.lines.shift()
    }
    this.parseLine(line)
    return index
  }

  private parseLine(line: LogLine): void {
    const { text } = line
    this.sidRe.lastIndex = 0
    let sidMatch: RegExpExecArray | null
    while ((sidMatch = this.sidRe.exec(text)) !== null) {
      const sidInLine = cleanId(sidMatch.groups?.sid)
      if (sidInLine && !this.firstSeen.has(sidInLine)) {
        this.firstSeen.set(sidInLine, line.index)
      }
    }

    const created = firstMatch(this.createRes, text)
    if (created) {
      const sid = cleanId(created.groups?.sid)
      if (sid && !this.sessions.has(sid)) {
        this.sessions.set(sid, {
          sid,
          createIndex: line.index,
          createTs: line.ts,
          createText: text,
          failureEvents: [],
          matchedKeys: new Set(),
          identities: new Set(),
          scanCursor: line.index,
          windowEnd: null,
        })
        this.order.push(sid)
        if (!this.firstSeen.has(sid)) this.firstSeen.set(sid, line.index)
        this.refreshWindows()
      }
    }

    const failed = firstMatch(this.failureRes, text)
    if (failed) {
      const sid = cleanId(failed.groups?.sid)
      const node = cleanId(failed.groups?.node)
      const ev: SessionEvent = {
        index: line.index,
        ts: line.ts,
        kind: 'failure',
        text,
        sid: sid || null,
        node: node || null,
        key: null,
      }
      this.failures.push(ev)
      let session = sid ? this.sessions.get(sid) : undefined
      if (!session) {
        session = {
          sid: sid || `?${line.index}`,
          createIndex: line.index,
          createTs: line.ts,
          createText: '',
          failureEvents: [],
          matchedKeys: new Set(),
          identities: new Set(),
          scanCursor: line.index,
          windowEnd: null,
        }
        if (sid) {
          this.sessions.set(sid, session)
          this.order.push(sid)
          this.refreshWindows()
        }
      }
      session.failureEvents.push(ev)
    }

    const exited = firstMatch(this.exitRes, text)
    if (exited) {
      const key = cleanId(exited.groups?.key) || null
      this.sidRe.lastIndex = 0
      const sidHint = this.sidRe.exec(text)
      this.exits.push({
        index: line.index,
        ts: line.ts,
        kind: 'exit',
        text,
        sid: sidHint ? cleanId(sidHint.groups?.sid) || null : null,
        node: null,
        key,
      })
    }

    if (this.failures.length > this.maxLines) this.failures.shift()
    if (this.exits.length > this.maxLines) this.exits.shift()
  }

  private refreshWindows(): void {
    const ordered: SessionState[] = []
    for (const sid of this.order) {
      const s = this.sessions.get(sid)
      if (s) ordered.push(s)
    }
    for (let i = 0; i < ordered.length; i++) {
      if (i + 1 < ordered.length) {
        ordered[i].windowEnd = Math.max(ordered[i].createIndex, ordered[i + 1].createIndex - 1)
      } else {
        ordered[i].windowEnd = null
      }
    }
  }

  resolve(passengerKeys: string[], sinceIndex: number, sessionHint: string | null = null): Resolution {
    const keys = passengerKeys.filter(Boolean)
    const lowerBound = Math.max(0, sinceIndex - this.tolerance)

    let hinted: SessionState | null = null
    if (sessionHint) {
      const cand = this.sessions.get(cleanId(sessionHint)) ?? this.sessions.get(sessionHint.trim())
      if (cand && !this.claimed.has(cand.sid)) {
        hinted = cand
      }
    }

    const candidates = hinted
      ? [hinted]
      : [...this.sessions.values()].filter(
          (s) => !this.claimed.has(s.sid) && (s.windowEnd === null || s.windowEnd >= lowerBound)
        )

    if (candidates.length === 0) {
      return {
        settled: false,
        sid: null,
        allowPass: null,
        failedNodes: [],
        exitSeen: false,
        contradictory: false,
        matchedKeys: [],
        terminalIndex: null,
        note: '',
      }
    }

    for (const session of candidates) {
      this.scanSessionKeys(session, keys)
    }

    let chosen: SessionState[] = []
    let fallbackNote = ''

    if (hinted) {
      chosen = [hinted]
      fallbackNote = '（按用例指定的会话ID归属）'
    } else {
      const matched = candidates.filter((s) => s.matchedKeys.size > 0)
      chosen = matched
      if (chosen.length === 0) {
        const usable = candidates.filter((s) => s.windowEnd === null || s.windowEnd >= lowerBound)
        const runningAtCapture = usable.filter(
          (s) => s.createIndex < sinceIndex && !this.finishedBefore(s, sinceIndex)
        )
        if (runningAtCapture.length > 0) {
          runningAtCapture.sort((a, b) => b.createIndex - a.createIndex)
          chosen = [runningAtCapture[0]]
          fallbackNote = '（按采集时已在运行的会话归属）'
        } else {
          const keySet = new Set(keys)
          const covering = usable.filter((s) => {
            if (!sessionCovers(s, sinceIndex)) return false
            if (keys.length > 0 && s.matchedKeys.size === 0) {
              for (const id of s.identities) {
                if (!keySet.has(id)) return false
              }
            }
            return true
          })
          if (covering.length > 0) {
            covering.sort((a, b) => a.createIndex - b.createIndex)
            chosen = [covering[0]]
            fallbackNote = '（按采集时间落在的会话区间归属）'
          }
        }
      }

      if (chosen.length === 0 && this.singleFallback) {
        const keySet = new Set(keys)
        const strict = candidates.filter((s) => {
          if (s.createIndex < sinceIndex || s.matchedKeys.size > 0) return false
          for (const id of s.identities) {
            if (!keySet.has(id)) return false
          }
          return true
        })
        if (strict.length === 1) {
          chosen = strict
          fallbackNote = '（日志中未出现旅客标识，按唯一候选会话归属）'
        }
      }
    }

    const sortedChosen = [...chosen].sort((a, b) => b.createIndex - a.createIndex)
    for (const session of sortedChosen) {
      const events: SessionEvent[] = [...session.failureEvents]
      const { picked: exits, note: exitsNote } = this.sessionExits(session, keys, sinceIndex)
      events.push(...exits)
      const noteSuffix = exitsNote ? `${fallbackNote}${exitsNote}` : fallbackNote
      if (events.length === 0) continue

      events.sort((a, b) => a.index - b.index)
      const last = events[events.length - 1]
      const hasFailure = events.some((e) => e.kind === 'failure')
      const hasExit = events.some((e) => e.kind === 'exit')

      const resolution: Resolution = {
        settled: true,
        sid: session.sid,
        allowPass: last.kind === 'failure' ? false : true,
        failedNodes: session.failureEvents.map((e) => e.node).filter((n): n is string => Boolean(n)),
        exitSeen: hasExit,
        contradictory: hasFailure && hasExit,
        matchedKeys: [...session.matchedKeys].sort(),
        terminalIndex: last.index,
        note:
          last.kind === 'failure'
            ? `会话 ${session.sid} 节点「${last.node}」执行失败${noteSuffix}`
            : `${last.key ? `旅客[${last.key}]` : '日志'}出现「${clip(last.text)}」${noteSuffix}`,
      }
      this.claimed.add(session.sid)
      return resolution
    }

    return {
      settled: false,
      sid: null,
      allowPass: null,
      failedNodes: [],
      exitSeen: false,
      contradictory: false,
      matchedKeys: [],
      terminalIndex: null,
      note: '',
    }
  }

  private sessionExits(
    session: SessionState,
    keys: string[],
    sinceIndex: number
  ): { picked: SessionEvent[]; note: string } {
    const afterCreate = this.exits.filter((e) => e.index >= session.createIndex)
    if (afterCreate.length === 0) return { picked: [], note: '' }
    const inWindow = afterCreate.filter((e) => session.windowEnd === null || e.index <= session.windowEnd)
    const keySet = new Set(keys.filter(Boolean))
    const matched = afterCreate.filter((e) => e.key && keySet.has(e.key))
    if (matched.length > 0) return { picked: matched, note: '' }

    if (keySet.size > 0) {
      const picked = inWindow.filter(
        (e) => e.index >= sinceIndex && (e.key === null || session.identities.has(e.key))
      )
      if (picked.length === 0) return { picked: [], note: '' }
      return { picked, note: '（日志中的旅客标识与用例字段不同名，按会话区间归属结论事件）' }
    }

    const picked = [...afterCreate.filter((e) => Boolean(e.key)), ...inWindow.filter((e) => !e.key)]
    return { picked, note: '（用例未提供旅客标识，按会话区间归属结论事件）' }
  }

  private finishedBefore(session: SessionState, index: number): boolean {
    if (session.failureEvents.some((e) => e.index < index)) return true
    for (const e of this.exits) {
      if (e.index >= index) break
      if (e.sid === session.sid) return true
    }
    return false
  }

  private scanSessionKeys(session: SessionState, keys: string[]): void {
    const start = Math.max(session.scanCursor, session.createIndex)
    let end = this._nextIndex
    if (session.windowEnd !== null) {
      end = Math.min(end, session.windowEnd + 1)
    }
    for (let i = start; i < end; i++) {
      const line = this.lineAt(i)
      if (!line) continue
      for (const key of keys) {
        if (key && line.text.includes(key)) {
          session.matchedKeys.add(key)
        }
      }
      if (this.markerRe) {
        this.markerRe.lastIndex = 0
        let m: RegExpExecArray | null
        while ((m = this.markerRe.exec(line.text)) !== null) {
          const val = cleanId(m.groups?.key)
          if (val) session.identities.add(val)
        }
      }
    }
    session.scanCursor = end
  }

  private window(start: number, end: number, limit?: number): LogLine[] {
    const cap = limit !== undefined ? Math.max(1, limit) : this.maxSlice
    const total = end - start + 1
    if (total <= cap) return this.linesBetween(start, end)
    const headN = Math.max(1, Math.floor(cap / 2))
    const tailN = Math.max(1, cap - headN)
    const head = this.linesBetween(start, start + headN - 1)
    const tail = this.linesBetween(end - tailN + 1, end)
    const omitted = total - headN - tailN
    return [...head, { index: -1, ts: 0, text: `……（中间省略 ${omitted} 行）……` }, ...tail]
  }

  sliceSession(sid: string | null, alsoKeys: string[] = [], includeKeys = true): LogLine[] {
    if (!sid) return []
    const session = this.sessions.get(sid)
    if (!session) return []
    const anchor = Math.min(session.createIndex, this.firstSeen.get(sid) ?? session.createIndex)
    const start = Math.max(0, anchor - this.sliceLead)
    let end = anchor
    const needles = [sid, ...(includeKeys ? alsoKeys.filter(Boolean) : [])]
    for (const line of this.lines) {
      if (line.index < anchor) continue
      if (needles.some((n) => line.text.includes(n))) {
        end = line.index
      }
    }
    end = Math.max(end, Math.min(this._nextIndex - 1, anchor))
    return this.window(start, end)
  }

  sliceAround(index: number, limit?: number): LogLine[] {
    if (this.lines.length === 0) return []
    const count = limit !== undefined ? Math.max(1, limit) : this.maxSlice
    const first = this.lines[0].index
    const start = Math.max(first, index - this.sliceLead)
    const end = Math.min(this._nextIndex - 1, start + count - 1)
    return this.window(start, end, count)
  }
}

export function linesToText(lines: LogLine[]): string {
  return lines.map((l) => l.text).join('\n')
}
