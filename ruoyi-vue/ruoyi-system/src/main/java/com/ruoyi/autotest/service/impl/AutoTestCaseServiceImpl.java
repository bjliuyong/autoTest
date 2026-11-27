package com.ruoyi.autotest.service.impl;

import java.util.List;
import java.util.Map;
import com.ruoyi.common.utils.DateUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.autotest.mapper.AutoTestCaseMapper;
import com.ruoyi.autotest.domain.AutoTestCase;
import com.ruoyi.autotest.service.IAutoTestCaseService;

/**
 * 自动化测试用例记录Service业务层处理
 */
@Service
public class AutoTestCaseServiceImpl implements IAutoTestCaseService 
{
    @Autowired
    private AutoTestCaseMapper autoTestCaseMapper;

    @Override
    public AutoTestCase selectAutoTestCaseByCaseRecordId(Long caseRecordId)
    {
        return autoTestCaseMapper.selectAutoTestCaseByCaseRecordId(caseRecordId);
    }

    @Override
    public List<AutoTestCase> selectAutoTestCaseList(AutoTestCase autoTestCase)
    {
        return autoTestCaseMapper.selectAutoTestCaseList(autoTestCase);
    }

    @Override
    public int insertAutoTestCase(AutoTestCase autoTestCase)
    {
        autoTestCase.setCreateTime(DateUtils.getNowDate());
        return autoTestCaseMapper.insertAutoTestCase(autoTestCase);
    }

    @Override
    public int updateAutoTestCase(AutoTestCase autoTestCase)
    {
        return autoTestCaseMapper.updateAutoTestCase(autoTestCase);
    }

    @Override
    public int deleteAutoTestCaseByCaseRecordIds(Long[] caseRecordIds)
    {
        return autoTestCaseMapper.deleteAutoTestCaseByCaseRecordIds(caseRecordIds);
    }

    @Override
    public int deleteAutoTestCaseByCaseRecordId(Long caseRecordId)
    {
        return autoTestCaseMapper.deleteAutoTestCaseByCaseRecordId(caseRecordId);
    }

    @Override
    public int deleteAutoTestCaseByBatchId(Long batchId)
    {
        return autoTestCaseMapper.deleteAutoTestCaseByBatchId(batchId);
    }

    @Override
    public List<Map<String, Object>> countFailuresByNode(Long batchId)
    {
        return autoTestCaseMapper.countFailuresByNode(batchId);
    }
}
