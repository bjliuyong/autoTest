import { useState } from 'react'
import { Download, ExternalLink, FileCode2, Search, Trash2 } from 'lucide-react'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/app/components/ui/card'
import { Input } from '@/app/components/ui/input'
import type { TestBatch } from '@/lib/autotest/types'

interface BatchesPageProps {
  batches: TestBatch[]
  onViewBatchCases: (batchId: number) => void
  onExportBatch: (batchId: number, format: 'html' | 'json') => void
  onDeleteBatch: (batchId: number) => void
}

function fmtDateTime(iso: string | null) {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

function fmtDuration(ms: number) {
  if (!ms || ms < 1000) return `${ms || 0}ms`
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  return `${min}m ${sec % 60}s`
}

export function BatchesPage({
  batches,
  onViewBatchCases,
  onExportBatch,
  onDeleteBatch,
}: BatchesPageProps) {
  const [keyword, setKeyword] = useState('')

  const filtered = batches.filter((b) => {
    const q = keyword.trim().toLowerCase()
    if (!q) return true
    return (
      b.runCode.toLowerCase().includes(q) ||
      b.name.toLowerCase().includes(q) ||
      b.gateMode.toLowerCase().includes(q)
    )
  })

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">历史测试批次</h1>
          <p className="text-xs text-muted-foreground">
            每次实时测试或自测均自动归档至本地 SQLite 数据库，支持随时回溯与导出报告
          </p>
        </div>
        <div className="relative w-64">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索批次号 / 名称…"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">全部批次记录（共 {filtered.length} 批）</CardTitle>
          <CardDescription className="text-xs">
            包含用例统计、实时通过率、耗时及通道来源快照
          </CardDescription>
        </CardHeader>
        <CardContent>
          {filtered.length === 0 ? (
            <div className="py-14 text-center text-xs text-muted-foreground">
              暂无历史测试批次。前往「监控大盘」点击「一键自测模拟」或「启动实时测试」创建首条记录。
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="py-2.5 pr-3 font-medium">批次编号 / 名称</th>
                    <th className="px-3 py-2.5 font-medium">执行人</th>
                    <th className="px-3 py-2.5 font-medium">状态</th>
                    <th className="px-3 py-2.5 font-medium">通道来源</th>
                    <th className="px-3 py-2.5 font-medium">用例统计 (总/通/败/超)</th>
                    <th className="px-3 py-2.5 font-medium">通过率</th>
                    <th className="px-3 py-2.5 font-medium">开始时间 / 耗时</th>
                    <th className="py-2.5 pl-3 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {filtered.map((batch) => (
                    <tr key={batch.id} className="transition-colors hover:bg-secondary/40">
                      <td className="py-3 pr-3">
                        <div className="font-mono font-semibold text-foreground">{batch.runCode}</div>
                        <div className="text-[11px] text-muted-foreground">{batch.name}</div>
                      </td>
                      <td className="px-3 py-3 font-mono text-[11px] text-foreground/80">
                        {batch.createdBy || 'admin'}
                      </td>
                      <td className="px-3 py-3">
                        <Badge
                          variant="outline"
                          className={
                            batch.status === 'running'
                              ? 'border-brand/40 bg-brand/10 text-brand'
                              : batch.status === 'completed'
                                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
                                : 'text-muted-foreground'
                          }
                        >
                          {batch.status === 'running' ? '运行中' : batch.status === 'completed' ? '已完成' : '已停止'}
                        </Badge>
                      </td>
                      <td className="px-3 py-3 font-mono text-[11px] text-muted-foreground">
                        {batch.gateMode} / {batch.logSource}
                      </td>
                      <td className="px-3 py-3 font-mono">
                        <span className="font-semibold">{batch.totalCases}</span>
                        <span className="mx-1 text-muted-foreground">/</span>
                        <span className="text-emerald-500">{batch.passedCases}</span>
                        <span className="mx-1 text-muted-foreground">/</span>
                        <span className="text-rose-500">{batch.failedCases}</span>
                        <span className="mx-1 text-muted-foreground">/</span>
                        <span className="text-amber-500">{batch.timeoutCases}</span>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-secondary">
                            <div
                              className="h-full rounded-full bg-emerald-500 transition-all"
                              style={{ width: `${Math.min(100, Math.max(0, batch.passRate))}%` }}
                            />
                          </div>
                          <span className="font-mono text-[11px] font-medium">{batch.passRate.toFixed(1)}%</span>
                        </div>
                      </td>
                      <td className="px-3 py-3 text-[11px] text-muted-foreground">
                        <div>{fmtDateTime(batch.startedAt)}</div>
                        <div className="font-mono">耗时 {fmtDuration(batch.durationMs)}</div>
                      </td>
                      <td className="py-3 pl-3 text-right">
                        <div className="inline-flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 px-2 text-xs text-brand hover:text-brand"
                            onClick={() => onViewBatchCases(batch.id)}
                          >
                            <ExternalLink className="size-3" />
                            明细
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 px-2 text-xs"
                            title="导出 HTML 报告"
                            onClick={() => onExportBatch(batch.id, 'html')}
                          >
                            <Download className="size-3" />
                            HTML
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 px-2 text-xs"
                            title="导出 JSON 数据"
                            onClick={() => onExportBatch(batch.id, 'json')}
                          >
                            <FileCode2 className="size-3" />
                            JSON
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs text-rose-500 hover:bg-rose-500/10 hover:text-rose-500"
                            title="删除该批次及关联用例"
                            onClick={() => onDeleteBatch(batch.id)}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
