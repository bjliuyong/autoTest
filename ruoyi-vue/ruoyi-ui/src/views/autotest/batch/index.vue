<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryForm" size="small" :inline="true" v-show="showSearch" label-width="68px">
      <el-form-item label="批次编号" prop="batchNo">
        <el-input
          v-model="queryParams.batchNo"
          placeholder="请输入批次编号"
          clearable
          style="width: 240px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="批次名称" prop="batchName">
        <el-input
          v-model="queryParams.batchName"
          placeholder="请输入批次名称"
          clearable
          style="width: 240px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item label="状态" prop="status">
        <el-select v-model="queryParams.status" placeholder="批次状态" clearable style="width: 150px">
          <el-option label="运行中" value="RUNNING" />
          <el-option label="已完成" value="COMPLETED" />
          <el-option label="已停止" value="STOPPED" />
          <el-option label="失败" value="FAILED" />
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
          v-hasPermi="['autotest:batch:list']"
        >删除</el-button>
      </el-col>
      <right-toolbar :showSearch.sync="showSearch" @queryTable="getList"></right-toolbar>
    </el-row>

    <el-table v-loading="loading" :data="batchList" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" align="center" />
      <el-table-column label="批次ID" align="center" prop="batchId" width="80" />
      <el-table-column label="批次编号" align="center" prop="batchNo" :show-overflow-tooltip="true" width="180" />
      <el-table-column label="批次名称" align="center" prop="batchName" :show-overflow-tooltip="true" min-width="150" />
      <el-table-column label="状态" align="center" width="100">
        <template slot-scope="scope">
          <el-tag :type="scope.row.status === 'RUNNING' ? 'success' : scope.row.status === 'COMPLETED' ? 'info' : 'danger'">
            {{ scope.row.status === 'RUNNING' ? '运行中' : scope.row.status === 'COMPLETED' ? '已完成' : scope.row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="总数" align="center" prop="totalCases" width="80" />
      <el-table-column label="通过" align="center" prop="passCases" width="80">
        <template slot-scope="scope">
          <span style="color: #67C23A; font-weight: bold;">{{ scope.row.passCases }}</span>
        </template>
      </el-table-column>
      <el-table-column label="失败" align="center" prop="failCases" width="80">
        <template slot-scope="scope">
          <span style="color: #F56C6C; font-weight: bold;">{{ scope.row.failCases }}</span>
        </template>
      </el-table-column>
      <el-table-column label="超时" align="center" prop="timeoutCases" width="80" />
      <el-table-column label="通过率" align="center" width="100">
        <template slot-scope="scope">
          <span>{{ scope.row.passRate }}%</span>
        </template>
      </el-table-column>
      <el-table-column label="耗时(秒)" align="center" prop="durationSeconds" width="90" />
      <el-table-column label="启动时间" align="center" prop="startTime" width="160" />
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width" width="160">
        <template slot-scope="scope">
          <el-button
            size="mini"
            type="text"
            icon="el-icon-tickets"
            @click="handleViewCases(scope.row)"
          >用例追溯</el-button>
          <el-button
            size="mini"
            type="text"
            icon="el-icon-delete"
            @click="handleDelete(scope.row)"
            v-hasPermi="['autotest:batch:list']"
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
  </div>
</template>

<script>
import { listBatch, delBatch } from "@/api/autotest/batch";

export default {
  name: "AutoTestBatch",
  data() {
    return {
      loading: true,
      ids: [],
      multiple: true,
      showSearch: true,
      total: 0,
      batchList: [],
      queryParams: {
        pageNum: 1,
        pageSize: 10,
        batchNo: undefined,
        batchName: undefined,
        status: undefined
      }
    };
  },
  created() {
    this.getList();
  },
  methods: {
    getList() {
      this.loading = true;
      listBatch(this.queryParams).then(response => {
        this.batchList = response.rows;
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
      this.ids = selection.map(item => item.batchId);
      this.multiple = !selection.length;
    },
    handleViewCases(row) {
      this.$router.push({
        path: '/autotest/case',
        query: { batchId: row.batchId }
      });
    },
    handleDelete(row) {
      const batchIds = row.batchId || this.ids;
      this.$modal.confirm('是否确认删除测试批次编号为"' + batchIds + '"的数据项？').then(() => {
        return delBatch(batchIds);
      }).then(() => {
        this.getList();
        this.$modal.msgSuccess("删除成功");
      }).catch(() => {});
    }
  }
};
</script>
