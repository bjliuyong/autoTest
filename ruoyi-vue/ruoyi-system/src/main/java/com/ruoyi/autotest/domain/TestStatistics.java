package com.ruoyi.autotest.domain;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 测试看板与统计指标模型
 */
public class TestStatistics implements Serializable {
    private static final long serialVersionUID = 1L;

    private String currentStatus = "IDLE";
    private String currentBatchNo = "";
    private Integer totalCases = 0;
    private Integer passCases = 0;
    private Integer failCases = 0;
    private Integer timeoutCases = 0;
    private BigDecimal passRate = BigDecimal.ZERO;
    private Map<String, Integer> failureNodeCounts = new LinkedHashMap<>();

    public String getCurrentStatus() {
        return currentStatus;
    }

    public void setCurrentStatus(String currentStatus) {
        this.currentStatus = currentStatus;
    }

    public String getCurrentBatchNo() {
        return currentBatchNo;
    }

    public void setCurrentBatchNo(String currentBatchNo) {
        this.currentBatchNo = currentBatchNo;
    }

    public Integer getTotalCases() {
        return totalCases;
    }

    public void setTotalCases(Integer totalCases) {
        this.totalCases = totalCases;
    }

    public Integer getPassCases() {
        return passCases;
    }

    public void setPassCases(Integer passCases) {
        this.passCases = passCases;
    }

    public Integer getFailCases() {
        return failCases;
    }

    public void setFailCases(Integer failCases) {
        this.failCases = failCases;
    }

    public Integer getTimeoutCases() {
        return timeoutCases;
    }

    public void setTimeoutCases(Integer timeoutCases) {
        this.timeoutCases = timeoutCases;
    }

    public BigDecimal getPassRate() {
        return passRate;
    }

    public void setPassRate(BigDecimal passRate) {
        this.passRate = passRate;
    }

    public Map<String, Integer> getFailureNodeCounts() {
        return failureNodeCounts;
    }

    public void setFailureNodeCounts(Map<String, Integer> failureNodeCounts) {
        this.failureNodeCounts = failureNodeCounts;
    }
}
