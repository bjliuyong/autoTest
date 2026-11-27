package com.ruoyi.autotest.mapper;

import java.util.List;
import java.util.Map;
import com.ruoyi.autotest.domain.AutoTestCase;

/**
 * 自动化测试用例记录Mapper接口
 */
public interface AutoTestCaseMapper 
{
    public AutoTestCase selectAutoTestCaseByCaseRecordId(Long caseRecordId);

    public List<AutoTestCase> selectAutoTestCaseList(AutoTestCase autoTestCase);

    public int insertAutoTestCase(AutoTestCase autoTestCase);

    public int updateAutoTestCase(AutoTestCase autoTestCase);

    public int deleteAutoTestCaseByCaseRecordId(Long caseRecordId);

    public int deleteAutoTestCaseByCaseRecordIds(Long[] caseRecordIds);

    public int deleteAutoTestCaseByBatchId(Long batchId);

    public List<Map<String, Object>> countFailuresByNode(Long batchId);
}
