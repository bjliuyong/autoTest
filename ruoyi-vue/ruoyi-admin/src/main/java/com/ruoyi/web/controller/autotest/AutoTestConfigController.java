package com.ruoyi.web.controller.autotest;

import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.autotest.domain.AutoTestConfig;
import com.ruoyi.autotest.service.IAutoTestConfigService;
import com.ruoyi.common.annotation.Log;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.common.enums.BusinessType;

/**
 * 自动化测试参数配置Controller
 */
@RestController
@RequestMapping("/autotest/config")
public class AutoTestConfigController extends BaseController {

    @Autowired
    private IAutoTestConfigService configService;

    /**
     * 查询配置列表
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @GetMapping("/list")
    public TableDataInfo list(AutoTestConfig autoTestConfig) {
        startPage();
        List<AutoTestConfig> list = configService.selectAutoTestConfigList(autoTestConfig);
        return getDataTable(list);
    }

    /**
     * 获取默认配置
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @GetMapping("/default")
    public AjaxResult getDefault() {
        return success(configService.selectDefaultConfig());
    }

    /**
     * 获取配置详情
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @GetMapping(value = "/{configId}")
    public AjaxResult getInfo(@PathVariable("configId") Long configId) {
        return success(configService.selectAutoTestConfigByConfigId(configId));
    }

    /**
     * 新增配置
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @Log(title = "测试参数配置", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody AutoTestConfig autoTestConfig) {
        return toAjax(configService.insertAutoTestConfig(autoTestConfig));
    }

    /**
     * 修改配置
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @Log(title = "测试参数配置", businessType = BusinessType.UPDATE)
    @PutMapping
    public AjaxResult edit(@RequestBody AutoTestConfig autoTestConfig) {
        return toAjax(configService.updateAutoTestConfig(autoTestConfig));
    }

    /**
     * 删除配置
     */
    @PreAuthorize("@ss.hasPermi('autotest:config:list')")
    @Log(title = "测试参数配置", businessType = BusinessType.DELETE)
    @DeleteMapping("/{configIds}")
    public AjaxResult remove(@PathVariable Long[] configIds) {
        return toAjax(configService.deleteAutoTestConfigByConfigIds(configIds));
    }
}
