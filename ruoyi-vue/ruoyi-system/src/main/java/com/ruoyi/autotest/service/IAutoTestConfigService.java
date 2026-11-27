package com.ruoyi.autotest.service;

import java.util.List;
import com.ruoyi.autotest.domain.AutoTestConfig;

/**
 * 自动化测试参数配置Service接口
 */
public interface IAutoTestConfigService 
{
    public AutoTestConfig selectAutoTestConfigByConfigId(Long configId);

    public AutoTestConfig selectDefaultConfig();

    public List<AutoTestConfig> selectAutoTestConfigList(AutoTestConfig autoTestConfig);

    public int insertAutoTestConfig(AutoTestConfig autoTestConfig);

    public int updateAutoTestConfig(AutoTestConfig autoTestConfig);

    public int deleteAutoTestConfigByConfigIds(Long[] configIds);

    public int deleteAutoTestConfigByConfigId(Long configId);
}
