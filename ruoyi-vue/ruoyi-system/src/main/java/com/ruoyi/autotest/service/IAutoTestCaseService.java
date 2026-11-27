package com.ruoyi.autotest.service;

import java.util.List;
import java.util.Map;
import com.ruoyi.autotest.domain.AutoTestCase;

/**
 * 自动化测试用例记录Service接口
 */
public interface IAutoTestCaseService 
{
    public AutoTestCase selectAutoTestCaseByCaseRecordId(Long caseRecordId);

    public List<AutoTestCase> selectAutoTestCaseList(AutoTestCase autoTestCase);

    public int insertAutoTestCase(AutoTestCase autoTestCase);

    public int updateAutoTestCase(AutoTestCase autoTestCase);

    public int deleteAutoTestCaseByCaseRecordIds(Long[] caseRecordIds);

    public int deleteAutoTestCaseByCaseRecordId(Long caseRecordId);

    public int deleteAutoTestCaseByBatchId(Long batchId);

    public List<Map<String, Object>> countFailuresByNode(Long batchId);
}
