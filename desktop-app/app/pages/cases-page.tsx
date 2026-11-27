import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileText,
  Image as ImageIcon,
  Search,
  Sparkles,
  Trash2,
  User,
  XCircle,
} from 'lucide-react'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/app/components/ui/card'
import { Input } from '@/app/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/app/components/ui/tabs'
import type { TestBatch, TestCaseRecord, VerdictStatus } from '@/lib/autotest/types'

interface CasesPageProps {
  cases: TestCaseRecord[]
  batches: TestBatch[]
  selectedBatchId: number | undefined
  onSelectBatchId: (batchId: number | undefined) => void
  statusFilter: VerdictStatus | 'ALL'
  onStatusFilterChange: (status: VerdictStatus | 'ALL') => void
  keyword: string
  onKeywordChange: (kw: string) => void
  selectedCaseId: number | null
  onSelectCaseId: (id: number | null) => void
  onAnalyzeCase: (id: number) => void
  onDeleteCase: (id: number) => void
  analyzingId: number | null
}

function statusPill(status: VerdictStatus) {
  if (status === 'PASS') {
    return (
      <Badge className="gap-1 bg-emerald-500/15 text-emerald-500 border-emerald-500/30 hover:bg-emerald-500/20">
        <CheckCircle2 className="size-3" /> 通过
      </Badge>
    )
  }
  if (status === 'TIMEOUT') {
    return (
      <Badge className="gap-1 bg-amber-500/15 text-amber-500 border-amber-500/30 hover:bg-amber-500/20">
        <Clock className="size-3" /> 超时
      </Badge>
    )
  }
  return (
    <Badge className="gap-1 bg-rose-500/15 text-rose-500 border-rose-500/30 hover:bg-rose-500/20">
      <XCircle className="size-3" /> 不通过
    </Badge>
  )
}

