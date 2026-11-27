import { useEffect, useState } from 'react'
import { Globe, RotateCcw, Save, Users } from 'lucide-react'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/app/components/ui/card'
import { Input } from '@/app/components/ui/input'
import { Label } from '@/app/components/ui/label'
import { Switch } from '@/app/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/app/components/ui/tabs'
import { Textarea } from '@/app/components/ui/textarea'
import { buildDynamicLogPath } from '@/lib/autotest/log-path'
import type { AppConfig, DatabaseInfo } from '@/lib/autotest/types'

interface SettingsPageProps {
  config: AppConfig | undefined
  dbInfo: DatabaseInfo | undefined
  serverUrl: string
  onSave: (cfg: AppConfig) => Promise<void>
  onReset: () => Promise<void>
  onPickLogFile: () => Promise<string | null>
  onPickLogDir: () => Promise<string | null>
}

export function SettingsPage({
  config,
  dbInfo,
  serverUrl,
  onSave,
  onReset,
  onPickLogFile,
  onPickLogDir,
}: SettingsPageProps) {
  const [draft, setDraft] = useState<AppConfig | null>(null)
  const [aliasesText, setAliasesText] = useState('{}')
  const [saving, setSaving] = useState(false)
  const [savedNotice, setSavedNotice] = useState('')

  useEffect(() => {
    if (config) {
      setDraft(structuredClone(config))
      setAliasesText(JSON.stringify(config.judge.nodeAliases ?? {}, null, 2))
    }
  }, [config])

  if (!draft) {
    return <div className="p-6 text-xs text-muted-foreground">正在加载配置…</div>
  }

  const handleSave = async () => {
    setSaving(true)
    setSavedNotice('')
    try {
      let parsedAliases = draft.judge.nodeAliases
      try {
        parsedAliases = JSON.parse(aliasesText || '{}')
      } catch {
        setSavedNotice('节点别名 JSON 格式有误，请检查后重试')
        setSaving(false)
        return
      }
      await onSave({
        ...draft,
        judge: { ...draft.judge, nodeAliases: parsedAliases },
      })
      setSavedNotice('配置已保存至 SQLite 数据库（重启测试生效）')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">系统与测试配置</h1>
          <p className="text-xs text-muted-foreground">
            所有配置项自动持久化到本地 SQLite 数据库，跨 Windows / macOS / Linux 无缝同步
          </p>
        </div>
        <div className="flex items-center gap-2">
          {savedNotice && <span className="text-xs text-emerald-500">{savedNotice}</span>}
          <Button variant="outline" size="sm" onClick={onReset} className="gap-1.5 text-xs">
            <RotateCcw className="size-3.5" />
            恢复默认
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={saving}
            className="gap-1.5 bg-brand text-xs text-white hover:bg-brand-hover"
          >
            <Save className="size-3.5" />
            {saving ? '保存中…' : '保存配置'}
          </Button>
        </div>
      </div>

      <Tabs defaultValue="channels" className="w-full">
        <TabsList>
          <TabsTrigger value="channels" className="text-xs">
            通道与日志接入
          </TabsTrigger>
          <TabsTrigger value="judge" className="text-xs">
            判定规则与正则
          </TabsTrigger>
          <TabsTrigger value="dsh" className="text-xs">
            DeepSeek Harness 分析
          </TabsTrigger>
          <TabsTrigger value="storage" className="text-xs">
            共享后端与在线成员
          </TabsTrigger>
        </TabsList>

        {/* Tab 1: Channels */}
        <TabsContent value="channels" className="mt-4 space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">闸机通道（Gate WebSocket）</CardTitle>
              <CardDescription className="text-xs">
                支持 client（主动连接）、server（本地监听）与 both（双模式同时收）
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">连接模式</Label>
                <select
                  value={draft.gate.mode}
                  onChange={(e) =>
                    setDraft({ ...draft, gate: { ...draft.gate, mode: e.target.value as AppConfig['gate']['mode'] } })
                  }
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs"
                >
                  <option value="both">both（主动连接 + 监听端口）</option>
                  <option value="client">client（主动连接闸机 WS）</option>
                  <option value="server">server（本地监听等待推流）</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">客户端连接地址 (gate.url)</Label>
                <Input
                  value={draft.gate.url}
                  onChange={(e) => setDraft({ ...draft, gate: { ...draft.gate, url: e.target.value } })}
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">服务端监听端口 (listenPort)</Label>
                <Input
                  type="number"
                  value={draft.gate.listenPort}
                  onChange={(e) =>
                    setDraft({ ...draft, gate: { ...draft.gate, listenPort: Number(e.target.value) || 8765 } })
                  }
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">服务端监听路径 (listenPath)</Label>
                <Input
                  value={draft.gate.listenPath}
                  onChange={(e) => setDraft({ ...draft, gate: { ...draft.gate, listenPath: e.target.value } })}
                  className="font-mono text-xs"
                />
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border p-3 md:col-span-2">
                <div>
                  <div className="text-xs font-medium">收到用例后发送回执 (gate.ack)</div>
                  <div className="text-[11px] text-muted-foreground">
                    接入 Node-RED 时建议关闭，避免向对方流程回写数据
                  </div>
                </div>
                <Switch
                  checked={draft.gate.sendAck}
                  onCheckedChange={(v) => setDraft({ ...draft, gate: { ...draft.gate, sendAck: v } })}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">待测系统日志来源（Log Channel）</CardTitle>
              <CardDescription className="text-xs">
                支持 WebSocket 实时推流，或直接跟随本地日志文件（支持相对路径 + 前缀 + 自动日期拼接，跨天自动切换）
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">日志来源类型</Label>
                <select
                  value={draft.logs.source}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      logs: { ...draft.logs, source: e.target.value as AppConfig['logs']['source'] },
                    })
                  }
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs"
                >
                  <option value="ws">WebSocket 推流 (ws)</option>
                  <option value="file">本地日志文件跟随 (file)</option>
                </select>
              </div>
              <div className={`space-y-1.5 transition-opacity ${draft.logs.source !== 'ws' ? 'opacity-50' : ''}`}>
                <Label className="text-xs">日志 WS 地址 (logs.url)</Label>
                <Input
                  disabled={draft.logs.source !== 'ws'}
                  value={draft.logs.url}
                  onChange={(e) => setDraft({ ...draft, logs: { ...draft.logs, url: e.target.value } })}
                  className="font-mono text-xs disabled:cursor-not-allowed disabled:bg-muted"
                />
              </div>

              <div className={`space-y-1.5 md:col-span-2 transition-opacity ${draft.logs.source !== 'file' ? 'opacity-50' : ''}`}>
                <Label className="text-xs">
                  日志目录 / 文件路径（支持相对路径，如 <code className="font-mono">./logs</code> 或 <code className="font-mono">D:/code/work/build/bin/logs</code>）
                </Label>
                <div className="flex gap-2">
                  <Input
                    disabled={draft.logs.source !== 'file'}
                    value={draft.logs.filePath}
                    onChange={(e) => setDraft({ ...draft, logs: { ...draft.logs, filePath: e.target.value } })}
                    placeholder="填写日志目录（配合下方前缀+日期）或完整文件路径"
                    className="font-mono text-xs disabled:cursor-not-allowed disabled:bg-muted"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={draft.logs.source !== 'file'}
                    onClick={async () => {
                      const picked = await onPickLogDir()
                      if (picked) setDraft({ ...draft, logs: { ...draft.logs, filePath: picked } })
                    }}
                  >
                    选择目录
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={draft.logs.source !== 'file'}
                    onClick={async () => {
                      const picked = await onPickLogFile()
                      if (picked) setDraft({ ...draft, logs: { ...draft.logs, filePath: picked, filePrefix: '' } })
                    }}
                  >
                    选择文件
                  </Button>
                </div>
              </div>

              <div className={`space-y-1.5 transition-opacity ${draft.logs.source !== 'file' ? 'opacity-50' : ''}`}>
                <Label className="text-xs">日志文件前缀（可选，如 biisapp_）</Label>
                <Input
                  disabled={draft.logs.source !== 'file'}
                  value={draft.logs.filePrefix ?? ''}
                  onChange={(e) => setDraft({ ...draft, logs: { ...draft.logs, filePrefix: e.target.value } })}
                  placeholder="留空则直接读取上方完整路径；填 biisapp_ 自动拼日期"
                  className="font-mono text-xs disabled:cursor-not-allowed disabled:bg-muted"
                />
              </div>

              <div className={`grid grid-cols-2 gap-2 transition-opacity ${draft.logs.source !== 'file' ? 'opacity-50' : ''}`}>
                <div className="space-y-1.5">
                  <Label className="text-xs">自动日期格式</Label>
                  <select
                    disabled={draft.logs.source !== 'file'}
                    value={draft.logs.fileDateFormat ?? 'YYYY-MM-DD'}
                    onChange={(e) => setDraft({ ...draft, logs: { ...draft.logs, fileDateFormat: e.target.value } })}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-xs disabled:cursor-not-allowed disabled:bg-muted"
                  >
                    <option value="YYYY-MM-DD">YYYY-MM-DD (2026-11-27)</option>
                    <option value="YYYYMMDD">YYYYMMDD (20261127)</option>
                    <option value="YYYY_MM_DD">YYYY_MM_DD (2026_11_27)</option>
                    <option value="">不拼接日期</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">文件后缀</Label>
                  <Input
                    disabled={draft.logs.source !== 'file'}
                    value={draft.logs.fileExtension ?? '.log'}
                    onChange={(e) => setDraft({ ...draft, logs: { ...draft.logs, fileExtension: e.target.value } })}
                    placeholder=".log"
                    className="font-mono text-xs disabled:cursor-not-allowed disabled:bg-muted"
                  />
                </div>
              </div>

              {draft.logs.source === 'file' && (
                <div className="rounded-lg border border-brand/30 bg-brand/5 px-3 py-2 text-xs md:col-span-2">
                  <span className="text-muted-foreground">今日自动解析日志路径预览：</span>
                  <span className="ml-1 font-mono font-medium text-brand">
                    {buildDynamicLogPath(draft.logs) || '（请填写目录/文件路径或日志前缀）'}
                  </span>
                </div>
              )}

              <div className={`space-y-1.5 transition-opacity ${draft.logs.source !== 'file' ? 'opacity-50' : ''}`}>
                <Label className="text-xs">文件编码</Label>
                <select
                  disabled={draft.logs.source !== 'file'}
                  value={draft.logs.fileEncoding}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      logs: { ...draft.logs, fileEncoding: e.target.value as AppConfig['logs']['fileEncoding'] },
                    })
                  }
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs disabled:cursor-not-allowed disabled:bg-muted"
                >
                  <option value="auto">auto（自动识别 UTF-8 / GBK）</option>
                  <option value="utf-8">UTF-8</option>
                  <option value="gbk">GBK</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">HTTP 推用例接口端口 (POST /api/case)</Label>
                <Input
                  type="number"
                  value={draft.run.httpApiPort}
                  onChange={(e) =>
                    setDraft({ ...draft, run: { ...draft.run, httpApiPort: Number(e.target.value) || 8767 } })
                  }
                  className="font-mono text-xs"
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Judge & Regex */}
        <TabsContent value="judge" className="mt-4 space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">判定参数与正则模板</CardTitle>
              <CardDescription className="text-xs">
                每行一条正则表达式，任意一条命中即生效（兼容 Python 命名分组语法）
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs">单条用例超时时间（秒）</Label>
                  <Input
                    type="number"
                    value={draft.run.caseTimeoutSeconds}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        run: { ...draft.run, caseTimeoutSeconds: Math.max(2, Number(e.target.value) || 30) },
                      })
                    }
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">旅客匹配字段顺序（逗号分隔）</Label>
                  <Input
                    value={draft.judge.passengerKeys.join(', ')}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        judge: {
                          ...draft.judge,
                          passengerKeys: e.target.value
                            .split(',')
                            .map((s) => s.trim())
                            .filter(Boolean),
                        },
                      })
                    }
                    className="font-mono text-xs"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">创建会话正则 (sessionCreate)</Label>
                <Textarea
                  rows={2}
                  value={draft.patterns.sessionCreate.join('\n')}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      patterns: { ...draft.patterns, sessionCreate: e.target.value.split('\n').filter(Boolean) },
                    })
                  }
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">节点执行失败正则 (nodeFailure)</Label>
                <Textarea
                  rows={2}
                  value={draft.patterns.nodeFailure.join('\n')}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      patterns: { ...draft.patterns, nodeFailure: e.target.value.split('\n').filter(Boolean) },
                    })
                  }
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">旅客放行/离开通道正则 (passengerExit)</Label>
                <Textarea
                  rows={4}
                  value={draft.patterns.passengerExit.join('\n')}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      patterns: { ...draft.patterns, passengerExit: e.target.value.split('\n').filter(Boolean) },
                    })
                  }
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">节点别名映射 (JSON，例如 {`{"证照核验": "证件核验"}`})</Label>
                <Textarea
                  rows={3}
                  value={aliasesText}
                  onChange={(e) => setAliasesText(e.target.value)}
                  className="font-mono text-xs"
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 3: DSH */}
        <TabsContent value="dsh" className="mt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">DeepSeek Harness (DSH) 失败分析集成</CardTitle>
              <CardDescription className="text-xs">
                关闭时使用内置快速规则诊断；开启后可调用本机已安装的 dsh CLI 进行深度代码级分析
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between rounded-lg border border-border p-3">
                <div>
                  <div className="text-xs font-medium">启用外部 DSH CLI 深度分析</div>
                  <div className="text-[11px] text-muted-foreground">需确保本机已配置 dsh 命令及对应凭据</div>
                </div>
                <Switch
                  checked={draft.dsh.enabled}
                  onCheckedChange={(v) => setDraft({ ...draft, dsh: { ...draft.dsh, enabled: v } })}
                />
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs">DSH 命令路径</Label>
                  <Input
                    value={draft.dsh.command}
                    onChange={(e) => setDraft({ ...draft, dsh: { ...draft.dsh, command: e.target.value } })}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">Profile 名称</Label>
                  <Input
                    value={draft.dsh.profile}
                    onChange={(e) => setDraft({ ...draft, dsh: { ...draft.dsh, profile: e.target.value } })}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">待测源码仓库路径 (codePath)</Label>
                  <Input
                    value={draft.dsh.codePath}
                    onChange={(e) => setDraft({ ...draft, dsh: { ...draft.dsh, codePath: e.target.value } })}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">修复分支模板</Label>
                  <Input
                    value={draft.dsh.branchTemplate}
                    onChange={(e) => setDraft({ ...draft, dsh: { ...draft.dsh, branchTemplate: e.target.value } })}
                    className="font-mono text-xs"
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 4: Shared Backend & Online Team */}
        <TabsContent value="storage" className="mt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Globe className="size-4 text-brand" />
                共享后端服务器与团队连线状态
              </CardTitle>
              <CardDescription className="text-xs">
                所有测试批次、用例追溯、配置与用户数据均集中存储在共享后端（支持 MySQL 8），团队成员实时共享
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 text-xs">
              <div className="rounded-lg border border-border bg-secondary/40 p-3.5">
                <div className="text-[11px] text-muted-foreground">当前已连接后端服务器</div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="break-all font-mono text-foreground">{serverUrl}</span>
                  <Badge variant="outline" className="border-emerald-500/40 text-emerald-500">
                    存储引擎：{dbInfo?.driver ?? 'shared'}
                  </Badge>
                </div>
                <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                  数据位置：{dbInfo?.location ?? '-'}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-border p-3">
                  <div className="text-[11px] text-muted-foreground">团队成员数</div>
                  <div className="mt-1 font-mono text-base font-semibold">{dbInfo?.userCount ?? 0}</div>
                </div>
                <div className="rounded-lg border border-border p-3">
                  <div className="text-[11px] text-muted-foreground">共享批次数</div>
                  <div className="mt-1 font-mono text-base font-semibold">{dbInfo?.batchCount ?? 0}</div>
                </div>
                <div className="rounded-lg border border-border p-3">
                  <div className="text-[11px] text-muted-foreground">共享用例数</div>
                  <div className="mt-1 font-mono text-base font-semibold">{dbInfo?.caseCount ?? 0}</div>
                </div>
                <div className="rounded-lg border border-border p-3">
                  <div className="text-[11px] text-muted-foreground">系统日志条数</div>
                  <div className="mt-1 font-mono text-base font-semibold">{dbInfo?.logCount ?? 0}</div>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Users className="size-3.5 text-brand" /> 当前在线连线成员（{dbInfo?.onlineUsers?.length ?? 0} 人）
                </div>
                <div className="flex flex-wrap gap-2">
                  {(dbInfo?.onlineUsers ?? []).map((u, idx) => (
                    <Badge key={`${u.username}-${idx}`} variant="secondary" className="gap-1.5 py-1 text-xs">
                      <span className="size-2 rounded-full bg-emerald-500" />
                      {u.nickname} ({u.username}) · {u.roleName}
                    </Badge>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
