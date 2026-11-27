<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryForm" size="small" :inline="true" v-show="showSearch" label-width="68px">
      <el-form-item label="批次ID" prop="batchId">
        <el-input
          v-model="queryParams.batchId"
          placeholder="批次ID"
          clearable
          style="width: 130px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="用例编号" prop="caseId">
        <el-input
          v-model="queryParams.caseId"
          placeholder="请输入用例编号"
          clearable
          style="width: 160px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="旅客姓名" prop="passengerName">
        <el-input
          v-model="queryParams.passengerName"
          placeholder="请输入旅客姓名"
          clearable
          style="width: 140px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="证件号码" prop="certNo">
        <el-input
          v-model="queryParams.certNo"
          placeholder="请输入证件号码"
          clearable
          style="width: 180px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="判定结论" prop="verdict">
        <el-select v-model="queryParams.verdict" placeholder="判定结论" clearable style="width: 130px">
          <el-option label="通过 (PASS)" value="PASS" />
          <el-option label="失败 (FAIL)" value="FAIL" />
          <el-option label="超时 (TIMEOUT)" value="TIMEOUT" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="el-icon-search" size="mini" @click="handleQuery">搜索</el-button>
        <el-button icon="el-icon-refresh" size="mini" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="danger"
          plain
          icon="el-icon-delete"
          size="mini"
          :disabled="multiple"
          @click="handleDelete"
          v-hasPermi="['autotest:case:list']"
        >删除</el-button>
      </el-col>
      <right-toolbar :showSearch.sync="showSearch" @queryTable="getList"></right-toolbar>
    </el-row>

    <el-table v-loading="loading" :data="caseList" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" align="center" />
      <el-table-column label="记录ID" align="center" prop="caseRecordId" width="75" />
      <el-table-column label="用例编号" align="center" prop="caseId" width="130" :show-overflow-tooltip="true" />
      <el-table-column label="批次号" align="center" prop="batchNo" width="160" :show-overflow-tooltip="true" />
      <el-table-column label="旅客姓名" align="center" prop="passengerName" width="100" />
      <el-table-column label="证件号码" align="center" prop="certNo" width="170" :show-overflow-tooltip="true" />
      <el-table-column label="预期结果" align="center" width="140">
        <template slot-scope="scope">
          <el-tag size="mini" :type="scope.row.expectedAllowPass === 1 ? 'success' : 'danger'">
            {{ scope.row.expectedAllowPass === 1 ? '放行' : '拦截 (' + (scope.row.expectedFailedNode || '失败') + ')' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="实际结果" align="center" width="140">
        <template slot-scope="scope">
          <el-tag size="mini" :type="scope.row.actualAllowPass === 1 ? 'success' : scope.row.actualAllowPass === 0 ? 'danger' : 'info'">
            {{ scope.row.actualAllowPass === 1 ? '放行' : scope.row.actualAllowPass === 0 ? '拦截 (' + (scope.row.actualFailedNode || '-') + ')' : '未判定' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="判定结论" align="center" width="110">
        <template slot-scope="scope">
          <el-tag
            size="small"
            effect="dark"
            :type="scope.row.verdict === 'PASS' ? 'success' : scope.row.verdict === 'TIMEOUT' ? 'warning' : 'danger'"
          >
            {{ scope.row.verdict === 'PASS' ? '通过' : scope.row.verdict === 'TIMEOUT' ? '超时' : '不通过' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="耗时(ms)" align="center" prop="durationMs" width="90" />
      <el-table-column label="接收时间" align="center" prop="receivedTime" width="160" />
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width" width="120">
        <template slot-scope="scope">
          <el-button
            size="mini"
            type="text"
            icon="el-icon-document"
            @click="handleDetail(scope.row)"
          >详情追溯</el-button>
          <el-button
            size="mini"
            type="text"
            icon="el-icon-delete"
            @click="handleDelete(scope.row)"
            v-hasPermi="['autotest:case:list']"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <pagination
      v-show="total>0"
      :total="total"
      :page.sync="queryParams.pageNum"
      :limit.sync="queryParams.pageSize"
      @pagination="getList"
    />

    <!-- 用例追溯详细对话框 -->
    <el-dialog title="自动化测试用例完整追溯信息" :visible.sync="openDetail" width="850px" append-to-body>
      <div v-if="currentDetail">
        <el-row :gutter="20">
          <el-col :span="16">
            <el-descriptions title="用例与旅客信息" :column="2" border size="small">
              <el-descriptions-item label="用例编号">{{ currentDetail.caseId }}</el-descriptions-item>
              <el-descriptions-item label="测试批次">{{ currentDetail.batchNo }}</el-descriptions-item>
              <el-descriptions-item label="旅客姓名">{{ currentDetail.passengerName }}</el-descriptions-item>
              <el-descriptions-item label="证件类型">{{ currentDetail.certType }}</el-descriptions-item>
              <el-descriptions-item label="证件号码">{{ currentDetail.certNo }}</el-descriptions-item>
              <el-descriptions-item label="闸机编号">{{ currentDetail.gateId || 'G01' }}</el-descriptions-item>
              <el-descriptions-item label="会话ID (SID)">{{ currentDetail.sessionId || '-' }}</el-descriptions-item>
              <el-descriptions-item label="判定耗时">{{ currentDetail.durationMs }} 毫秒</el-descriptions-item>
            </el-descriptions>
          </el-col>
          <el-col :span="8" style="text-align: center;">
            <div style="font-size: 13px; font-weight: bold; margin-bottom: 8px;">旅客现场照片</div>
            <div class="photo-container">
              <el-image
                v-if="currentDetail.photoUrl"
                :src="currentDetail.photoUrl"
                :preview-src-list="[currentDetail.photoUrl]"
                fit="contain"
                style="width: 140px; height: 160px; border-radius: 4px; border: 1px solid #dcdfe6;"
              />
              <div v-else class="photo-placeholder">暂无照片</div>
            </div>
          </el-col>
        </el-row>

        <div style="margin-top: 18px;">
          <el-descriptions title="判定比对详情" :column="2" border size="small">
            <el-descriptions-item label="预期放行">
              <el-tag size="mini" :type="currentDetail.expectedAllowPass === 1 ? 'success' : 'danger'">
                {{ currentDetail.expectedAllowPass === 1 ? '允许通行' : '不允许通行' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="预期拦截节点">{{ currentDetail.expectedFailedNode || '无 (正常通行)' }}</el-descriptions-item>
            <el-descriptions-item label="实际放行">
              <el-tag size="mini" :type="currentDetail.actualAllowPass === 1 ? 'success' : currentDetail.actualAllowPass === 0 ? 'danger' : 'info'">
                {{ currentDetail.actualAllowPass === 1 ? '允许通行' : currentDetail.actualAllowPass === 0 ? '不允许通行' : '未判定' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="实际拦截节点">{{ currentDetail.actualFailedNode || '无' }}</el-descriptions-item>
            <el-descriptions-item label="综合判定结论" :span="2">
              <el-tag
                size="medium"
                effect="dark"
                :type="currentDetail.verdict === 'PASS' ? 'success' : currentDetail.verdict === 'TIMEOUT' ? 'warning' : 'danger'"
              >
                {{ currentDetail.verdict === 'PASS' ? '判定通过 (一致)' : currentDetail.verdict === 'TIMEOUT' ? '日志超时' : '判定失败 (不一致)' }}
              </el-tag>
              <span style="margin-left: 10px; color: #606266;">{{ currentDetail.reasons }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div style="margin-top: 18px;">
          <div style="font-weight: bold; margin-bottom: 8px;">旅客会话日志切片 (Log Slice)</div>
          <pre class="log-slice-view">{{ currentDetail.logSlice || '无日志切片数据' }}</pre>
        </div>
      </div>
      <div slot="footer" class="dialog-footer">
        <el-button @click="openDetail = false">关 闭</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import { listCase, getCase, delCase } from "@/api/autotest/case";

export default {
  name: "AutoTestCase",
  data() {
    return {
      loading: true,
      ids: [],
      multiple: true,
      showSearch: true,
      total: 0,
      caseList: [],
      openDetail: false,
      currentDetail: null,
      queryParams: {
        pageNum: 1,
        pageSize: 10,
        batchId: undefined,
        caseId: undefined,
        passengerName: undefined,
        certNo: undefined,
        verdict: undefined
      }
    };
  },
  created() {
    if (this.$route.query && this.$route.query.batchId) {
      this.queryParams.batchId = this.$route.query.batchId;
    }
    this.getList();
  },
  methods: {
    getList() {
      this.loading = true;
      listCase(this.queryParams).then(response => {
        this.caseList = response.rows;
        this.total = response.total;
        this.loading = false;
      });
    },
    handleQuery() {
      this.queryParams.pageNum = 1;
      this.getList();
    },
    resetQuery() {
      this.resetForm("queryForm");
      this.handleQuery();
    },
    handleSelectionChange(selection) {
      this.ids = selection.map(item => item.caseRecordId);
      this.multiple = !selection.length;
    },
    handleDetail(row) {
      getCase(row.caseRecordId).then(response => {
        this.currentDetail = response.data;
        this.openDetail = true;
      });
    },
    handleDelete(row) {
      const caseRecordIds = row.caseRecordId || this.ids;
      this.$modal.confirm('是否确认删除测试用例编号为"' + caseRecordIds + '"的数据项？').then(() => {
        return delCase(caseRecordIds);
      }).then(() => {
        this.getList();
        this.$modal.msgSuccess("删除成功");
      }).catch(() => {});
    }
  }
};
</script>

<style scoped>
.photo-container {
  display: flex;
  justify-content: center;
  align-items: center;
}
.photo-placeholder {
  width: 140px;
  height: 160px;
  background-color: #f5f7fa;
  color: #909399;
  display: flex;
  justify-content: center;
  align-items: center;
  border-radius: 4px;
  border: 1px dashed #dcdfe6;
}
.log-slice-view {
  background-color: #1e1e1e;
  color: #00ff66;
  font-family: Consolas, Monaco, "Courier New", monospace;
  font-size: 12px;
  padding: 12px;
  border-radius: 4px;
  max-height: 250px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
