# 闸机自动化测试 + DeepSeek Harness 失败分析

一个持续运行的自动化测试程序：

1. 开 **两个 WebSocket 连接**：闸机程序（采集信息 + 预期结果）与待测系统日志；
2. 每条用例用「预期结果」和「日志推断的实际结果」比对 —— **是否允许通行** 与 **失败节点名称** 都一致才算通过；
3. 不通过的用例立刻生成报告（**一张照片 + 一份 JSON 体表格 + 该旅客的日志切片**），并把分析任务**落盘入队**；
4. 单 worker **严格串行**消费队列：调用 DeepSeek Harness 综合分析 → 可能原因 → 解决办法 → 自动改代码 → 提交到「时间+修复&lt;节点&gt;error」分支。

测试本身**永不因为失败而中断**：判定、报告、分析都在后台任务里，异常只记录不抛出。

> 📘 **[命令与配置手册（完整版）](docs/命令与配置手册.md)** —— 所有命令、所有配置项（含默认值）、
> 产物目录、判定规则、排查表格。下面只是概览，细节以手册为准。

---

## 一、快速开始

```bash
pip install -r requirements.txt

# 正常跑（连真实闸机与真实系统日志）
python run_autotest.py

# 本地自测：先起模拟服务端，再跑一遍全链路
python tools/mock_servers.py                # 终端 1
python run_autotest.py -c config.selftest.yaml --max-cases 4   # 终端 2
```

其他常用参数：

| 参数 | 说明 |
| --- | --- |
| `-c/--config` | 配置文件路径，默认 `config.yaml` |
| `--no-dsh` | 只跑测试 + 报告，不调用 DeepSeek Harness |
| `--max-analyses N` | 本次最多分析 N 条失败用例（其余标记跳过） |
| `--max-cases N` | 收到 N 条用例后，等判定+分析队列清空再退出（自测/CI） |
| `--duration SECONDS` | 跑够 N 秒后优雅退出 |
| `--log-level` | `DEBUG/INFO/WARNING/ERROR` |

`Ctrl+C` 优雅退出：**已入队的分析任务留在磁盘队列里**，下次启动自动接着做。

---

## 二、WebSocket 协议

Python 端是**客户端**，闸机程序与待测系统是服务端，由服务端主动推送。

### 1. 闸机通道（`ws://127.0.0.1:8765/gate`，可配）

闸机每采集一位旅客就推一条 JSON 文本帧，**采集信息与预期结果一起发**：

```json
{
  "type": "gate.capture",
  "case_id": "C-000123",
  "timestamp": "2026-02-14T10:00:00+08:00",
  "expected": {
    "allow_pass": false,
    "failed_node": "证件核验"
  },
  "capture": {
    "passenger_name": "张三",
    "cert_type": "ID_CARD",
    "cert_no": "110101199003071234",
    "gate_id": "G01",
    "photo_base64": "data:image/jpeg;base64,/9j/4AAQ..."
  }
}
```

* `expected.allow_pass`（必填）：是否允许通行；
* `expected.failed_node`：不允许通行时**预期失败的节点名称**；
* `capture`：闸机采集的证件信息，**原样透传**给分析模型，照片支持
  `data URL` / 纯 base64 / `http(s)` 地址 / 本地文件路径；
* `case_id` 可省略（自动生成），`timestamp` 可省略。

容错写法（老闸机快速接入）：`expected` 可省略，直接把
`allow_pass` / `pass` / `allow` 与 `failed_node` / `node` 放在顶层；`capture`
省略时顶层其余字段即采集信息。`allow_pass` 支持 `true/false`、`"true"/"false"`、
`1/0`、`"允许"/"不允许"`、`"pass"/"deny"`。

收到用例后可回执（`gate.send_ack` 控制）：

```json
{"type": "gate.ack", "case_id": "C-000123", "status": "accepted", "reason": ""}
```

### 2. 日志通道（`ws://127.0.0.1:8766/logs`，可配）

两种格式都支持，`logs.message_format` 可选 `auto`（默认）/ `text` / `json`：

```text
[流程引擎]创建新会话S0001
```

```json
{"type": "log", "line": "[流程引擎]创建新会话S0001"}
```

JSON 包体按 `logs.text_fields`（默认 `line/text/message/msg/content/log`…）取正文，
一条消息里有多行会自动拆成多行；也支持 `{"lines": [...]}` 这种批量形式。

---

## 三、判定规则

| 日志内容 | 含义 |
| --- | --- |
| `会话ID[xx]-节点yy执行失败` | 实际**不允许通行**，失败节点 = `yy` |
| `旅客[zz]离开通道` | 实际**允许通行** |

用例通过的**唯一标准**：`是否允许通行` 与 `失败节点名称` 都与预期一致
（预期允许通行时不比对节点）。

