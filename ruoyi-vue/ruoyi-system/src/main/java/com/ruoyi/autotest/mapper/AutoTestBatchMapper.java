package com.ruoyi.autotest.mapper;

import java.util.List;
import com.ruoyi.autotest.domain.AutoTestBatch;

/**
 * 自动化测试批次Mapper接口
 */
public interface AutoTestBatchMapper 
{
    public AutoTestBatch selectAutoTestBatchByBatchId(Long batchId);

    public AutoTestBatch selectAutoTestBatchByBatchNo(String batchNo);

    public List<AutoTestBatch> selectAutoTestBatchList(AutoTestBatch autoTestBatch);

    public int insertAutoTestBatch(AutoTestBatch autoTestBatch);

    public int updateAutoTestBatch(AutoTestBatch autoTestBatch);

    public int deleteAutoTestBatchByBatchId(Long batchId);

    public int deleteAutoTestBatchByBatchIds(Long[] batchIds);

    public AutoTestBatch selectLatestBatch();
}
