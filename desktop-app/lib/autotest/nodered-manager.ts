import { spawn, type ChildProcess } from 'child_process'
import crypto from 'crypto'
import fs from 'fs'
import path from 'path'
import { app } from 'electron'
import type { NodeRedFlowTab, NodeRedStatus } from './types'

const STARTER_FLOWS = [
  {
    id: 'gate-demo-tab',
    type: 'tab',
    label: '闸机自动化测试示例流程',
    disabled: false,
    info: '点击顶部「运行流程」或点击左侧注入按钮，即可向本程序 HTTP 推用例接口发送一条测试报文。',
  },
  {
    id: 'gate-demo-inject',
    type: 'inject',
    z: 'gate-demo-tab',
    name: '触发单条闸机用例',
    props: [{ p: 'payload' }],
    repeat: '',
    crontab: '',
    once: false,
    onceDelay: 0.1,
    topic: '',
    payload: '',
    payloadType: 'date',
    x: 160,
    y: 120,
    wires: [['gate-demo-fn']],
  },
  {
    id: 'gate-demo-fn',
    type: 'function',
    z: 'gate-demo-tab',
    name: '构造 gate.capture 报文',
    func: [
      'const seq = String(Date.now()).slice(-4);',
      'msg.headers = { "Content-Type": "application/json" };',
      'msg.payload = {',
      '  type: "gate.capture",',
      '  case_id: `NR-CASE-${seq}`,',
      '  serial: `NR-${seq}_1`,',
      '  expected: { allow_pass: true, failed_node: null },',
      '  capture: {',
      '    passenger_name: "NodeRED模拟旅客",',
      '    cert_type: "ID_CARD",',
      '    cert_no: `11010119900101${seq}`,',
      '    gate_id: "G01"',
      '  }',
      '};',
      'return msg;',
    ].join('\n'),
    outputs: 1,
    noerr: 0,
    x: 410,
    y: 120,
    wires: [['gate-demo-http', 'gate-demo-debug']],
  },
  {
    id: 'gate-demo-http',
    type: 'http request',
    z: 'gate-demo-tab',
    name: '推送到测试引擎 (8767)',
    method: 'POST',
    ret: 'obj',
    paytoqs: 'ignore',
    url: 'http://127.0.0.1:8767/api/case',
    x: 680,
    y: 100,
    wires: [['gate-demo-debug']],
  },
  {
    id: 'gate-demo-debug',
    type: 'debug',
    z: 'gate-demo-tab',
    name: '查看回执结果',
    active: true,
    tosidebar: true,
    console: false,
    tostatus: false,
    complete: 'payload',
    targetType: 'msg',
    x: 670,
    y: 180,
    wires: [],
  },
]

interface RawFlowNode {
  id: string
  type: string
  label?: string
  name?: string
  z?: string
  disabled?: boolean
  info?: string
  [key: string]: unknown
}

export class NodeRedManager {
  private url = 'http://127.0.0.1:1880'
  private port = 1880
  private child: ChildProcess | null = null
  private lastError = ''

  private resolveRedEntry(): string | null {
    const candidates = [
      path.join(app.getAppPath(), 'node_modules', 'node-red', 'red.js'),
      path.join(process.cwd(), 'node_modules', 'node-red', 'red.js'),
      path.join(process.resourcesPath ?? '', 'app.asar.unpacked', 'node_modules', 'node-red', 'red.js'),
    ]
    for (const p of candidates) {
      if (p && fs.existsSync(p)) return p
    }
    try {
      return require.resolve('node-red/red.js')
    } catch {
      return null
    }
  }

  getUrl(): string {
    return this.url
  }

  setUrl(nextUrl: string): string {
    const cleaned = nextUrl.trim().replace(/\/+$/, '') || 'http://127.0.0.1:1880'
    this.url = cleaned
    try {
      const parsed = new URL(cleaned)
      if (parsed.port) this.port = Number(parsed.port) || 1880
    } catch {
      // ignore
    }
    return this.url
  }

  private async checkReachable(): Promise<boolean> {
    try {
      const res = await fetch(`${this.url}/flows`, { signal: AbortSignal.timeout(2000) })
      return res.ok
    } catch {
      return false
    }
  }