实现要点：

* **会话切片**：该旅客的日志从 `[流程引擎]创建新会话<会话ID>` 开始，到**整个日志流中最后一次出现该会话 ID**
  （以及该旅客标识）的那一行为止；
* **用例归属**：用采集信息里的 `cert_no` / `passenger_name`（`judge.passenger_keys` 可配）去匹配日志里的
  `旅客[...]`，并给会话打上「已归属」标记，**后到的用例不会抢走前一条用例的会话**；
* **节点归一化**：全角转半角、去空白标点、小写后比对，可再配 `judge.node_aliases` 别名表
  （例如把日志里的「证照核验」映射成「证件核验」）；
* **超时兜底**：`run.case_timeout_seconds` 内既没失败也没离开通道 → 判为不通过，
  原因写「超时仍未…未判定实际结果」，报告里附带最近的日志；
* **自相矛盾**：同一会话既有失败又有离开通道 → 默认判为不通过并提示人工复核
  （`judge.contradiction_as_failure`）。

正则都能在 `patterns` 里改，适配你系统真实的日志格式，不用动代码。

---

## 四、失败用例分析（DeepSeek Harness）

### 1. 产物

每条用例一个目录 `runs/<批次>/cases/<case_id>/`：

| 文件 | 内容 |
| --- | --- |
| `case.json` | 用例数据的 **JSON 体表格**（采集信息 / 预期 / 实际 / 判定原因；照片 base64 已剥离） |
| `logs.txt` | 该旅客的日志切片（交给模型的就是这份） |
| `photo.png` | 闸机采集照片 |
| `report.html` | **照片 + JSON 表格 + 日志 + 分析结论**，可直接浏览器打开 |
| `report.md` | 同上，Markdown 版 |
| `analysis.json` | 模型产出的结构化结论（流水线读取入口） |
| `analysis_prompt.md` | 实际发给 Harness 的提示词（便于复现） |
| `dsh_stdout.log` / `dsh_stderr.log` | Harness 原始输出 |

`runs/<批次>/summary.json` 实时汇总，`runs/_queue/{pending,running,done,failed}/` 是分析队列的落盘缓存。

### 2. 怎么调用

用 DSH 的 `headless` profile（回答一个任务、打印结果、退出）：

```bash
node <cli_js> --profile headless "<提示词>"
```

* 提示词里**只放文件路径**（日志正文走文件），避免 Windows 32K 命令行上限；
* 子进程 stdout/stderr 直接重定向到文件，不用管道，兼容受限 shell/CI；
* 模型自己用工具读 `case.json` / `logs.txt`，并用 `read_image` 看照片；
* 模型必须把结论写成 `analysis.json`，结构：

```json
{
  "status": "done",
  "root_cause": "一句话根因",
  "possible_causes": ["按可能性排序，每条附日志证据"],
  "solutions": ["具体解决办法"],
  "code_changes": ["改了哪个文件哪一段"],
  "confidence": "high|medium|low",
  "summary": "给测试同学的中文总结"
}
```

### 3. 自动改代码 + 提交分支

配好 `fix.repo_path` 指向待测系统仓库后：

1. 脚本先按模板建分支：`fix.branch_template: '{time}-修复{node}error'`，
   例如 `20261020-153012-修复证件核验error`（非法字符自动清洗）；
2. 模型在该分支上改代码并 `git commit`（提示词明确要求**不要 push**）；
3. 脚本收尾：模型漏提交时自动补一次提交 → 校验提交 → 按 `fix.push` 决定是否
   `git push -u <remote> <branch>`；
4. 分支名/提交号/推送结果回写 `analysis.json` 与报告。

`fix.repo_path` 留空就只做分析（报告里给出「建议改动」的位置），不碰任何代码。

**仓库里只有某个子目录是测试程序相关代码？** 填 `fix.subdir`：

```yaml
fix:
  repo_path: D:/Work/Project/gate-platform   # git 根（含 .git），分支/提交都在这层
  subdir: gate-system                        # 只改这个目录
  commit_scope: subdir                       # 只提交这个目录的改动（默认）
```

效果：提示词里明确「只改 `gate-platform/gate-system`」，提交时用
`git add -A -- gate-system` + `git commit -- gate-system`，**仓库里其它无关脏文件不会被带上**。
（git 仍作用于仓库根，所以提交正常；只是想验证更精确 → 用 `commit_scope: all` 也能全量提交。）

