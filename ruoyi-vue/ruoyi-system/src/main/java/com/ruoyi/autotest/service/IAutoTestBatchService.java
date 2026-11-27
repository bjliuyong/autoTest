package com.ruoyi.autotest.service;

import java.util.List;
import com.ruoyi.autotest.domain.AutoTestBatch;

/**
 * 自动化测试批次Service接口
 */
public interface IAutoTestBatchService 
{
    public AutoTestBatch selectAutoTestBatchByBatchId(Long batchId);

    public AutoTestBatch selectAutoTestBatchByBatchNo(String batchNo);

    public List<AutoTestBatch> selectAutoTestBatchList(AutoTestBatch autoTestBatch);

    public int insertAutoTestBatch(AutoTestBatch autoTestBatch);

    public int updateAutoTestBatch(AutoTestBatch autoTestBatch);

    public int deleteAutoTestBatchByBatchIds(Long[] batchIds);

    public int deleteAutoTestBatchByBatchId(Long batchId);

    public AutoTestBatch selectLatestBatch();
}