  async getStatus(): Promise<NodeRedStatus> {
    const reachable = await this.checkReachable()
    return {
      url: this.url,
      reachable,
      managedRunning: this.child !== null && !this.child.killed,
      embeddedAvailable: this.resolveRedEntry() !== null,
      port: this.port,
      error: this.lastError || undefined,
    }
  }

  async startEmbedded(port = 1880): Promise<NodeRedStatus> {
    this.port = port || 1880
    this.url = `http://127.0.0.1:${this.port}`
    this.lastError = ''

    if (await this.checkReachable()) {
      return this.getStatus()
    }

    const redJs = this.resolveRedEntry()
    if (!redJs) {
      this.lastError = '未检测到内嵌 node-red 依赖，可直接填写外部 Node-RED 地址连接，或在 desktop-app 中安装 node-red'
      return this.getStatus()
    }

    const userDir = path.join(app.getPath('userData'), 'nodered')
    fs.mkdirSync(userDir, { recursive: true })
    const flowsFile = path.join(userDir, 'flows.json')
    if (!fs.existsSync(flowsFile)) {
      fs.writeFileSync(flowsFile, JSON.stringify(STARTER_FLOWS, null, 2), 'utf-8')
    }

    // Explicitly point Node-RED nodesDir to bundled contrib packages so dynamic-websocket & http-request always load offline
    const nmRoot = path.resolve(path.dirname(redJs), '..')
    const extraNodesDirs = [
      path.join(nmRoot, 'node-red-contrib-dynamic-websocket'),
      path.join(nmRoot, 'node-red-contrib-http-request'),
    ].filter((d) => fs.existsSync(d))
    const settingsFile = path.join(userDir, 'settings.js')
    fs.writeFileSync(
      settingsFile,
      `module.exports = {\n  nodesDir: ${JSON.stringify(extraNodesDirs)},\n  editorTheme: { tours: false }\n};\n`,
      'utf-8'
    )

    // Use Electron's built-in Node runtime (ELECTRON_RUN_AS_NODE=1) so it works cross-platform without system node
    const proc = spawn(
      process.execPath,
      [redJs, '--port', String(this.port), '--userDir', userDir, '--settings', settingsFile, flowsFile],
      {
        env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      }
    )
    this.child = proc
    proc.stderr?.on('data', (chunk) => {
      const msg = chunk.toString('utf-8').trim()
      if (msg) this.lastError = msg.slice(-240)
    })
    proc.on('exit', () => {
      if (this.child === proc) this.child = null
    })

    // Wait up to 12 seconds for Node-RED HTTP server to become ready
    const deadline = Date.now() + 12000
    while (Date.now() < deadline) {
      if (await this.checkReachable()) {
        this.lastError = ''
        break
      }
      await new Promise((r) => setTimeout(r, 400))
    }

    return this.getStatus()
  }

  async stopEmbedded(): Promise<NodeRedStatus> {
    if (this.child) {
      try {
        this.child.kill()
      } catch {
        // ignore
      }
      this.child = null
    }
    return this.getStatus()
  }

  async listFlows(): Promise<NodeRedFlowTab[]> {
    const res = await fetch(`${this.url}/flows`, { signal: AbortSignal.timeout(4000) })
    if (!res.ok) throw new Error(`无法获取 Node-RED 流程列表 (HTTP ${res.status})`)
    const nodes = (await res.json()) as RawFlowNode[]
    const tabs = nodes.filter((n) => n.type === 'tab')
    return tabs.map((tab) => {
      const children = nodes.filter((n) => n.z === tab.id)
      const injects = children
        .filter((n) => n.type === 'inject')
        .map((n) => ({ id: n.id, name: n.name?.trim() || 'Inject 触发节点' }))
      return {
        id: tab.id,
        label: tab.label?.trim() || '未命名流程',
        disabled: Boolean(tab.disabled),
        info: String(tab.info || ''),
        nodeCount: children.length,
        injectNodes: injects,
      }
    })
  }