**没有代码时是怎么分析的？** 只用三样东西：`case.json`（采集/预期/实际/判定原因）、
`logs.txt`（该会话日志切片）、`photo.*`（照片）。模型从**日志原文**里找证据（如
`原因=证件有效期已过期 expire=2020-01-01` 与 `captured_at` 对比），据此归类
用例问题 / 程序问题 / 环境问题，并给出建议改动位置 —— 但**定位不到具体代码行、也改不了代码**。
想让它更准，就在日志里多打：入参、失败原因、关键字段值。

### 4. 分析以代码为主（默认），照片不参与

只靠日志定位不到实现，容易猜错。默认配置下模型**必须读源码**再下结论：

```yaml
dsh:
  code_analysis: true        # 分析读取代码（默认开）
  code_path: null            # 代码目录；不配就用 fix.repo_path
  code_subdir: null          # 只看代码的某个子目录
  repo_map: true             # 生成代码地图（定位可疑位置）
  use_photo: false           # 照片不参与分析（默认）
```

配了代码后，提示词会变成"代码优先"的四步：

1. 打开 `case.json` / `logs.txt`，列出关键字段值（证件号、有效期、相似度、时间戳）；
2. 打开代码地图里的**高度可疑文件**，定位到处理这些字段的函数/分支，记下 `文件:行号`；
3. 对照日志：这段代码被执行时的入参是什么、走了哪个分支、为什么产生该结果；
4. 若**代码与日志对不上**（代码该抛 A，日志却是 B），必须把矛盾写出来——这往往是最有价值的发现。

结论 JSON 里 `code_evidence` 必填：

```json
"code_evidence": [{"file": "gate-system/service/cert_verify.py", "lines": "17-23",
                   "snippet": "baseline = \"2020-01-02\"\nif expire >= baseline: raise Denied(...)",
                   "why": "基准硬编码 + 比较方向写反，有效期正常的证件也被判过期"}]
```

还有 `missing_observability`：日志证据不足时，列出应该补打的日志/字段。

**实测**（造假仓库 + 真 DSH）：日志 `原因=证件有效期已过期` + 源码里确实有这个 bug，
分析结果 `category=sut_issue`，`code_evidence` 精确到 `cert_verify.py:17-23`，
并额外发现"日志里的 expire 按该代码不会触发拒绝，说明日志与代码版本不一致"。

> 只靠日志也能跑（`code_analysis: false` 或不配代码目录），但定位不到代码行，
> `confidence` 会自动降级，并在 `missing_observability` 里说明缺什么。

### 5. 让分析更快：代码地图（已内置）

`dsh --profile headless` 是**一次性进程**（跑完退出，**不支持 `--resume`**），所以没法靠
会话复用"提前读一遍代码"。替代做法：分析前在本地用日志原文**搜源码**，把可疑位置直接指给模型。

内容：
* **高度可疑的代码位置** —— 拿日志里的失败原文（`原因=…`、失败行、节点名）去搜源码，
  给出 `文件:行号 + 代码片段`（git 仓库用 `git grep`；普通目录用受限遍历 + 内容搜索）；
* **最近改动过的文件**（`git log -5 --name-only`）——帮助判断"是不是刚改坏的"；
* 目录分布 + 文件清单（限定 `code_subdir`，默认最多 400 个）。

本地开销 0.2~1 秒，换来模型少跑几十次 `ls`/`grep`，直接跳到可疑文件。

### 5. 串行与缓存

* 用例判定完只**入队**，测试继续往下跑，不阻塞；
* 单 worker 串行：一条分析彻底结束后才开始下一条；
* 任务落盘在 `runs/_queue/`，进程被杀重启时 `running/` 里的任务会回到 `pending/` 继续；
* 超过 `run.analysis_max_attempts` 才判失败，失败原因写在 `failed/*.json`。

---

## 五、配置速查（`config.yaml`）

| 配置节 | 关键项 |
| --- | --- |
| `run` | `work_dir`、`case_timeout_seconds`、`capture_tolerance_lines`、`single_session_fallback`、`max_slice_lines`、`analysis_max_attempts` |
| `gate` | `url`、`reconnect_seconds`、`headers`、`send_ack` |
| `logs` | `url`、`message_format`、`text_fields`、`subscription`（连接后下发的订阅报文） |
| `patterns` | `session_create`、`node_failure`、`passenger_exit` 三条正则 |
| `judge` | `node_aliases`、`passenger_keys`、`contradiction_as_failure` |
| `dsh` | `cli_js`（DSH CLI 入口）、`profile`、`dsh_home`、`timeout_seconds`、`allow_code_fix` |
| `fix` | `repo_path`、`branch_template`、`time_format`、`push`、`remote` |
| `report` | `photo_fields`、`open_browser` |

两个连接都带**指数退避自动重连**，任一端断开都不会终止测试。

---

## 六、目录结构

