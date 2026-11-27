package com.ruoyi.autotest.domain;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

/**
 * 判定结果模型
 */
public class Verdict implements Serializable {
    private static final long serialVersionUID = 1L;

    private boolean passed;
    private String verdictCode; // PASS, FAIL, TIMEOUT, ERROR
    private List<String> reasons = new ArrayList<>();
    private Boolean expectedAllow;
    private Boolean actualAllow;
    private String expectedNode;
    private String actualNode;
    private String sessionId;
    private boolean timedOut;
    private String logSlice;

    public boolean isPassed() {
        return passed;
    }

    public void setPassed(boolean passed) {
        this.passed = passed;
    }

    public String getVerdictCode() {
        return verdictCode;
    }

    public void setVerdictCode(String verdictCode) {
        this.verdictCode = verdictCode;
    }

    public List<String> getReasons() {
        return reasons;
    }

    public void setReasons(List<String> reasons) {
        this.reasons = reasons;
    }

    public void addReason(String reason) {
        if (this.reasons == null) {
            this.reasons = new ArrayList<>();
        }
        this.reasons.add(reason);
    }

    public Boolean getExpectedAllow() {
        return expectedAllow;
    }

    public void setExpectedAllow(Boolean expectedAllow) {
        this.expectedAllow = expectedAllow;
    }

    public Boolean getActualAllow() {
        return actualAllow;
    }

    public void setActualAllow(Boolean actualAllow) {
        this.actualAllow = actualAllow;
    }

    public String getExpectedNode() {
        return expectedNode;
    }

    public void setExpectedNode(String expectedNode) {
        this.expectedNode = expectedNode;
    }

    public String getActualNode() {
        return actualNode;
    }

    public void setActualNode(String actualNode) {
        this.actualNode = actualNode;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public boolean isTimedOut() {
        return timedOut;
    }

    public void setTimedOut(boolean timedOut) {
        this.timedOut = timedOut;
    }

    public String getLogSlice() {
        return logSlice;
    }

    public void setLogSlice(String logSlice) {
        this.logSlice = logSlice;
    }

    public String getReasonsAsString() {
        if (reasons == null || reasons.isEmpty()) {
            return passed ? "判定通过：预期结果与系统实际运行结论一致" : "判定失败";
        }
        return String.join("；", reasons);
    }
}
