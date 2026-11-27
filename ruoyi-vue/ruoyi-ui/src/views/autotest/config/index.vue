<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryForm" size="small" :inline="true" v-show="showSearch" label-width="68px">
      <el-form-item label="配置名称" prop="configName">
        <el-input
          v-model="queryParams.configName"
          placeholder="请输入配置名称"
          clearable
          style="width: 240px"
          @keyup.enter.native="handleQuery"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="el-icon-search" size="mini" @click="handleQuery">搜索</el-button>
        <el-button icon="el-icon-refresh" size="mini" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="el-icon-plus"
          size="mini"
          @click="handleAdd"
          v-hasPermi="['autotest:config:list']"
        >新增配置</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button
          type="danger"
          plain
          icon="el-icon-delete"
          size="mini"
          :disabled="multiple"
          @click="handleDelete"
          v-hasPermi="['autotest:config:list']"
        >删除</el-button>
      </el-col>
      <right-toolbar :showSearch.sync="showSearch" @queryTable="getList"></right-toolbar>
    </el-row>

    <el-table v-loading="loading" :data="configList" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" align="center" />
      <el-table-column label="配置ID" align="center" prop="configId" width="80" />
      <el-table-column label="配置方案名称" align="center" prop="configName" :show-overflow-tooltip="true" min-width="150" />
      <el-table-column label="闸机WS地址" align="center" prop="gateWsUrl" :show-overflow-tooltip="true" min-width="180" />
      <el-table-column label="待测日志WS地址" align="center" prop="logWsUrl" :show-overflow-tooltip="true" min-width="180" />
      <el-table-column label="超时(秒)" align="center" prop="timeoutSeconds" width="80" />
      <el-table-column label="模式" align="center" width="110">
        <template slot-scope="scope">
          <el-tag size="mini" :type="scope.row.isGateServer === 1 ? 'warning' : 'primary'">
            {{ scope.row.isGateServer === 1 ? '服务端监听' : '客户端直连' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="默认方案" align="center" width="90">
        <template slot-scope="scope">
          <el-tag size="mini" :type="scope.row.isDefault === 1 ? 'success' : 'info'">
            {{ scope.row.isDefault === 1 ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" align="center" prop="createTime" width="160" />
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width" width="140">
        <template slot-scope="scope">
          <el-button
            size="mini"
            type="text"
            icon="el-icon-edit"
            @click="handleUpdate(scope.row)"
            v-hasPermi="['autotest:config:list']"
          >修改</el-button>
          <el-button
            size="mini"
            type="text"
            icon="el-icon-delete"
            @click="handleDelete(scope.row)"
            v-hasPermi="['autotest:config:list']"
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

    <!-- 添加或修改测试配置对话框 -->
    <el-dialog :title="title" :visible.sync="open" width="700px" append-to-body>
      <el-form ref="form" :model="form" :rules="rules" label-width="120px">
        <el-form-item label="配置方案名称" prop="configName">
          <el-input v-model="form.configName" placeholder="例如：正式测试环境配置" />
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="16">
            <el-form-item label="闸机WS地址" prop="gateWsUrl">
              <el-input v-model="form.gateWsUrl" placeholder="ws://127.0.0.1:8765/gate" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="超时时间(秒)" prop="timeoutSeconds">
              <el-input-number v-model="form.timeoutSeconds" :min="1" :max="120" style="width: 100%;" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="待测日志WS地址" prop="logWsUrl">
          <el-input v-model="form.logWsUrl" placeholder="ws://127.0.0.1:8766/logs" />
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="闸机通道模式">
              <el-radio-group v-model="form.isGateServer">
                <el-radio :label="0">客户端直连</el-radio>
                <el-radio :label="1">服务端监听</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="设为默认方案">
              <el-switch v-model="form.isDefault" :active-value="1" :inactive-value="0" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-collapse>
          <el-collapse-item title="高级正则与别名映射规则配置 (点击展开)" name="1">
            <el-form-item label="会话创建正则">
              <el-input type="textarea" :rows="2" v-model="form.sessionCreatePattern" placeholder="匹配流程引擎创建新会话" />
            </el-form-item>
            <el-form-item label="节点失败正则">
              <el-input type="textarea" :rows="2" v-model="form.nodeFailurePattern" placeholder="匹配会话ID与节点执行失败" />
            </el-form-item>
            <el-form-item label="放行/离开正则">
              <el-input type="textarea" :rows="3" v-model="form.passengerExitPattern" placeholder="支持多行：离开通道/已放行/开闸成功" />
            </el-form-item>
            <el-form-item label="节点别名映射">
              <el-input type="textarea" :rows="2" v-model="form.nodeAliases" placeholder='JSON格式，例如 {"人脸比对": "人脸识别"}' />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item label="备注说明" style="margin-top: 15px;">
          <el-input type="textarea" v-model="form.remark" placeholder="请输入备注内容" />
        </el-form-item>
      </el-form>
      <div slot="footer" class="dialog-footer">
        <el-button type="primary" @click="submitForm">确 定</el-button>
        <el-button @click="cancel">取 消</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import { listConfig, getConfig, delConfig, addConfig, updateConfig } from "@/api/autotest/config";

export default {
  name: "AutoTestConfig",
  data() {
    return {
      loading: true,
      ids: [],
      multiple: true,
      showSearch: true,
      total: 0,
      configList: [],
      title: "",
      open: false,
      queryParams: {
        pageNum: 1,
        pageSize: 10,
        configName: undefined
      },
      form: {},
      rules: {
        configName: [
          { required: true, message: "配置方案名称不能为空", trigger: "blur" }
        ],
        gateWsUrl: [
          { required: true, message: "闸机WS地址不能为空", trigger: "blur" }
        ],
        logWsUrl: [
          { required: true, message: "待测日志WS地址不能为空", trigger: "blur" }
        ],
        timeoutSeconds: [
          { required: true, message: "超时时间不能为空", trigger: "blur" }
        ]
      }
    };
  },
  created() {
    this.getList();
  },
  methods: {
    getList() {
      this.loading = true;
      listConfig(this.queryParams).then(response => {
        this.configList = response.rows;
        this.total = response.total;
        this.loading = false;
      });
    },
    cancel() {
      this.open = false;
      this.reset();
    },
    reset() {
      this.form = {
        configId: undefined,
        configName: undefined,
        gateWsUrl: "ws://127.0.0.1:8765/gate",
        logWsUrl: "ws://127.0.0.1:8766/logs",
        isGateServer: 0,
        gateServerPort: 8765,
        timeoutSeconds: 15,
        sessionCreatePattern: undefined,
        nodeFailurePattern: undefined,
        passengerExitPattern: undefined,
        nodeAliases: undefined,
        isDefault: 1,
        remark: undefined
      };
      this.resetForm("form");
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
      this.ids = selection.map(item => item.configId);
      this.multiple = !selection.length;
    },
    handleAdd() {
      this.reset();
      this.open = true;
      this.title = "添加自动化测试配置方案";
    },
    handleUpdate(row) {
      this.reset();
      const configId = row.configId || this.ids;
      getConfig(configId).then(response => {
        this.form = response.data;
        this.open = true;
        this.title = "修改自动化测试配置方案";
      });
    },
    submitForm() {
      this.$refs["form"].validate(valid => {
        if (valid) {
          if (this.form.configId != null) {
            updateConfig(this.form).then(response => {
              this.$modal.msgSuccess("修改成功");
              this.open = false;
              this.getList();
            });
          } else {
            addConfig(this.form).then(response => {
              this.$modal.msgSuccess("新增成功");
              this.open = false;
              this.getList();
            });
          }
        }
      });
    },
    handleDelete(row) {
      const configIds = row.configId || this.ids;
      this.$modal.confirm('是否确认删除测试配置编号为"' + configIds + '"的数据项？').then(() => {
        return delConfig(configIds);
      }).then(() => {
        this.getList();
        this.$modal.msgSuccess("删除成功");
      }).catch(() => {});
    }
  }
};
</script>