  async triggerInject(nodeId: string): Promise<{ ok: boolean; error?: string }> {
    const postInject = () =>
      fetch(`${this.url}/inject/${encodeURIComponent(nodeId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        signal: AbortSignal.timeout(5000),
      })

    try {
      let res = await postInject()
      if (res.status === 404) {
        // Check if Node-RED flows are currently stopped; if so, start them and retry once
        try {
          const stRes = await fetch(`${this.url}/flows/state`, { signal: AbortSignal.timeout(2000) })
          if (stRes.ok) {
            const st = (await stRes.json()) as { state?: string }
            if (st.state === 'stop') {
              await fetch(`${this.url}/flows/state`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ state: 'start' }),
                signal: AbortSignal.timeout(3000),
              })
              res = await postInject()
            }
          }
        } catch {
          // ignore
        }
      }
      if (!res.ok) {
        if (res.status === 404) {
          return {
            ok: false,
            error: '触发失败 (404)：节点未生效，请先在下方 Node-RED 右上角点击「部署 / Deploy」并刷新流程列表',
          }
        }
        return { ok: false, error: `触发失败 (HTTP ${res.status})` }
      }
      return { ok: true }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  }

  async runFlow(flowId: string): Promise<{ ok: boolean; triggeredCount: number; error?: string }> {
    const tabs = await this.listFlows()
    const target = tabs.find((t) => t.id === flowId)
    if (!target) return { ok: false, triggeredCount: 0, error: '未找到指定流程' }
    if (target.disabled) return { ok: false, triggeredCount: 0, error: '该流程处于禁用状态，请先启用' }
    if (target.injectNodes.length === 0) {
      return {
        ok: false,
        triggeredCount: 0,
        error: '该流程中没有 Inject 触发节点，请在下方编辑器中拖入一个 Inject 节点并点击部署',
      }
    }
    let count = 0
    let lastError: string | undefined
    for (const inj of target.injectNodes) {
      const r = await this.triggerInject(inj.id)
      if (r.ok) count++
      else if (r.error) lastError = r.error
    }
    return { ok: count > 0, triggeredCount: count, error: count > 0 ? undefined : lastError }
  }

  private async fetchFlowsV2(): Promise<{ rev?: string; flows: RawFlowNode[] }> {
    const res = await fetch(`${this.url}/flows`, {
      headers: { 'Node-RED-API-Version': 'v2' },
      signal: AbortSignal.timeout(4000),
    })
    if (!res.ok) throw new Error(`读取流程失败 (HTTP ${res.status})`)
    const data = (await res.json()) as { rev?: string; flows?: RawFlowNode[] } | RawFlowNode[]
    if (Array.isArray(data)) return { flows: data }
    return { rev: data.rev, flows: data.flows ?? [] }
  }

  private async deployFlowsV2(flows: RawFlowNode[], rev?: string): Promise<void> {
    const deploy = await fetch(`${this.url}/flows`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Node-RED-API-Version': 'v2',
        'Node-RED-Deployment-Type': 'full',
      },
      body: JSON.stringify(rev ? { rev, flows } : { flows }),
      signal: AbortSignal.timeout(6000),
    })
    if (!deploy.ok) throw new Error(`部署流程失败 (HTTP ${deploy.status})`)
  }

  async toggleFlow(flowId: string, disabled: boolean): Promise<NodeRedFlowTab[]> {
    const { rev, flows } = await this.fetchFlowsV2()
    const updated = flows.map((n) => (n.type === 'tab' && n.id === flowId ? { ...n, disabled } : n))
    await this.deployFlowsV2(updated, rev)
    return this.listFlows()
  }

  async createFlow(label: string): Promise<NodeRedFlowTab[]> {
    const { rev, flows } = await this.fetchFlowsV2()
    const tabId = `flow-${crypto.randomBytes(4).toString('hex')}`
    const injId = `inj-${crypto.randomBytes(4).toString('hex')}`
    const dbgId = `dbg-${crypto.randomBytes(4).toString('hex')}`
    const newNodes: RawFlowNode[] = [
      ...flows,
      { id: tabId, type: 'tab', label: label.trim() || '新建测试流程', disabled: false, info: '' },
      {
        id: injId,
        type: 'inject',
        z: tabId,
        name: '手动触发',
        props: [{ p: 'payload' }],
        payload: '',
        payloadType: 'date',
        x: 160,
        y: 120,
        wires: [[dbgId]],
      },
      {
        id: dbgId,
        type: 'debug',
        z: tabId,
        name: '调试输出',
        active: true,
        tosidebar: true,
        complete: 'payload',
        targetType: 'msg',
        x: 400,
        y: 120,
        wires: [],
      },
    ]
    await this.deployFlowsV2(newNodes, rev)
    return this.listFlows()
  }
}

export const nodeRedManager = new NodeRedManager()
