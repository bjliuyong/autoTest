package com.ruoyi.autotest.service.impl;

import java.util.List;
import com.ruoyi.common.utils.DateUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.autotest.mapper.AutoTestBatchMapper;
import com.ruoyi.autotest.domain.AutoTestBatch;
import com.ruoyi.autotest.service.IAutoTestBatchService;

/**
 * 自动化测试批次Service业务层处理
 */
@Service
public class AutoTestBatchServiceImpl implements IAutoTestBatchService 
{
    @Autowired
    private AutoTestBatchMapper autoTestBatchMapper;

    @Override
    public AutoTestBatch selectAutoTestBatchByBatchId(Long batchId)
    {
        return autoTestBatchMapper.selectAutoTestBatchByBatchId(batchId);
    }

    @Override
    public AutoTestBatch selectAutoTestBatchByBatchNo(String batchNo)
    {
        return autoTestBatchMapper.selectAutoTestBatchByBatchNo(batchNo);
    }

    @Override
    public List<AutoTestBatch> selectAutoTestBatchList(AutoTestBatch autoTestBatch)
    {
        return autoTestBatchMapper.selectAutoTestBatchList(autoTestBatch);
    }

    @Override
    public int insertAutoTestBatch(AutoTestBatch autoTestBatch)
    {
        autoTestBatch.setCreateTime(DateUtils.getNowDate());
        return autoTestBatchMapper.insertAutoTestBatch(autoTestBatch);
    }

    @Override
    public int updateAutoTestBatch(AutoTestBatch autoTestBatch)
    {
        autoTestBatch.setUpdateTime(DateUtils.getNowDate());
        return autoTestBatchMapper.updateAutoTestBatch(autoTestBatch);
    }

    @Override
    public int deleteAutoTestBatchByBatchIds(Long[] batchIds)
    {
        return autoTestBatchMapper.deleteAutoTestBatchByBatchIds(batchIds);
    }

    @Override
    public int deleteAutoTestBatchByBatchId(Long batchId)
    {
        return autoTestBatchMapper.deleteAutoTestBatchByBatchId(batchId);
    }

    @Override
    public AutoTestBatch selectLatestBatch()
    {
        return autoTestBatchMapper.selectLatestBatch();
    }
}
