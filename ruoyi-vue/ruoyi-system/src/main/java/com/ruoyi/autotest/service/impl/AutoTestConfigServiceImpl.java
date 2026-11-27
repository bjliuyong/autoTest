package com.ruoyi.autotest.service.impl;

import java.util.List;
import com.ruoyi.common.utils.DateUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.ruoyi.autotest.mapper.AutoTestConfigMapper;
import com.ruoyi.autotest.domain.AutoTestConfig;
import com.ruoyi.autotest.service.IAutoTestConfigService;

/**
 * 自动化测试参数配置Service业务层处理
 */
@Service
public class AutoTestConfigServiceImpl implements IAutoTestConfigService 
{
    @Autowired
    private AutoTestConfigMapper autoTestConfigMapper;

    @Override
    public AutoTestConfig selectAutoTestConfigByConfigId(Long configId)
    {
        return autoTestConfigMapper.selectAutoTestConfigByConfigId(configId);
    }

    @Override
    public AutoTestConfig selectDefaultConfig()
    {
        AutoTestConfig config = autoTestConfigMapper.selectDefaultConfig();
        if (config == null) {
            config = new AutoTestConfig();
            config.setConfigName("默认闸机测试配置");
            config.setGateWsUrl("ws://127.0.0.1:8765/gate");
            config.setLogWsUrl("ws://127.0.0.1:8766/logs");
            config.setIsGateServer(0);
            config.setGateServerPort(8765);
            config.setTimeoutSeconds(15);
            config.setIsDefault(1);
        }
        return config;
    }

    @Override
    public List<AutoTestConfig> selectAutoTestConfigList(AutoTestConfig autoTestConfig)
    {
        return autoTestConfigMapper.selectAutoTestConfigList(autoTestConfig);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public int insertAutoTestConfig(AutoTestConfig autoTestConfig)
    {
        if (autoTestConfig.getIsDefault() != null && autoTestConfig.getIsDefault() == 1) {
            autoTestConfigMapper.resetDefaultFlag();
        }
        autoTestConfig.setCreateTime(DateUtils.getNowDate());
        return autoTestConfigMapper.insertAutoTestConfig(autoTestConfig);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public int updateAutoTestConfig(AutoTestConfig autoTestConfig)
    {
        if (autoTestConfig.getIsDefault() != null && autoTestConfig.getIsDefault() == 1) {
            autoTestConfigMapper.resetDefaultFlag();
        }
        autoTestConfig.setUpdateTime(DateUtils.getNowDate());
        return autoTestConfigMapper.updateAutoTestConfig(autoTestConfig);
    }

    @Override
    public int deleteAutoTestConfigByConfigIds(Long[] configIds)
    {
        return autoTestConfigMapper.deleteAutoTestConfigByConfigIds(configIds);
    }

    @Override
    public int deleteAutoTestConfigByConfigId(Long configId)
    {
        return autoTestConfigMapper.deleteAutoTestConfigByConfigId(configId);
    }
}
