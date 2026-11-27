package com.ruoyi.web.controller.autotest;

import java.util.List;
import javax.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.autotest.domain.AutoTestBatch;
import com.ruoyi.autotest.service.IAutoTestBatchService;
import com.ruoyi.common.annotation.Log;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.common.enums.BusinessType;
import com.ruoyi.common.utils.poi.ExcelUtil;

/**
 * 自动化测试批次Controller
 */
@RestController
@RequestMapping("/autotest/batch")
public class AutoTestBatchController extends BaseController {

    @Autowired
    private IAutoTestBatchService batchService;

    /**
     * 查询自动化测试批次列表
     */
    @PreAuthorize("@ss.hasPermi('autotest:batch:list')")
    @GetMapping("/list")
    public TableDataInfo list(AutoTestBatch autoTestBatch) {
        startPage();
        List<AutoTestBatch> list = batchService.selectAutoTestBatchList(autoTestBatch);
        return getDataTable(list);
    }

    /**
     * 获取自动化测试批次详细信息
     */
    @PreAuthorize("@ss.hasPermi('autotest:batch:list')")
    @GetMapping(value = "/{batchId}")
    public AjaxResult getInfo(@PathVariable("batchId") Long batchId) {
        return success(batchService.selectAutoTestBatchByBatchId(batchId));
    }

    /**
     * 删除自动化测试批次
     */
    @PreAuthorize("@ss.hasPermi('autotest:batch:list')")
    @Log(title = "自动化测试批次", businessType = BusinessType.DELETE)
    @DeleteMapping("/{batchIds}")
    public AjaxResult remove(@PathVariable Long[] batchIds) {
        return toAjax(batchService.deleteAutoTestBatchByBatchIds(batchIds));
    }

    /**
     * 导出自动化测试批次列表
     */
    @PreAuthorize("@ss.hasPermi('autotest:batch:list')")
    @Log(title = "自动化测试批次", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, AutoTestBatch autoTestBatch) {
        List<AutoTestBatch> list = batchService.selectAutoTestBatchList(autoTestBatch);
        ExcelUtil<AutoTestBatch> util = new ExcelUtil<>(AutoTestBatch.class);
        util.exportExcel(response, list, "测试批次数据");
    }
}
