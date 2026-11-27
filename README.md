# 闸机自动化测试与判定平台 (Web版 / RuoYi-Vue)

本项目已从原单机 Python 执行脚本改造为基于 **RuoYi-Vue 前后端分离架构** 的成熟企业级 Web 自动化测试管理平台。具备完整的**数据持久化**、**全链路用例追溯**、**实时监控大盘**以及**Docker 容器化部署**能力。

---

## 一、项目架构与目录说明

```text
auto/
├── autotest-scripts/            # 【原 Python 脚本归档目录】
│   ├── autotest/               # 核心测试调度与判定 Python 源码
│   ├── config.*.yaml           # 各环境 YAML 配置样例
│   ├── docs/                   # 原始协议与技术文档
│   ├── run_autotest.py         # 原入口脚本
│   ├── tests/                  # 单元测试
│   └── tools/                  # 模拟服务端、排查工具 (mock_servers.py 等)
│
├── ruoyi-vue/                   # 【新若依前后端分离版系统工程】
│   ├── ruoyi-admin/            # Web 服务入口与 REST 控制器
│   │   └── .../controller/autotest/ # 自动化测试控制层 (Dashboard, Batch, Case, Config)
│   ├── ruoyi-system/           # 业务模型、数据库映射与核心引擎
│   │   ├── .../autotest/domain/     # 批次、用例、判定、配置等实体模型
│   │   ├── .../autotest/engine/     # 判定引擎、日志流缓存仓、双WebSocket调度引擎
│   │   ├── .../autotest/mapper/     # MyBatis 数据库持久化 Mapper
│   │   └── .../autotest/service/    # 业务接口与实现
│   ├── ruoyi-framework/        # 核心框架配置 (WebSocket 握手、Spring Security 放行)
│   ├── ruoyi-common/           # 通用工具类与 WebSocket 依赖
│   ├── ruoyi-ui/               # 前端工程 (Vue 2 + Element-UI + ECharts)
│   │   ├── src/api/autotest/        # 前端 API 请求模块
│   │   └── src/views/autotest/      # 前端页面
│   │       ├── dashboard/index.vue  # 测试监控大盘 (指标卡片 + ECharts 图表 + 实时流)
│   │       ├── batch/index.vue      # 历史批次追踪与管理
│   │       ├── case/index.vue       # 用例详情追溯 (照片、预期对比、日志切片)
│   │       └── config/index.vue     # 测试参数、通道与正则配置
│   ├── sql/
│   │   ├── ry_20240629.sql          # 若依基础系统表
│   │   ├── quartz.sql               # 定时任务表
│   │   └── autotest.sql             # 自动化测试持久化扩展表与菜单 SQL
│   ├── Dockerfile              # 后端 Java 多阶段构建与轻量级运行镜像
│   ├── Dockerfile.jar          # 现有 Jar 包轻量镜像
│   ├── docker-compose.yml      # 全套编排文件 (MySQL + Redis + RuoYi 后端)
│   └── build-and-run-docker.*  # Docker 一键构建运行脚本
│
└── .gitignore                  # 已排除 dist/、runs/、target/、node_modules/ 等所有构建与运行产物
```

---

## 二、核心改造特性

### 1. 数据持久化与全链路追溯 (Requirement 3)
* **批次管理 (`auto_test_batch`)**：记录每次测试的启动/结束时间、运行耗时、总用例数、通过数、失败数、超时数以及实时计算的通过率。
* **用例追溯 (`auto_test_case`)**：
  * 旅客全量信息（姓名、证件类型、证件号码、闸机编号、采集照片 Base64/URL）；
  * 预期结论与实际结论的逐项精准比对（放行状态、失败/拦截节点名称）；
  * 最终判定结果（`PASS` / `FAIL` / `TIMEOUT` / `ERROR`）与详细原因说明；
  * 该旅客在待测流程引擎中的**完整会话日志切片 (Log Slice)**，支持随时回溯排查。
* **参数配置持久化 (`auto_test_config`)**：支持网页端动态修改闸机 WS 地址、日志 WS 地址、超时时长、正则匹配模板及节点别名映射。

### 2. 前端实时监控大盘与一键自测 (Requirement 2)
* **实时监控**：基于 SpringBoot WebSocket (`/ws/autotest`) 实现毫秒级实时推送，在网页端直观看到最新的用例判定流与滚动的系统日志。
* **数据可视化**：通过 ECharts 展示通过率比例饼图和失败拦截节点分布柱状图。
* **内置自测模拟器**：支持在界面点击「一键自测模拟」，无需外接真实硬件即可一键跑通 5~20 条典型用例全流程判定与落库。

### 3. 后端 Java 包 Docker 容器化运行 (Requirement 4)
* 提供多阶段构建 Dockerfile（容器内部自动通过 Maven 编译，宿主机免装编译环境）；
* 提供标准 `docker-compose.yml`，一键拉起 `MySQL 5.7` + `Redis 6` + `RuoYi 后端`，MySQL 会自动执行 `autotest.sql` 进行数据库建表与菜单注入。

---

## 三、快速开始

### 方式 A：Docker 容器化一键部署（推荐）

1. 进入 `ruoyi-vue` 目录：
   ```bash
   cd ruoyi-vue
   ```
2. 执行构建启动：
   * Windows: 双击运行 `build-and-run-docker.bat` 或在终端运行：
     ```cmd
     docker-compose up --build -d
     ```
   * Linux / macOS:
     ```bash
     chmod +x build-and-run-docker.sh && ./build-and-run-docker.sh
     ```
3. 查看服务状态：
   ```bash
   docker-compose ps
   ```
4. 后端 API 端口为 `8080`，闸机监听端口为 `8765`。

### 方式 B：本地开发调试运行

#### 1. 数据库准备
* 创建 MySQL 数据库 `ry-vue` (utf8mb4)；
* 依次执行 `ruoyi-vue/sql/` 下的三个 SQL 脚本：
  1. `ry_20240629.sql`
  2. `quartz.sql`
  3. `autotest.sql`

#### 2. 后端启动
* 本机启动 Redis 服务（默认端口 6379）；
* 在 `ruoyi-vue/ruoyi-admin/src/main/resources/application-druid.yml` 中确认数据库密码；
* 运行 `com.ruoyi.RuoYiApplication` 主类启动后端服务。

#### 3. 前端启动
```bash
cd ruoyi-vue/ruoyi-ui
npm install
npm run dev
```
* 浏览器访问：`http://localhost:80` (或直接访问 `http://localhost`)
* 默认管理员账号：`admin`，密码：`admin123`
* 登录后在左侧菜单即可看到 **「自动化测试」** 菜单（含测试大盘、测试批次、用例追溯、测试配置）。
