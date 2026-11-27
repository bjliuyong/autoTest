import { dig } from './protocol'

export const DEFAULT_ROUND_KEYS = [
  'serial',
  'Serial',
  'serialNo',
  'serial_no',
  'requestId',
  'request_id',
  'capture.serial',
  'capture.cert_no',
  'cert_no',
]

export function serialValue(payload: Record<string, unknown>, keys: string[] = DEFAULT_ROUND_KEYS): string | null {
  for (const name of keys) {
    const val = dig(payload, name)
    if ((typeof val === 'string' || typeof val === 'number') && String(val).trim()) {
      return String(val).trim()
    }
  }
  return null
}

export function roundKeyOf(
  payload: Record<string, unknown>,
  keys: string[] = DEFAULT_ROUND_KEYS,
  split: string | null = '_'
): string | null {
  const val = serialValue(payload, keys)
  if (!val) return null
  if (split && val.includes(split)) {
    return val.split(split, 1)[0] || null
  }
  return val || null
}

interface RoundEntry {
  firstLogIndex: number
  firstSeen: number
  lastSeen: number
  frames: Record<string, unknown>[]
}

export class RoundTracker {
  private readonly keys: string[]
  private readonly split: string | null
  private readonly ttlMs: number
  private readonly maxRounds: number
  private readonly rounds = new Map<string, RoundEntry>()

  constructor(options?: { keys?: string[]; split?: string | null; ttlSeconds?: number; maxRounds?: number }) {
    this.keys = options?.keys ?? DEFAULT_ROUND_KEYS
    this.split = options?.split !== undefined ? options.split : '_'
    this.ttlMs = Math.max(30, options?.ttlSeconds ?? 600) * 1000
    this.maxRounds = Math.max(10, options?.maxRounds ?? 500)
  }

  observe(payload: Record<string, unknown>, logIndex: number): { key: string | null; isFirst: boolean } {
    const key = roundKeyOf(payload, this.keys, this.split)
    if (!key) return { key: null, isFirst: false }
    const now = Date.now()
    this.evict(now)
    const brief = this.stripLargeFields(payload)
    const existing = this.rounds.get(key)
    if (!existing) {
      this.rounds.set(key, {
        firstLogIndex: logIndex,
        firstSeen: now,
        lastSeen: now,
        frames: [brief],
      })
      return { key, isFirst: true }
    }
    existing.lastSeen = now
    if (existing.frames.length < 100) {
      existing.frames.push(brief)
    }
    return { key, isFirst: false }
  }

  markerFor(key: string | null, fallback: number): number {
    if (!key) return fallback
    const rec = this.rounds.get(key)
    return rec ? Math.min(rec.firstLogIndex, fallback) : fallback
  }

  framesFor(key: string | null): Record<string, unknown>[] {
    if (!key) return []
    const rec = this.rounds.get(key)
    return rec ? [...rec.frames] : []
  }

  forget(key: string | null): void {
    if (key) this.rounds.delete(key)
  }

  private stripLargeFields(obj: Record<string, unknown>): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === 'string' && v.length > 200) {
        out[k] = `${v.slice(0, 80)}…（共 ${v.length} 字符）`
      } else if (v && typeof v === 'object' && !Array.isArray(v)) {
        out[k] = this.stripLargeFields(v as Record<string, unknown>)
      } else {
        out[k] = v
      }
    }
    return out
  }

  private evict(now: number): void {
    if (this.rounds.size < this.maxRounds) {
      for (const [k, v] of this.rounds) {
        if (now - v.lastSeen > this.ttlMs) this.rounds.delete(k)
      }
      return
    }
    const sorted = [...this.rounds.entries()].sort((a, b) => a[1].lastSeen - b[1].lastSeen)
    const removeCount = Math.max(1, Math.floor(sorted.length / 2))
    for (let i = 0; i < removeCount; i++) {
      this.rounds.delete(sorted[i][0])
    }
  }
}
