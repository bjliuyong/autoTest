package com.ruoyi.web.controller.autotest;

import java.util.List;
import javax.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.autotest.domain.AutoTestCase;
import com.ruoyi.autotest.service.IAutoTestCaseService;
import com.ruoyi.common.annotation.Log;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.common.enums.BusinessType;
import com.ruoyi.common.utils.poi.ExcelUtil;

/**
 * 自动化测试用例记录Controller（持久化数据追溯）
 */
@RestController
@RequestMapping("/autotest/case")
public class AutoTestCaseController extends BaseController {

    @Autowired
    private IAutoTestCaseService caseService;

    /**
     * 查询测试用例追溯列表
     */
    @PreAuthorize("@ss.hasPermi('autotest:case:list')")
    @GetMapping("/list")
    public TableDataInfo list(AutoTestCase autoTestCase) {
        startPage();
        List<AutoTestCase> list = caseService.selectAutoTestCaseList(autoTestCase);
        return getDataTable(list);
    }

    /**
     * 获取用例完整追溯信息（包含照片、日志切片、判定原因）
     */
    @PreAuthorize("@ss.hasPermi('autotest:case:list')")
    @GetMapping(value = "/{caseRecordId}")
    public AjaxResult getInfo(@PathVariable("caseRecordId") Long caseRecordId) {
        return success(caseService.selectAutoTestCaseByCaseRecordId(caseRecordId));
    }

    /**
     * 删除用例记录
     */
    @PreAuthorize("@ss.hasPermi('autotest:case:list')")
    @Log(title = "测试用例记录", businessType = BusinessType.DELETE)
    @DeleteMapping("/{caseRecordIds}")
    public AjaxResult remove(@PathVariable Long[] caseRecordIds) {
        return toAjax(caseService.deleteAutoTestCaseByCaseRecordIds(caseRecordIds));
    }

    /**
     * 导出用例记录
     */
    @PreAuthorize("@ss.hasPermi('autotest:case:list')")
    @Log(title = "测试用例记录", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, AutoTestCase autoTestCase) {
        List<AutoTestCase> list = caseService.selectAutoTestCaseList(autoTestCase);
        ExcelUtil<AutoTestCase> util = new ExcelUtil<>(AutoTestCase.class);
        util.exportExcel(response, list, "用例追溯记录");
    }
}