```text
auto/
├── config.yaml                 # 主配置
├── config.selftest.yaml        # 自测配置（配 tools/mock_servers.py）
├── run_autotest.py             # 入口
├── requirements.txt
├── autotest/
│   ├── config.py               # 配置模型
│   ├── protocol.py             # 闸机 WebSocket 协议
│   ├── ws_clients.py           # 两个 WS 客户端（自动重连）
│   ├── logstore.py             # 日志缓存 / 会话识别 / 切片
│   ├── judge.py                # 预期 vs 实际
│   ├── report.py               # 照片 + JSON 体表格 + HTML/Markdown
│   ├── harness_bridge.py       # DeepSeek Harness 调用与提示词
│   ├── fixer.py                # 修复分支、提交、推送
│   ├── analysis_queue.py       # 落盘串行队列
│   └── runner.py               # 编排
├── tools/mock_servers.py       # 模拟闸机 + 模拟日志服务端
├── tools/check_channels.py     # 现场诊断：两个通道通不通、日志编码与正则命中
├── tests/test_core.py          # 核心逻辑单测
└── runs/                       # 运行产物（自动创建）
```

---

## 七、单测与自测

```bash
python -m unittest discover -s tests -v      # 全部单测（100+ 个用例）
python tools/mock_servers.py                 # 端到端自测（覆盖 4 种场景）
python run_autotest.py -c config.selftest.yaml --max-cases 4
```

### 现场排查：一条命令看出"为什么没收到用例"

```bash
python tools\check_channels.py -c config.yaml            # 默认等 15 秒
python tools\check_channels.py -c config.yaml --wait 60  # 用例来得慢就等久一点
```

它会逐项报告：

| 检查 | 说明 |
| --- | --- |
| 闸机通道 | 能不能连上、连上后有没有数据下来；没数据时按"最可能的原因"逐条给建议 |
| 日志通道 | 文件是否存在、**真实编码**（对不上会直接告诉你改哪个值）、三条正则各命中几次 |
| 日志行样 | 正则没命中时，把最近 500 行里"可能相关"的真实日志行打出来，方便你照着改正则 |
| 分析/面板 | dsh 能不能调、代码目录对不对（`code_path` 已指到子目录又填了同名 `code_subdir` 会明确提示） |
| HTTP 推用例 | 给出可以直接复制执行的 `curl.exe` 示例 |

自测场景：① 预期允许且日志允许 → 通过；② 预期允许但日志拒绝 → 不通过；
③ 预期节点与日志节点不一致 → 不通过；④ 日志只有会话创建无终态 → 超时。

---

## 八、数据来源、输入协议与 BI 面板

### 1. 日志来自文件（不推 WebSocket 时）

待测系统只往日志文件里写？把日志来源切成文件，程序会像 `tail -f` 一样持续跟随：

```yaml
logs:
  source: file
  file:
    path: D:/gate/logs/flow.log   # 必填
    encoding: auto                # auto = 逐行自动识别 utf-8 / gbk（推荐）；也可写死 utf-8、gbk
    poll_interval: 0.5
    from_beginning: false         # true = 连历史一起补读
    follow_rotation: true         # 文件被截断/轮转后自动重开
```

> **编码一定要对**：中文 Windows 的日志常是 GBK，用 UTF-8 硬解会把中文变成 `����`，
> 于是 `执行失败`、`离开通道` 这些正则**一条都匹配不到**（表现为"日志明明有内容却判不出结论"）。
> 填 `auto` 就不用管了；`python tools\check_channels.py -c config.yaml` 会告诉你真实编码。

细节：按行读取（**半行会等下次**，不会把写了一半的日志当完整行）；文件一开始不存在时会等它出现，
**出现后从头读**（避免漏掉"创建会话"那几行）；轮转/截断后自动重开。

### 2. 测试程序怎么把"预期结果"推给本程序

两种方向，消息格式一致：

```yaml
gate:
  mode: both            # client=我连它 / server=它连我 / both=两个方向都收
  url: ws://127.0.0.1:1880   # 我主动去连的地址
  listen_host: 0.0.0.0       # both、server 模式下我监听的地址
  listen_port: 8765
  listen_path: /gate
  send_ack: false            # 别往对方流程里回写数据
```

不确定对方是"广播给已连上的客户端"还是"主动连你"时，用 `mode: both` 最省事：两边同时开着，
哪个方向来消息都进流水线，**不用改对方的程序**。

**一轮十几条消息、只有一条带预期结果？** 不用管，程序已经处理：

```yaml
gate:
  round_keys: [serial, requestId, capture.cert_no, cert_no]   # 用它判断"是否同一轮"
  round_split: '_'        # serial_123 与 serial_456 → 同一轮（取 "_" 前那部分）
```

