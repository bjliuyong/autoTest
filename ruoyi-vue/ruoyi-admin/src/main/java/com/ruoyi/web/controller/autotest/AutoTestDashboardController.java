package com.ruoyi.web.controller.autotest;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.autotest.domain.AutoTestBatch;
import com.ruoyi.autotest.domain.TestStatistics;
import com.ruoyi.autotest.engine.AutoTestEngine;
import com.ruoyi.autotest.service.IAutoTestBatchService;
import com.ruoyi.autotest.service.IAutoTestCaseService;
import com.ruoyi.common.annotation.Log;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.enums.BusinessType;

/**
 * 自动化测试大盘与控制台
 */
@RestController
@RequestMapping("/autotest/dashboard")
public class AutoTestDashboardController extends BaseController {

    @Autowired
    private AutoTestEngine autoTestEngine;

    @Autowired
    private IAutoTestBatchService batchService;

    @Autowired
    private IAutoTestCaseService caseService;

    /**
     * 获取大盘运行状态与实时统计指标
     */
    @PreAuthorize("@ss.hasPermi('autotest:dashboard:view')")
    @GetMapping("/stats")
    public AjaxResult getStats() {
        TestStatistics stats = new TestStatistics();
        boolean running = autoTestEngine.isRunning();
        stats.setCurrentStatus(running ? "RUNNING" : "IDLE");

        AutoTestBatch batch = autoTestEngine.getCurrentBatch();
        if (batch == null) {
            batch = batchService.selectLatestBatch();
        }

        Long batchId = null;
        if (batch != null) {
            stats.setCurrentBatchNo(batch.getBatchNo());
            stats.setTotalCases(batch.getTotalCases());
            stats.setPassCases(batch.getPassCases());
            stats.setFailCases(batch.getFailCases());
            stats.setTimeoutCases(batch.getTimeoutCases());
            stats.setPassRate(batch.getPassRate());
            batchId = batch.getBatchId();
        }

        // 节点失败分布
        List<Map<String, Object>> nodeFails = caseService.countFailuresByNode(batchId);
        Map<String, Integer> failureMap = new HashMap<>();
        if (nodeFails != null) {
            for (Map<String, Object> item : nodeFails) {
                String node = (String) item.get("nodeName");
                Number count = (Number) item.get("failureCount");
                if (node != null && count != null) {
                    failureMap.put(node, count.intValue());
                }
            }
        }
        stats.setFailureNodeCounts(failureMap);

        AjaxResult ajax = AjaxResult.success(stats);
        ajax.put("currentBatch", batch);
        ajax.put("isRunning", running);
        return ajax;
    }

    /**
     * 启动测试
     */
    @PreAuthorize("@ss.hasPermi('autotest:dashboard:view')")
    @Log(title = "启动自动化测试", businessType = BusinessType.INSERT)
    @PostMapping("/start")
    public AjaxResult startTest(@RequestBody(required = false) Map<String, Object> params) {
        Long configId = null;
        String batchName = null;
        if (params != null) {
            if (params.get("configId") != null) {
                configId = Long.valueOf(params.get("configId").toString());
            }
            if (params.get("batchName") != null) {
                batchName = params.get("batchName").toString();
            }
        }
        String msg = autoTestEngine.startTest(configId, batchName);
        return AjaxResult.success(msg);
    }

    /**
     * 停止测试
     */
    @PreAuthorize("@ss.hasPermi('autotest:dashboard:view')")
    @Log(title = "停止自动化测试", businessType = BusinessType.UPDATE)
    @PostMapping("/stop")
    public AjaxResult stopTest() {
        String msg = autoTestEngine.stopTest();
        return AjaxResult.success(msg);
    }

    /**
     * 运行自测模拟 (Mock Simulator)
     */
    @PreAuthorize("@ss.hasPermi('autotest:dashboard:view')")
    @Log(title = "触发自动化测试模拟", businessType = BusinessType.OTHER)
    @PostMapping("/simulate")
    public AjaxResult simulate(@RequestParam(defaultValue = "5") Integer count) {
        autoTestEngine.runSimulation(count);
        return AjaxResult.success("自测模拟已启动，正在后台生成用例并执行严格判定...");
    }
}
