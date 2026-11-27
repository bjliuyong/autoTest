# Gate AutoTest Studio（桌面端应用）

基于 GitHub 开源现代桌面框架 **[guasam/electron-react-app](https://github.com/guasam/electron-react-app)**（Electron + Vite + React 19 + TypeScript + Tailwind CSS v4 + shadcn/ui + electron-conveyor）重构而成的**跨平台闸机自动化测试与判定桌面应用**。

支持 **Windows / macOS / Linux** 三大桌面操作系统，内置 **SQLite 本地关系型数据库**，实现测试批次、用例详情、日志切片、失败分析与系统配置的全链路持久化与随时追溯。

## 核心特性

1. **现代化桌面交互体验**
   - 原生无边框窗口与自定义标题栏（自动适配 macOS 交通灯按钮与 Windows/Linux 窗口控制按钮），支持亮/暗主题平滑切换。
   - 四个核心模块：**监控大盘**、**历史批次**、**用例追溯**、**系统配置**。
2. **完整复用并重构原测试引擎逻辑**
   - **闸机通道**：支持 `client`（主动连接）、`server`（本地监听）、`both`（双模式同时接收），以及内置 `POST /api/case` HTTP 推用例接口。
   - **日志通道**：支持 WebSocket 实时推流与本地日志文件跟随（`tail -f`，自动识别 UTF-8 / GBK 编码与日志轮转）。
   - **轮次归并 & 会话判定**：完整移植 `protocol`、`rounds`、`logstore`、`judge` 核心算法（节点名 NFKC 归一化、别名映射、并发会话窗口归属、超时与自相矛盾处理）。
   - **内置一键自测模拟**：无需外接闸机硬件，一键运行 5 条典型场景用例（正常放行、预期拦截一致、误拒、节点不一致、日志超时），全流程自动判定与落库。
3. **SQLite 持久化与全链路追溯**
   - 使用跨平台 `sql.js`（WebAssembly SQLite，免原生编译、免装外部数据库），数据文件位于系统用户数据目录 `gate-autotest.sqlite`。
   - 四张核心数据表：`test_batches`（测试批次）、`test_cases`（用例详情与日志切片）、`system_logs`（系统运行日志）、`app_configs`（持久化配置）。
   - 支持按批次/结果/关键字检索追溯，支持一键导出独立 **HTML 报告** 或 **JSON 归档**。

## 开发与打包命令

```bash
cd desktop-app
npm install

# 本地开发启动
npm run dev

# 类型检查
npm run typecheck

# 跨平台打包
npm run build:win     # Windows (NSIS 安装包 + Portable 便携版)
npm run build:mac     # macOS (DMG)
npm run build:linux   # Linux (AppImage / deb / snap)
```