* 不带预期结果的设备/命令应答会被**安静忽略**，不计入"协议错误"；
* 日志切片从**这一轮第一条消息**的位置开始，不会漏掉"创建新会话"那几行；
* 心跳里能看到"进行中轮次 N 个 / 忽略非用例报文 M 条"。

**唯一序号用 serial（用例号/证号都会重复）**：同一条用例会被测多次（用例号相同）、
证号也可能重复，所以产物目录按 `cases/<用例号>__<serial>` 命名，`case.json` 里同时有
`case_id`（用例号）和 `serial`（唯一序号），报告与大屏都会显示 serial。
归轮用 serial 的**前缀**（`serial_1`/`serial_2` 同一轮），但**唯一标识用完整 serial**。

**这一轮的报文会随用例保存**，不通过时自动交给 DeepSeek Harness：

| 文件 | 内容 |
| --- | --- |
| `runs/<批次>/gate_raw.jsonl` | 全量原始报文（含照片 base64） |
| `cases/<case_id>/gate_messages.jsonl` | 这一轮的消息（`brief` 已去掉 base64） |
| `case.json` → `gate` | 轮次 serial、消息条数、**命令序列** |

报告新增「四、闸机通道消息」一节；分析提示词会要求模型先核对命令序列是否完整
（例如缺 `CB_PASSENGER_LEAVE`），再结合日志与源码判断根因。

### 报告界面也是同一套科技蓝大屏风格

单条用例的 `report.html` 和实时大屏共用一套设计令牌与图标（`autotest\ui.py`）：

* 顶部：结论徽标（绿=通过 / 红=不通过，带光晕）+ 批次、判定时间、采集时间；
* 第二行：**判定原因** 胶囊标签；
* KPI 卡片一栏：判定结果 / 预期 / 实际 / 失败节点 / 会话ID / 轮次·消息数 / 日志行数 / 问题归类；
* 五个分区面板：采集照片、用例数据（JSON 表格）、闸机通道消息（**命令序列**，带预期结果的那条高亮）、
  该旅客相关日志（可折叠）、DeepSeek Harness 分析（根因卡片 + 可能原因 + 解决办法 + 代码证据 +
  缺少的可观测信息 + 修复分支 + 模型总结）；
* 全部样式内联、无外链、无 JS，离线机打开正常；`@media print` 下自动转白底，可直接打印存档。

最小可用消息（一条 WebSocket 文本帧）：

```json
{
  "case_id": "C-000123",
  "expected": {"allow_pass": false, "failed_node": "证件核验"},
  "capture": {"passenger_name": "李四", "cert_no": "310101198805124321",
              "photo_base64": "data:image/jpeg;base64,..."}
}
```

* `expected.allow_pass` 必填（`true/false`、`"true"/"false"`、`1/0`、`"允许"/"不允许"` 都认）；
* `capture` 里的 `cert_no` / `passenger_name` **强烈建议给** —— 程序靠它们把用例和日志里的
  `旅客[xxx]` 对上号；
* 完整字段表、容错写法、回执、常见错误见 **[docs/输入协议.md](docs/输入协议.md)**；
* 现成可跑的示例：`python tools\demo_driver.py --url ws://127.0.0.1:8765/gate --log-file ./demo-logs/flow.log`。

**不想碰 WebSocket？可以直接 HTTP POST 推用例**（用面板那个端口，无需额外配置）：

```bash
curl.exe -X POST http://127.0.0.1:8767/api/case -H "Content-Type: application/json" ^
  -d "{\"case_id\":\"C-1\",\"expected\":{\"allow_pass\":true},\"capture\":{\"cert_no\":\"110101\"}}"
```

返回 `{"accepted": true}` 就是进流水线了；消息格式与 WebSocket 完全一致，也支持一次 POST 一个数组。

> 如果 WS 是"连上了但一条消息都没有"：那个端口很可能**本来是发数据给别人的中继**，
> WebSocket 不会把别人的数据转发给新连上的客户端。先用 `gate.mode: both`（两个方向都收），
> 或加 `gate.subscription`，或干脆用上面的 HTTP 方式。
>
> **如果对方是 Node-RED 的一问一答流程**（`websocket in` → … → `websocket out`）：
> `websocket in` 会给消息打上 `msg._session`，`websocket out` 看到它**只回给发起命令的那个客户端**，
> 所以我们连上去永远收不到。在发出消息的那个 function 里加一行 `delete msg._session;`
> 就变成广播给所有客户端（对方照旧收到）。详见 **[docs/nodered/说明.md](docs/nodered/说明.md)**。
>
> 对方发的格式和本程序不一致时：`python tools\check_channels.py -c config.yaml --save-raw gate_raw.jsonl`
> 把原始报文留档（运行中也会自动写 `runs\<批次>\gate_raw.jsonl`），照着真实字段适配解析即可。

