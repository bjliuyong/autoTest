import { useCallback, useEffect, useState } from 'react'
import {
  ExternalLink,
  Play,
  Plus,
  RefreshCw,
  Square,
  Workflow,
  Zap,
} from 'lucide-react'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Input } from '@/app/components/ui/input'
import { Switch } from '@/app/components/ui/switch'
import { conveyor } from '@/conveyor/client'
import type { NodeRedFlowTab, NodeRedStatus } from '@/lib/autotest/types'

export function NodeRedPage() {
  const [status, setStatus] = useState<NodeRedStatus | null>(null)
  const [urlInput, setUrlInput] = useState('http://127.0.0.1:1880')
  const [flows, setFlows] = useState<NodeRedFlowTab[]>([])
  const [newFlowName, setNewFlowName] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [iframeKey, setIframeKey] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const s = await conveyor.autotest.noderedStatus()
      setStatus(s)
      setUrlInput(s.url)
      if (s.reachable) {
        const list = await conveyor.autotest.noderedListFlows()
        setFlows(list)
      } else {
        setFlows([])
      }
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleStartEmbedded = async () => {
    setBusy(true)
    setNotice('正在启动内嵌 Node-RED 服务…')
    try {
      const s = await conveyor.autotest.noderedStart(1880)
      setStatus(s)
      setUrlInput(s.url)
      if (s.reachable) {
        setNotice('内嵌 Node-RED 已就绪')
        const list = await conveyor.autotest.noderedListFlows()
        setFlows(list)
        setIframeKey((k) => k + 1)
      } else {
        setNotice(s.error || '启动失败，请确认已安装 node-red 或连接外部地址')
      }
    } finally {
      setBusy(false)
    }
  }

  const handleStopEmbedded = async () => {
    setBusy(true)
    try {
      const s = await conveyor.autotest.noderedStop()
      setStatus(s)
      setFlows([])
      setNotice('已停止内嵌 Node-RED')
    } finally {
      setBusy(false)
    }
  }

  const handleConnectUrl = async () => {
    setBusy(true)
    setNotice('')
    try {
      const s = await conveyor.autotest.noderedSetUrl(urlInput)
      setStatus(s)
      if (s.reachable) {
        const list = await conveyor.autotest.noderedListFlows()
        setFlows(list)
        setIframeKey((k) => k + 1)
        setNotice(`已连接到 ${s.url}`)
      } else {
        setFlows([])
        setNotice(`无法连接 ${s.url}，请确认 Node-RED 已启动`)
      }
    } finally {
      setBusy(false)
    }
  }

  const handleRunFlow = async (flow: NodeRedFlowTab) => {
    setNotice('')
    const res = await conveyor.autotest.noderedRunFlow(flow.id)
    if (res.ok) {
      setNotice(`已触发运行流程「${flow.label}」（共执行 ${res.triggeredCount} 个注入节点）`)
    } else {
      setNotice(res.error || '触发运行失败')
    }
  }

  const handleTriggerInject = async (flowLabel: string, nodeId: string, nodeName: string) => {
    const res = await conveyor.autotest.noderedTriggerInject(nodeId)
    setNotice(res.ok ? `已触发「${flowLabel} / ${nodeName}」` : res.error || '触发失败')
  }

  const handleToggleFlow = async (flow: NodeRedFlowTab) => {
    const updated = await conveyor.autotest.noderedToggleFlow({
      flowId: flow.id,
      disabled: !flow.disabled,
    })
    setFlows(updated)
    setIframeKey((k) => k + 1)
    setNotice(`流程「${flow.label}」已${!flow.disabled ? '禁用' : '启用'}`)
  }

  const handleCreateFlow = async () => {
    if (!newFlowName.trim()) return
    const updated = await conveyor.autotest.noderedCreateFlow(newFlowName.trim())
    setFlows(updated)
    setNewFlowName('')
    setIframeKey((k) => k + 1)
    setNotice('已创建并部署新流程，可在下方画布直接可视化编辑')
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Top Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/80 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <Workflow className="size-4 text-brand" />
          <span className="text-sm font-semibold">Node-RED 可视化流程编排</span>
          <Badge
            variant="outline"
            className={
              status?.reachable
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500 text-[11px]'
                : 'text-muted-foreground text-[11px]'
            }
          >
            {status?.reachable ? (status.managedRunning ? '内嵌运行中' : '外部已连接') : '未启动'}
          </Badge>
          {notice && <span className="max-w-xs truncate text-xs text-brand">{notice}</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Input
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="http://127.0.0.1:1880"
            className="h-7 w-48 font-mono text-xs"
          />
          <Button variant="outline" size="sm" onClick={handleConnectUrl} disabled={busy} className="h-7 text-xs">
            连接
          </Button>
          {status?.managedRunning ? (
            <Button variant="destructive" size="sm" onClick={handleStopEmbedded} disabled={busy} className="h-7 gap-1 text-xs">
              <Square className="size-3 fill-current" /> 停止内嵌
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={handleStartEmbedded}
              disabled={busy}
              className="h-7 gap-1 bg-brand text-xs text-white hover:bg-brand-hover"
            >
              <Play className="size-3 fill-current" /> 启动内嵌 Node-RED
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => { void refresh(); setIframeKey((k) => k + 1) }} className="h-7 px-2" title="刷新流程与画布">
            <RefreshCw className="size-3.5" />
          </Button>
          {status?.reachable && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void conveyor.autotest.noderedOpenExternal()}
              className="h-7 px-2"
              title="在外部浏览器打开"
            >
              <ExternalLink className="size-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Flow Runner & Quick Management Strip */}
      {status?.reachable && (
        <div className="flex flex-wrap items-center gap-2.5 border-b border-border bg-secondary/30 px-4 py-2">
          <div className="flex items-center gap-1.5">
            <Input
              value={newFlowName}
              onChange={(e) => setNewFlowName(e.target.value)}
              placeholder="新流程名称…"
              className="h-7 w-36 text-xs"
            />
            <Button size="sm" variant="outline" onClick={handleCreateFlow} className="h-7 gap-1 px-2.5 text-xs">
              <Plus className="size-3" /> 新建流程
            </Button>
          </div>

          <div className="h-4 w-px bg-border" />

          <div className="flex flex-1 flex-wrap items-center gap-2">
            {flows.length === 0 ? (
              <span className="text-xs text-muted-foreground">暂无流程标签页</span>
            ) : (
              flows.map((flow) => (
                <div
                  key={flow.id}
                  className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1 text-xs shadow-2xs"
                >
                  <Switch
                    checked={!flow.disabled}
                    onCheckedChange={() => void handleToggleFlow(flow)}
                    className="scale-75"
                  />
                  <span className={`font-medium ${flow.disabled ? 'line-through text-muted-foreground' : ''}`}>
                    {flow.label}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground">({flow.nodeCount}节点)</span>

                  {flow.injectNodes.length <= 1 ? (
                    <Button
                      size="sm"
                      disabled={flow.disabled}
                      onClick={() => void handleRunFlow(flow)}
                      className="h-6 gap-1 bg-emerald-600 px-2 text-[11px] text-white hover:bg-emerald-700"
                    >
                      <Play className="size-2.5 fill-current" /> 运行流程
                    </Button>
                  ) : (
                    <div className="flex items-center gap-1">
                      {flow.injectNodes.map((inj) => (
                        <Button
                          key={inj.id}
                          size="sm"
                          disabled={flow.disabled}
                          onClick={() => void handleTriggerInject(flow.label, inj.id, inj.name)}
                          className="h-6 gap-1 bg-emerald-600 px-2 text-[11px] text-white hover:bg-emerald-700"
                        >
                          <Zap className="size-2.5" /> {inj.name}
                        </Button>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Embedded Visual Node-RED Editor */}
      <div className="min-h-0 flex-1 bg-background">
        {status?.reachable ? (
          <iframe
            key={iframeKey}
            src={status.url}
            title="Node-RED Visual Editor"
            className="h-full w-full border-0"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
            <div className="flex size-14 items-center justify-center rounded-2xl bg-brand/10 text-brand">
              <Workflow className="size-7" />
            </div>
            <div className="max-w-md space-y-1.5">
              <h2 className="text-base font-semibold">可视化 Node-RED 流程编辑器未启动</h2>
              <p className="text-xs text-muted-foreground">
                点击下方按钮启动内嵌 Node-RED（默认自动加载「闸机自动化测试示例流程」），或在顶部输入已有 Node-RED 地址直接连线编辑与运行。
              </p>
              {status?.error && (
                <p className="rounded-md bg-rose-500/10 p-2 text-xs text-rose-500">{status.error}</p>
              )}
            </div>
            <Button
              onClick={handleStartEmbedded}
              disabled={busy}
              className="gap-1.5 bg-brand text-xs text-white hover:bg-brand-hover"
            >
              <Play className="size-3.5 fill-current" />
              {busy ? '正在启动内嵌 Node-RED…' : '一键启动内嵌 Node-RED 编辑器'}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
