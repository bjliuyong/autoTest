import type { AppConfig } from './types'

export function formatDatePattern(pattern: string, date: Date = new Date()): string {
  const yyyy = String(date.getFullYear())
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  if (!pattern || !pattern.trim()) return ''
  return pattern
    .replace(/YYYY/g, yyyy)
    .replace(/MM/g, mm)
    .replace(/DD/g, dd)
}

/**
 * Pure path resolver (works in both Node main process and React renderer preview).
 * Supports:
 * 1) Placeholders in filePath: {YYYY}, {MM}, {DD}, {date}
 * 2) Directory (relative or absolute) + filePrefix + auto-calculated date + fileExtension
 *    e.g. filePath="./logs", filePrefix="biisapp_", fileDateFormat="YYYY-MM-DD", fileExtension=".log"
 *    => "./logs/biisapp_2026-11-27.log" (resolved against baseDir in main process)
 */
export function buildDynamicLogPath(
  logs: Pick<AppConfig['logs'], 'filePath' | 'filePrefix' | 'fileDateFormat' | 'fileExtension'>,
  date: Date = new Date()
): string {
  const yyyy = String(date.getFullYear())
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const dateFmt = logs.fileDateFormat ?? 'YYYY-MM-DD'
  const dateStr = formatDatePattern(dateFmt, date)

  let rawPath = (logs.filePath || '').trim()
  rawPath = rawPath
    .replace(/\{YYYY\}/g, yyyy)
    .replace(/\{MM\}/g, mm)
    .replace(/\{DD\}/g, dd)
    .replace(/\{date\}/g, dateStr || `${yyyy}-${mm}-${dd}`)

  const prefix = (logs.filePrefix || '').trim()
  const ext = (logs.fileExtension ?? '.log').trim()

  if (prefix) {
    const fileName = `${prefix}${dateStr}${ext}`
    if (!rawPath) return fileName
    const sep = rawPath.endsWith('/') || rawPath.endsWith('\\') ? '' : '/'
    return `${rawPath}${sep}${fileName}`
  }

  return rawPath
}