### 3. BI 面板（大屏，直接开 URL 就能看）

跑起来后**浏览器直接访问 `http://<本机IP>:8767/`**，不用去找 html 文件 —— 程序内置了一个
轻量 HTTP 服务（标准库实现，离线可用），面板每 2 秒局部刷新，不闪屏：

```yaml
report:
  dashboard: true            # 生成 dashboard.html / dashboard.json（留档用）
  dashboard_interval: 2      # 刷新间隔（秒）
  serve: true                # 起内建 HTTP 服务
  host: 0.0.0.0              # 0.0.0.0 = 局域网可访问；只想本机看填 127.0.0.1
  port: 8767
  open_dashboard: false      # true = 启动时自动打开浏览器
```

启动日志里会直接打印地址：

```
INFO  BI 面板已启动：http://127.0.0.1:8767/（监听 0.0.0.0:8767）
BI 面板：http://127.0.0.1:8767/  ← 局域网用 http://<本机IP>:8767/ 访问
```

**界面**：科技蓝大屏风格（深空蓝渐变 + 网格底纹 + 霓虹描边），全部图标是内联 SVG，
不依赖任何 CDN/字体，断网也能显示。

| 区域 | 内容 |
| --- | --- |
| 顶部 | 标题、实时时钟、运行状态呼吸灯 |
| 阶段条 | **现在到哪一步**：等待用例 / 正在分析 DEMO-FAIL-003 / 已判定 N 条用例 / 运行结束；右侧显示闸机通道、日志来源、日志行数、会话数、更新时间 |
| 通过率环 | 环形进度 + 百分比 + `通过/总数` |
| 指标卡 | 用例总数 / 通过 / 不通过 / **分析中** / 待分析 / **用例问题** / **程序问题** / 环境问题 / 待定（各带图标与配色） |
| 明细表 | 每条用例：用例ID、结果（带图标状态胶囊）、预期、实际、判定原因、**归属**、根因摘要、判定时间、**报告链接**（点击直接看该用例报告） |

路由：`/`（面板）、`/api/state`（状态 JSON，也可给外部 BI 取）、`/cases/<用例>/report.html`
（单条报告）、`/cases/<用例>/photo.png`（照片）、`/health`（健康检查）。

> 面板在运行期间可用；进程退出后自动关闭。留档可以继续看 `dashboard.html` 与 `dashboard.json`。

一键体验（不需要闸机）：

```bat
run_offline.cmd -c config.demo.yaml            :: 终端 A：服务端模式 + 读日志文件 + 面板
python tools\demo_driver.py --url ws://127.0.0.1:8765/gate --log-file D:/gate/logs/flow.log
:: 浏览器打开 http://127.0.0.1:8767/
```

---

## 九、离线部署

完整步骤见 **[docs/离线部署与配置清单.md](docs/离线部署与配置清单.md)**。

### 机器上已经装好 DSH（推荐路径）

只要让程序找得到 `dsh` 命令，**其余全部沿用 DSH 自己的配置** —— 模型、API 地址、
API Key 都不需要在这里配任何东西：

```bat
dsh --help                       :: 有输出 = 它在 PATH 里，配置里什么都不用改
run_offline.cmd                  :: 直接跑
```

配置只需要填 4 项：`gate.url`、`logs.url`、`patterns.*`（按真实日志核对）、`fix.repo_path`。

启动横幅会显示 `Harness 分析：命令模式就绪：<dsh 路径>（home=...）`；
`模型/凭据` 一行只是提示，不阻断（凭据可能在系统密钥库或 DSH 自己的 provider 插件里）。

### 机器上什么都没有（裸机，用本仓库打离线包）

```bat
:: ① 联网机打包：Python 依赖 + 便携 node + DSH 运行树（约 1.6 GB）
python tools\prepare_offline_bundle.py

:: ② 整个目录拷到离线机

:: ③ 离线机：路径变了就必须重定位 junction（Windows junction 只能存绝对路径）
python tools\prepare_offline_bundle.py --relink

:: ④ 自检
python tools\verify_offline.py
```

此时配置里改用脚本模式：`dsh.cli_js: ./vendor/dsh/apps/cli/lib/bin.js`、`dsh.node_exe: ./vendor/node/node.exe`。

> 只想要「测试 + 报告」不要「自动分析」：打包加 `--no-dsh`，删掉 `vendor/dsh`、`vendor/node`，
> 只剩 8 MB；用 `--no-dsh` 运行，失败用例仍入队落盘，之后到联网机 `--analyze-only`。

生成的 `vendor/`：`python/`（websockets/PyYAML/rich 等，8.4 MB）、`node/node.exe`（89 MB）、
`dsh/`（69,738 文件 1.52 GB，6205 个 junction 已改指包内）、`manifest.json`。

