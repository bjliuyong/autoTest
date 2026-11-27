<template>
  <div class="app-container autotest-dashboard">
    <!-- 顶部状态栏与控制操作 -->
    <el-card shadow="never" class="control-header">
      <el-row :gutter="20" type="flex" align="middle">
        <el-col :span="12">
          <div class="status-container">
            <span class="status-indicator" :class="stats.currentStatus.toLowerCase()"></span>
            <span class="status-title">引擎状态：</span>
            <el-tag :type="stats.currentStatus === 'RUNNING' ? 'success' : 'info'" effect="dark" size="medium">
              {{ stats.currentStatus === 'RUNNING' ? '正在运行中' : '空闲待命中' }}
            </el-tag>
            <span class="batch-text" v-if="stats.currentBatchNo">当前批次: <b>{{ stats.currentBatchNo }}</b></span>
          </div>
        </el-col>
        <el-col :span="12" style="text-align: right;">
          <el-button
            type="primary"
            icon="el-icon-video-play"
            :disabled="stats.currentStatus === 'RUNNING'"
            @click="handleStart"
          >启动测试</el-button>
          <el-button
            type="danger"
            icon="el-icon-video-pause"
            :disabled="stats.currentStatus !== 'RUNNING'"
            @click="handleStop"
          >停止测试</el-button>
          <el-dropdown @command="handleSimulate" style="margin-left: 10px;">
            <el-button type="warning" icon="el-icon-cpu">
              一键自测模拟 <i class="el-icon-arrow-down el-icon--right"></i>
            </el-button>
            <el-dropdown-menu slot="dropdown">
              <el-dropdown-item command="5">模拟 5 条用例</el-dropdown-item>
              <el-dropdown-item command="10">模拟 10 条用例</el-dropdown-item>
              <el-dropdown-item command="20">模拟 20 条用例</el-dropdown-item>
            </el-dropdown-menu>
          </el-dropdown>
          <el-button icon="el-icon-refresh" @click="fetchStats" style="margin-left: 10px;">刷新</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 关键指标卡片 -->
    <el-row :gutter="16" class="metric-row">
      <el-col :span="4">
        <el-card shadow="hover" class="metric-card total">
          <div class="metric-label">用例总数</div>
          <div class="metric-value">{{ stats.totalCases || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="metric-card passed">
          <div class="metric-label">判定通过</div>
          <div class="metric-value">{{ stats.passCases || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="metric-card failed">
          <div class="metric-label">判定不通过</div>
          <div class="metric-value">{{ stats.failCases || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="metric-card timeout">
          <div class="metric-label">日志超时</div>
          <div class="metric-value">{{ stats.timeoutCases || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="metric-card rate">
          <div class="metric-label">测试通过率</div>
          <div class="metric-value">{{ stats.passRate || 0 }}%</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 可视化图表区 -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="10">
        <el-card shadow="never" class="chart-card">
          <div slot="header">
            <span><i class="el-icon-pie-chart"></i> 测试结论比例分布</span>
          </div>
          <div ref="pieChart" style="height: 280px;"></div>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card shadow="never" class="chart-card">
          <div slot="header">
            <span><i class="el-icon-bar-chart"></i> 失败节点拦截统计</span>
          </div>
          <div ref="barChart" style="height: 280px;"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 实时测试用例流与系统日志双控制台 -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="14">
        <el-card shadow="never" class="feed-card">
          <div slot="header" class="feed-header">
            <span><i class="el-icon-tickets"></i> 实时用例流 (最新判定)</span>
            <el-button type="text" @click="liveCases = []">清空列表</el-button>
          </div>
          <el-table :data="liveCases" size="small" stripe style="width: 100%" height="320">
            <el-table-column prop="caseId" label="用例号" width="110" />
            <el-table-column prop="passengerName" label="旅客姓名" width="90" />
            <el-table-column label="预期结果" width="130">
              <template slot-scope="scope">
                <el-tag size="mini" :type="scope.row.expectedAllowPass === 1 ? 'success' : 'danger'">
                  {{ scope.row.expectedAllowPass === 1 ? '允许通行' : '拦截 (' + (scope.row.expectedFailedNode || '失败') + ')' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="判定结论" width="100">
              <template slot-scope="scope">
                <el-tag
                  size="mini"
                  :type="scope.row.verdict === 'PASS' ? 'success' : scope.row.verdict === 'TIMEOUT' ? 'warning' : 'danger'"
                >
                  {{ scope.row.verdict === 'PASS' ? '通过' : scope.row.verdict === 'TIMEOUT' ? '超时' : '不通过' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reasons" label="判定说明" :show-overflow-tooltip="true" />
            <el-table-column label="操作" width="80" align="center">
              <template slot-scope="scope">
                <el-button size="mini" type="text" icon="el-icon-view" @click="handleViewCase(scope.row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never" class="console-card">
          <div slot="header" class="feed-header">
            <span><i class="el-icon-cpu"></i> 待测系统实时日志流 (Live Logs)</span>
            <el-button type="text" @click="liveLogs = []">清空日志</el-button>
          </div>
          <div class="terminal-box" ref="logBox">
            <div v-for="(log, idx) in liveLogs" :key="idx" class="log-line">
              <span class="log-text">{{ log }}</span>
            </div>
            <div v-if="liveLogs.length === 0" class="log-empty">等待日志接收中...</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 用例详情对话框 -->
    <el-dialog title="用例判定与追溯详情" :visible.sync="openCaseDialog" width="750px" append-to-body>
      <div v-if="selectedCase">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="用例编号">{{ selectedCase.caseId }}</el-descriptions-item>
          <el-descriptions-item label="归属批次">{{ selectedCase.batchNo }}</el-descriptions-item>
          <el-descriptions-item label="旅客姓名">{{ selectedCase.passengerName }}</el-descriptions-item>
          <el-descriptions-item label="证件号码">{{ selectedCase.certNo }}</el-descriptions-item>
          <el-descriptions-item label="预期放行">
            <el-tag size="mini" :type="selectedCase.expectedAllowPass === 1 ? 'success' : 'danger'">
              {{ selectedCase.expectedAllowPass === 1 ? '允许通行' : '不允许通行' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="预期失败节点">{{ selectedCase.expectedFailedNode || '-' }}</el-descriptions-item>
          <el-descriptions-item label="实际放行">
            <el-tag size="mini" :type="selectedCase.actualAllowPass === 1 ? 'success' : 'danger'">
              {{ selectedCase.actualAllowPass === 1 ? '允许通行' : selectedCase.actualAllowPass === 0 ? '不允许通行' : '未判定' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="实际失败节点">{{ selectedCase.actualFailedNode || '-' }}</el-descriptions-item>
          <el-descriptions-item label="最终判定">
            <el-tag
              size="medium"
              :type="selectedCase.verdict === 'PASS' ? 'success' : selectedCase.verdict === 'TIMEOUT' ? 'warning' : 'danger'"
            >
              {{ selectedCase.verdict === 'PASS' ? '判定通过' : selectedCase.verdict === 'TIMEOUT' ? '判定超时' : '判定失败' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="判定耗时">{{ selectedCase.durationMs }} ms</el-descriptions-item>
          <el-descriptions-item label="判定说明" :span="2">{{ selectedCase.reasons }}</el-descriptions-item>
        </el-descriptions>

        <div style="margin-top: 15px;">
          <h4><i class="el-icon-document"></i> 关联日志切片 (Log Slice)</h4>
          <pre class="slice-box">{{ selectedCase.logSlice || '无日志记录' }}</pre>
        </div>
      </div>
      <div slot="footer" class="dialog-footer">
        <el-button @click="openCaseDialog = false">关 闭</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import * as echarts from 'echarts'
import { getDashboardStats, startTest, stopTest, runSimulation } from '@/api/autotest/dashboard'

export default {
  name: 'AutoTestDashboard',
  data() {
    return {
      stats: {
        currentStatus: 'IDLE',
        currentBatchNo: '',
        totalCases: 0,
        passCases: 0,
        failCases: 0,
        timeoutCases: 0,
        passRate: 0,
        failureNodeCounts: {}
      },
      liveCases: [],
      liveLogs: [],
      openCaseDialog: false,
      selectedCase: null,
      pieChartInstance: null,
      barChartInstance: null,
      ws: null,
      pollTimer: null
    }
  },
  mounted() {
    this.initCharts()
    this.fetchStats()
    this.initWebSocket()
    // 兜底定时拉取状态
    this.pollTimer = setInterval(this.fetchStats, 5000)
    window.addEventListener('resize', this.handleResize)
  },
  beforeDestroy() {
    if (this.ws) {
      this.ws.close()
    }
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
    }
    window.removeEventListener('resize', this.handleResize)
  },
  methods: {
    initCharts() {
      this.pieChartInstance = echarts.init(this.$refs.pieChart)
      this.barChartInstance = echarts.init(this.$refs.barChart)
      this.updateCharts()
    },
    updateCharts() {
      if (!this.pieChartInstance || !this.barChartInstance) return

      const pass = this.stats.passCases || 0
      const fail = this.stats.failCases || 0

      // 饼图
      this.pieChartInstance.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: '5%', left: 'center' },
        color: ['#67C23A', '#F56C6C'],
        series: [
          {
            name: '测试结果',
            type: 'pie',
            radius: ['45%', '70%'],
            avoidLabelOverlap: false,
            label: { show: false, position: 'center' },
            emphasis: { label: { show: true, fontSize: '16', fontWeight: 'bold' } },
            data: [
              { value: pass, name: '通过用例' },
              { value: fail, name: '未通过用例' }
            ]
          }
        ]
      })

      // 柱状图
      const nodeData = this.stats.failureNodeCounts || {}
      const nodeNames = Object.keys(nodeData)
      const nodeCounts = Object.values(nodeData)

      this.barChartInstance.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: nodeNames.length ? nodeNames : ['暂无失败节点'] },
        yAxis: { type: 'value', minInterval: 1 },
        series: [
          {
            data: nodeCounts.length ? nodeCounts : [0],
            type: 'bar',
            itemStyle: { color: '#E6A23C' },
            barWidth: '40%'
          }
        ]
      })
    },
    fetchStats() {
      getDashboardStats().then(response => {
        if (response.data) {
          this.stats = response.data
          this.updateCharts()
        }
      })
    },
    initWebSocket() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws/autotest`

      try {
        this.ws = new WebSocket(wsUrl)
        this.ws.onmessage = event => {
          try {
            const data = JSON.parse(event.data)
            if (data.type === 'case_result' && data.data) {
              this.liveCases.unshift(data.data)
              if (this.liveCases.length > 50) {
                this.liveCases.pop()
              }
            } else if (data.type === 'log_line' && data.data) {
              this.liveLogs.push(data.data)
              if (this.liveLogs.length > 200) {
                this.liveLogs.shift()
              }
              this.$nextTick(() => {
                if (this.$refs.logBox) {
                  this.$refs.logBox.scrollTop = this.$refs.logBox.scrollHeight
                }
              })
            } else if (data.type === 'batch_update' && data.data) {
              this.stats.totalCases = data.data.totalCases
              this.stats.passCases = data.data.passCases
              this.stats.failCases = data.data.failCases
              this.stats.timeoutCases = data.data.timeoutCases
              this.stats.passRate = data.data.passRate
              this.updateCharts()
            } else if (data.type === 'status_change' && data.data) {
              this.stats.currentStatus = data.data.status
              this.fetchStats()
            }
          } catch (e) {
          }
        }
      } catch (e) {
      }
    },
    handleStart() {
      startTest({}).then(response => {
        this.$message.success(response.msg || '测试已成功启动')
        this.fetchStats()
      })
    },
    handleStop() {
      stopTest().then(response => {
        this.$message.info(response.msg || '测试已停止')
        this.fetchStats()
      })
    },
    handleSimulate(count) {
      runSimulation(count).then(response => {
        this.$message.success(response.msg || '自测模拟已启动')
        this.fetchStats()
      })
    },
    handleViewCase(row) {
      this.selectedCase = row
      this.openCaseDialog = true
    },
    handleResize() {
      if (this.pieChartInstance) this.pieChartInstance.resize()
      if (this.barChartInstance) this.barChartInstance.resize()
    }
  }
}
</script>

<style scoped>
.autotest-dashboard {
  padding: 16px;
}
.control-header {
  background: #fdfdfd;
  margin-bottom: 16px;
}
.status-container {
  display: flex;
  align-items: center;
}
.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  margin-right: 8px;
  background-color: #909399;
}
.status-indicator.running {
  background-color: #67C23A;
  box-shadow: 0 0 8px #67C23A;
}
.status-title {
  font-weight: bold;
  margin-right: 8px;
}
.batch-text {
  margin-left: 16px;
  color: #606266;
}
.metric-row {
  margin-top: 10px;
}
.metric-card {
  text-align: center;
  border-radius: 6px;
}
.metric-label {
  font-size: 14px;
  color: #909399;
  margin-bottom: 8px;
}
.metric-value {
  font-size: 26px;
  font-weight: bold;
  color: #303133;
}
.metric-card.passed .metric-value { color: #67C23A; }
.metric-card.failed .metric-value { color: #F56C6C; }
.metric-card.timeout .metric-value { color: #E6A23C; }
.metric-card.rate .metric-value { color: #409EFF; }

.feed-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: bold;
}
.terminal-box {
  background-color: #1e1e1e;
  color: #d4d4d4;
  font-family: Consolas, Monaco, "Courier New", monospace;
  font-size: 12px;
  padding: 10px;
  height: 320px;
  overflow-y: auto;
  border-radius: 4px;
}
.log-line {
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-empty {
  color: #6a9955;
  text-align: center;
  padding-top: 80px;
}
.slice-box {
  background-color: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  font-size: 12px;
  max-height: 200px;
  overflow-y: auto;
}
</style>