function fmtDate(iso: string) {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

export function CasesPage({
  cases,
  batches,
  selectedBatchId,
  onSelectBatchId,
  statusFilter,
  onStatusFilterChange,
  keyword,
  onKeywordChange,
  selectedCaseId,
  onSelectCaseId,
  onAnalyzeCase,
  onDeleteCase,
  analyzingId,
}: CasesPageProps) {
  const [logSearch, setLogSearch] = useState('')

  const selected = useMemo(() => {
    if (selectedCaseId !== null) {
      const found = cases.find((c) => c.id === selectedCaseId)
      if (found) return found
    }
    return cases[0] ?? null
  }, [cases, selectedCaseId])

  const logLines = useMemo(() => {
    if (!selected?.logSlice) return []
    const all = selected.logSlice.split('\n')
    if (!logSearch.trim()) return all.map((text, idx) => ({ lineNo: idx + 1, text }))
    const q = logSearch.trim().toLowerCase()
    return all
      .map((text, idx) => ({ lineNo: idx + 1, text }))
      .filter((l) => l.text.toLowerCase().includes(q))
  }, [selected, logSearch])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border bg-card/60 px-5 py-3">
        <select
          value={selectedBatchId ?? ''}
          onChange={(e) => onSelectBatchId(e.target.value ? Number(e.target.value) : undefined)}
          className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none focus:border-brand"
        >
          <option value="">全部测试批次</option>
          {batches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.runCode} ({b.totalCases}条 · {b.passRate}%)
            </option>
          ))}
        </select>

        <div className="flex items-center rounded-md border border-border bg-background p-0.5 text-xs">
          {(['ALL', 'PASS', 'FAIL', 'TIMEOUT'] as const).map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => onStatusFilterChange(st)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                statusFilter === st
                  ? 'bg-brand text-white'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {st === 'ALL' ? '全部' : st === 'PASS' ? '通过' : st === 'FAIL' ? '不通过' : '超时'}
            </button>
          ))}
        </div>

        <div className="relative ml-auto w-64">
          <Search className="pointer-events-none absolute top-2 left-2.5 size-3.5 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={(e) => onKeywordChange(e.target.value)}
            placeholder="搜用例号 / 旅客 / 证件号 / 节点…"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      {/* Master-Detail Split */}
      <div className="grid min-h-0 flex-1 grid-cols-12">
        {/* Left List */}
        <div className="col-span-4 flex min-h-0 flex-col border-r border-border bg-card/30">
          <div className="border-b border-border px-4 py-2 text-[11px] font-medium text-muted-foreground">
            共 {cases.length} 条追溯记录
          </div>
          <div className="flex-1 space-y-1.5 overflow-y-auto p-2.5">
            {cases.length === 0 ? (
              <div className="py-16 text-center text-xs text-muted-foreground">暂无匹配的用例记录</div>
            ) : (
              cases.map((item) => {
                const isSelected = selected?.id === item.id
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onSelectCaseId(item.id)}
                    className={`w-full rounded-lg border p-3 text-left transition-all ${
                      isSelected
                        ? 'border-brand bg-brand/10 shadow-xs'
                        : 'border-border/60 bg-card hover:border-border-bright hover:bg-secondary/50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs font-semibold">{item.caseId}</span>
                      {statusPill(item.verdictStatus)}
                    </div>
                    <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span className="truncate">
                        {item.passengerName || '未知旅客'} · {item.certNo || '无证号'}
                      </span>
                      <span className="shrink-0 font-mono">{item.gateId || '-'}</span>
                    </div>
                    <p className="mt-1 line-clamp-1 text-[11px] text-muted-foreground">
                      {item.reasons[0] || item.resolutionNote || '-'}
                    </p>
                  </button>
                )
              })
            )}
          </div>
        </div>

        {/* Right Detail Inspector */}
        <div className="col-span-8 flex min-h-0 flex-col overflow-y-auto p-5">
          {!selected ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
              <FileText className="size-8 opacity-40" />
              <p className="text-xs">在左侧选择一条用例查看完整追溯信息</p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Case Header */}
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2.5">
                    <h2 className="font-mono text-base font-semibold">{selected.caseId}</h2>
                    {statusPill(selected.verdictStatus)}
                    {selected.sessionId && (
                      <Badge variant="outline" className="font-mono text-[11px]">
                        会话 {selected.sessionId}
                      </Badge>
                    )}
                    {selected.batchRunCode && (
                      <Badge variant="secondary" className="font-mono text-[11px]">
                        {selected.batchRunCode}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    采集时间：{fmtDate(selected.capturedAt)} ｜ 判定时间：{fmtDate(selected.judgedAt)}
                    {selected.serial ? ` ｜ Serial: ${selected.serial}` : ''}
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  {!selected.passed && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={analyzingId === selected.id}
                      onClick={() => onAnalyzeCase(selected.id)}
                      className="h-8 gap-1.5 border-brand/40 text-xs text-brand hover:bg-brand/10 hover:text-brand"
                    >
                      <Sparkles className="size-3.5" />
                      {analyzingId === selected.id ? '分析中…' : '根因诊断分析'}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2 text-rose-500 hover:bg-rose-500/10 hover:text-rose-500"
                    onClick={() => onDeleteCase(selected.id)}
                    title="删除此用例"
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>

              {/* Passenger + Photo + Verdict Comparison */}
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                {/* Photo & Passenger Info */}
                <Card className="xl:col-span-1">
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <User className="size-3.5" /> 旅客与抓拍信息
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="overflow-hidden rounded-lg border border-border bg-secondary/40">
                      {selected.photoData ? (
                        <img
                          src={selected.photoData}
                          alt={selected.passengerName || selected.caseId}
                          className="h-36 w-full object-cover"
                        />
                      ) : (
                        <div className="flex h-36 flex-col items-center justify-center gap-1 text-muted-foreground">
                          <ImageIcon className="size-6 opacity-40" />
                          <span className="text-[11px]">无采集照片</span>
                        </div>
                      )}
                    </div>
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">姓名</span>
                        <span className="font-medium">{selected.passengerName || '-'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">证件类型</span>
                        <span className="font-mono">{selected.certType || '-'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">证件号码</span>
                        <span className="font-mono">{selected.certNo || '-'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">闸机通道</span>
                        <span className="font-mono">{selected.gateId || '-'}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* Expected vs Actual */}
                <Card className="xl:col-span-2">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-xs font-medium text-muted-foreground">预期 vs 实际判定比对</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-lg border border-border bg-secondary/40 p-3">
                        <div className="text-[11px] text-muted-foreground">预期结论</div>
                        <div className="mt-1 text-sm font-semibold">
                          {selected.expectedAllow ? '允许通行' : '不允许通行（拦截）'}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          预期节点：<span className="font-mono text-foreground">{selected.expectedNode || '无'}</span>
                        </div>
                      </div>
                      <div className="rounded-lg border border-border bg-secondary/40 p-3">
                        <div className="text-[11px] text-muted-foreground">实际结论（日志推断）</div>
                        <div className="mt-1 text-sm font-semibold">
                          {selected.actualAllow === null
                            ? '超时未判定'
                            : selected.actualAllow
                              ? '允许通行'
                              : '不允许通行（拦截）'}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          实际节点：<span className="font-mono text-foreground">{selected.actualNode || '无'}</span>
                        </div>
                      </div>
                    </div>

                    <div className="rounded-lg border border-border/80 bg-background p-3 text-xs">
                      <div className="mb-1 flex items-center gap-1.5 font-medium">
                        {selected.passed ? (
                          <CheckCircle2 className="size-3.5 text-emerald-500" />
                        ) : (
                          <AlertTriangle className="size-3.5 text-rose-500" />
                        )}
                        判定说明
                      </div>
                      <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                        {selected.reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                        {selected.resolutionNote && <li>归属依据：{selected.resolutionNote}</li>}
                      </ul>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Evidence Tabs */}
              <Tabs defaultValue="logs" className="w-full">
                <TabsList>
                  <TabsTrigger value="logs" className="text-xs">
                    会话日志切片
                  </TabsTrigger>
                  <TabsTrigger value="analysis" className="text-xs">
                    根因诊断分析 {selected.analysis ? '✓' : ''}
                  </TabsTrigger>
                  <TabsTrigger value="raw" className="text-xs">
                    采集与原始报文
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="logs" className="mt-3">
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                      <CardTitle className="text-xs font-medium">
                        该旅客关联日志切片（共 {logLines.length} 行）
                      </CardTitle>
                      <Input
                        value={logSearch}
                        onChange={(e) => setLogSearch(e.target.value)}
                        placeholder="过滤日志关键字…"
                        className="h-7 w-48 text-xs"
                      />
                    </CardHeader>
                    <CardContent>
                      <div className="max-h-72 overflow-auto rounded-lg border border-border bg-background p-3 font-mono text-[11px] leading-relaxed">
                        {logLines.length === 0 ? (
                          <div className="py-6 text-center text-muted-foreground">无关联日志切片</div>
                        ) : (
                          logLines.map((l) => {
                            const isFail = l.text.includes('执行失败') || l.text.includes('拒绝')
                            const isPass = l.text.includes('离开通道') || l.text.includes('创建新会话')
                            return (
                              <div
                                key={l.lineNo}
                                className={`flex gap-3 rounded px-1.5 py-0.5 ${
                                  isFail
                                    ? 'bg-rose-500/10 text-rose-400'
                                    : isPass
                                      ? 'bg-emerald-500/10 text-emerald-400'
                                      : 'text-foreground/85'
                                }`}
                              >
                                <span className="w-8 shrink-0 select-none text-right text-muted-foreground/60">
                                  {l.lineNo}
                                </span>
                                <span className="break-all whitespace-pre-wrap">{l.text}</span>
                              </div>
                            )
                          })
                        )}
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="analysis" className="mt-3">
                  <Card>
                    <CardContent className="pt-4 text-xs">
                      {!selected.analysis ? (
                        <div className="flex flex-col items-center gap-3 py-10 text-muted-foreground">
                          <Sparkles className="size-6 opacity-50" />
                          <p>尚未对该用例执行失败根因分析</p>
                          {!selected.passed && (
                            <Button
                              size="sm"
                              onClick={() => onAnalyzeCase(selected.id)}
                              disabled={analyzingId === selected.id}
                              className="bg-brand text-white hover:bg-brand-hover"
                            >
                              立即执行诊断分析
                            </Button>
                          )}
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <div className="rounded-lg border border-brand/30 bg-brand/5 p-3">
                            <div className="text-[11px] font-medium text-brand">核心根因</div>
                            <div className="mt-1 text-sm font-medium text-foreground">
                              {selected.analysis.rootCause}
                            </div>
                          </div>
                          <div>
                            <div className="mb-1 font-medium text-muted-foreground">可能原因</div>
                            <ul className="list-disc space-y-1 pl-5">
                              {selected.analysis.possibleCauses.map((c, idx) => (
                                <li key={idx}>{c}</li>
                              ))}
                            </ul>
                          </div>
                          <div>
                            <div className="mb-1 font-medium text-muted-foreground">建议解决方案</div>
                            <ul className="list-disc space-y-1 pl-5">
                              {selected.analysis.solutions.map((s, idx) => (
                                <li key={idx}>{s}</li>
                              ))}
                            </ul>
                          </div>
                          <div className="rounded-md bg-secondary/50 p-3 text-muted-foreground">
                            {selected.analysis.summary}
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="raw" className="mt-3">
                  <Card>
                    <CardContent className="pt-4">
                      <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-background p-3 font-mono text-[11px] text-foreground/85">
                        {JSON.stringify(
                          {
                            capture: { ...selected.capture, photo_base64: selected.photoData ? '<omitted>' : undefined },
                            gateMessages: selected.gateMessages,
                          },
                          null,
                          2
                        )}
                      </pre>
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