程序启动时 `autotest/_vendor.py` 会把 `vendor/python` 插到 `sys.path` 最前，
所以**离线机不需要 pip 安装任何东西**（`python -S` 禁掉 site-packages 也能跑）。

### 打成压缩包带走

```bat
python tools\make_archive.py                 :: → dist\auto-<时间>.zip（约 2 MB，含 vendor/python）
python tools\make_archive.py --with-node     :: 额外带便携 node（+约 25 MB）
python tools\make_archive.py --with-runs     :: 额外带 runs 里的样例产物
python tools\make_archive.py --with-dsh      :: 带 vendor/dsh + links.json，目标机须跑 --restore-links
```

> 为什么不用"右键整个文件夹压缩"：`vendor/dsh` 里有 6205 个 junction（reparse point），
> 资源管理器会把它们当真目录递归跟进，体积爆掉甚至卡死；而**解压后链接全废**，
> 表现为 `--relink` 报 `共 0 个链接`、自检报 `Cannot find package ...`。
> 所以 zip 默认不含 `vendor/dsh` —— **目标机已装好 DSH 时本来就不需要它**。
> 确实要带，就用 `--with-dsh` 并在目标机跑 `--restore-links` 重建；
> 详见 [docs/故障修复-junction丢失.md](docs/故障修复-junction丢失.md)。

三种运行模式：

| 模式 | 命令 | 场景 |
| --- | --- | --- |
| 测试 + 分析 | `run_offline.cmd` | 离线机可达模型端点（内网网关/私有部署） |
| 只测试 + 报告 | `run_offline.cmd --no-dsh` | 完全断网；失败用例仍入队落盘 |
| 只消费队列 | `run_offline.cmd --analyze-only` | 把 `runs` 目录带回联网机集中分析 |

---

## 十、常见问题

**启动后一条用例都没收到？** 先跑通道诊断，它会真的去连、并告诉你卡在哪：

```bat
python tools\check_channels.py -c config.yaml --wait 15
```

输出会逐项显示：闸机通道是否连上、有没有收到消息、消息是否符合协议（会解析出 `case_id` /
`allow_pass`）；日志通道是否连上/文件是否存在、**三条正则各命中多少次**；dsh / 代码目录 / 面板端口状态。

常见原因（按概率排序）：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 闸机通道 `连接失败 … 拒绝网络连接` | 闸机/测试程序没在监听那个地址 | 确认地址端口；或改 `gate.mode: server` 让自己监听 |
| 连上了但 `15s 内没收到任何用例消息` | 对方连上了但不推 / 推给了别的连接 | 让对方按 `docs/输入协议.md` 推送；或核对是不是连了别的服务 |
| 程序日志出现 `闸机消息不符合协议，已忽略` | 缺 `expected.allow_pass`、JSON 不合法 | 按 `docs/输入协议.md` 的最小示例改 |
| 程序日志出现 `不支持的闸机消息类型` | `type` 是自定义名字且消息里没有用例字段 | 消息里带上 `expected`/`capture`（现在只要像用例就照收） |
| 用例收到了但一直超时 | 日志没进来，或正则不匹配 | 看诊断里三条正则的命中次数；核对 `logs.txt` 原文 |

程序每 30 秒会打一条**心跳**，能看出是"在等用例"还是"根本没连上"：

```
[心跳] 已收用例 0 条 / 已判定 0 条 / 日志 1234 行·3 会话 / 分析队列 待0·完成0·失败0（还没收到任何用例：…）
```

**日志格式对不上？** 改 `patterns` 三条正则即可。先在 `logs.txt` 里确认真实行样，
再调 `node_failure`（`会话ID[...]-节点...执行失败`）与 `passenger_exit`（`旅客[...]离开通道`）。

**节点名两边不一致？** 用 `judge.node_aliases` 做映射，别改模型提示词。

**日志里没有旅客标识？** `single_session_fallback: true` 会在「严格晚于本次采集且唯一」时
按该会话归属；想更严格就设成 `false`（宁可超时不误判）。

**分析没跑起来？** 看 `dsh_stderr.log`；确认 `dsh.cli_js` 指向已构建的
`apps/cli/lib/bin.js`，`node` 在 PATH 里，`dsh.dsh_home` 指向可用 profile 的 home。

**报 `CONTEXT_WINDOW_EXCEEDED`（输出 + 输入 > 上下文）？** 已自动处理：每次分析前会
`dsh --dump-config`（有缓存、带 60s 超时）读出现有 `maxTokens/contextWindow`，
把输出上限**自动收敛**到 `上下文 - input_reserve_tokens`（默认预留给输入 32768），
再通过 `--patch` 覆盖。日志侧也做了瘦身（见下），不再是几万字符一行的结构体转储。

**日志里有乱码行 / 超长结构体？** 由 `logs.max_field_chars`（默认 100）与
`logs.drop_undecodable`（默认 true）控制：

* 自动识别编码后**仍是乱码**的行直接丢弃，不进日志切片、不进模型输入；
* 单行里超过 100 字符的字段（base64 图片、长 hex）只保留前 100 字符，
  并标注 `…（长字段共 N 字符）` —— 一条 35 KB 的转储能被压到 300 字符左右，
  字段名和短字段仍然保留，模型照样能看懂结构；
* 心跳里能看到「日志清洗 丢N行·截断M行」。

**跑完去哪儿看改动？** 分析成功后会打印可直接点击的链接（放在输出最后一行）：
`改动详情`（commit diff）/`新建合并请求`（GitLab/GitHub）/`分支`。
链接由 `git remote get-url origin` 推导（SSH、https、带端口的内网地址都支持），
同时写进 `analysis.json` 的 `commit_url / branch_url / mr_url` 与报告第五节。

**停止程序时 DSH 会一起停吗？** 会。Ctrl+C / 到时退出 / 主动停止都会：

1. 优先**直接启动 node**（从 `dsh.cmd` 里解析出真实入口，不经 `cmd /c`），
   我们的子进程就是 node 本身，kill 一杀一个准；解析不出来才退回 `cmd /c`，
   并用 `taskkill /F /T` 连进程树一起杀（否则会留下孤儿 node 继续烧 token）；
2. 正在跑的那条分析**不算一次失败尝试**（`cancelled`），任务原样留在
   `runs/_queue/pending`，下次启动接着分析；
3. 退出时如果还有残留进程，`run_autotest.py` 的收尾会再清一次。

**想让某些已入队的分析不再跑？** 用队列控制命令（程序运行中也有效）：

```bat
python tools\queue_ctl.py list                    :: 看队列：待分析/进行中/完成/失败/已丢弃
python tools\queue_ctl.py drop --all              :: 丢弃全部待分析（不再分析）
python tools\queue_ctl.py drop --case A00000001   :: 只丢某条用例
python tools\queue_ctl.py drop --running          :: 终止正在跑的那条并丢弃
python tools\queue_ctl.py requeue-failed          :: 失败的全部放回待分析（重试）
python tools\queue_ctl.py clear --done --dropped  :: 清理历史记录
```

* 被丢弃的任务归档到 `runs/_queue/dropped/`（不删除，留痕），用例报告会显示
  「已人工放弃分析」，不会一直挂着"等待分析"；
* `drop --running` 是**写取消标记**：运行中的程序 2 秒内发现 → 终止 DSH → 归档；
  程序没在跑也没关系，下次启动照样直接丢弃；
* 也可以在启动时一次性丢：`run_autotest.py --drop-pending`（只分析本次新产生的用例）。

**报「分析结束但没有生成 analysis.json」？** 用例目录里有三份留档，按顺序看：

1. `dsh_stderr.log` 里如果模型在**反问**"请提供用例信息/失败详情"，说明它没读到完整任务。
   本程序因此把完整提示词写进 `analysis_prompt.md`，命令行只传**一行**
   "先读这个文件、照它执行" —— 因为 Windows 上 `dsh` 往往是 `.cmd`，必须经 `cmd /c` 启动，
   而 **cmd.exe 会在第一个换行处截断参数**（实测：第 2 行会被当成新命令执行）。
2. `dsh_stdout.log` 看模型到底输出了什么；`analysis_prompt.md` 看它本该看到什么。
3. 如果它读到了却没按 JSON 输出，换个更强的模型（`dsh.model`）或加大 `dsh.timeout_seconds`。

**会不会污染我平时用的 DSH 配置？** 默认 `dsh.dsh_home: null`，直接用你日常的 `DSH_HOME` / `~/.dsh`
（凭据现成可用）。想完全隔离就把 `dsh.dsh_home` 指到一个**你自己初始化过、且已配好 API Key** 的目录（相对路径会
按配置文件所在目录解析成绝对路径）。注意：不要把别人的 `~/.dsh/.credentials.yaml` 复制进本仓库——那等于把
API Key 提交到代码库；更干净的做法是导出 `DEEPSEEK_API_KEY` 环境变量，子进程会自动继承。

**分析报「凭据缺失」怎么办？** 启动横幅里有预检结论，会直接说明是 CLI 找不到还是 API Key 缺失
（`dsh_credential_state()` 检查 `DEEPSEEK_API_KEY` 与 home 里的 `.credentials.yaml`），此时分析的用例会标记为
「跳过」而不是跑成失败。
