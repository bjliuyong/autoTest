package com.ruoyi.autotest.domain;

import java.math.BigDecimal;
import java.util.Date;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.ruoyi.common.annotation.Excel;
import com.ruoyi.common.core.domain.BaseEntity;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

/**
 * 自动化测试批次表 auto_test_batch
 */
public class AutoTestBatch extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 批次ID */
    private Long batchId;

    /** 批次号(唯一编码) */
    @Excel(name = "批次号")
    private String batchNo;

    /** 批次名称 */
    @Excel(name = "批次名称")
    private String batchName;

    /** 状态(IDLE/RUNNING/COMPLETED/STOPPED/FAILED) */
    @Excel(name = "状态")
    private String status;

    /** 闸机WS连接地址 */
    @Excel(name = "闸机WS地址")
    private String gateWsUrl;

    /** 待测日志WS连接地址 */
    @Excel(name = "待测日志WS地址")
    private String logWsUrl;

    /** 总用例数 */
    @Excel(name = "总用例数")
    private Integer totalCases;

    /** 通过用例数 */
    @Excel(name = "通过用例数")
    private Integer passCases;

    /** 失败用例数 */
    @Excel(name = "失败用例数")
    private Integer failCases;

    /** 超时用例数 */
    @Excel(name = "超时用例数")
    private Integer timeoutCases;

    /** 通过率百分比 */
    @Excel(name = "通过率(%)")
    private BigDecimal passRate;

    /** 启动时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "启动时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date startTime;

    /** 结束时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "结束时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date endTime;

    /** 执行耗时(秒) */
    @Excel(name = "执行耗时(秒)")
    private Long durationSeconds;

    public void setBatchId(Long batchId) 
    {
        this.batchId = batchId;
    }

    public Long getBatchId() 
    {
        return batchId;
    }

    public void setBatchNo(String batchNo) 
    {
        this.batchNo = batchNo;
    }

    public String getBatchNo() 
    {
        return batchNo;
    }

    public void setBatchName(String batchName) 
    {
        this.batchName = batchName;
    }

    public String getBatchName() 
    {
        return batchName;
    }

    public void setStatus(String status) 
    {
        this.status = status;
    }

    public String getStatus() 
    {
        return status;
    }

    public void setGateWsUrl(String gateWsUrl) 
    {
        this.gateWsUrl = gateWsUrl;
    }

    public String getGateWsUrl() 
    {
        return gateWsUrl;
    }

    public void setLogWsUrl(String logWsUrl) 
    {
        this.logWsUrl = logWsUrl;
    }

    public String getLogWsUrl() 
    {
        return logWsUrl;
    }

    public void setTotalCases(Integer totalCases) 
    {
        this.totalCases = totalCases;
    }

    public Integer getTotalCases() 
    {
        return totalCases;
    }

    public void setPassCases(Integer passCases) 
    {
        this.passCases = passCases;
    }

    public Integer getPassCases() 
    {
        return passCases;
    }

    public void setFailCases(Integer failCases) 
    {
        this.failCases = failCases;
    }

    public Integer getFailCases() 
    {
        return failCases;
    }

    public void setTimeoutCases(Integer timeoutCases) 
    {
        this.timeoutCases = timeoutCases;
    }

    public Integer getTimeoutCases() 
    {
        return timeoutCases;
    }

    public void setPassRate(BigDecimal passRate) 
    {
        this.passRate = passRate;
    }

    public BigDecimal getPassRate() 
    {
        return passRate;
    }

    public void setStartTime(Date startTime) 
    {
        this.startTime = startTime;
    }

    public Date getStartTime() 
    {
        return startTime;
    }

    public void setEndTime(Date endTime) 
    {
        this.endTime = endTime;
    }

    public Date getEndTime() 
    {
        return endTime;
    }

    public void setDurationSeconds(Long durationSeconds) 
    {
        this.durationSeconds = durationSeconds;
    }

    public Long getDurationSeconds() 
    {
        return durationSeconds;
    }

    @Override
    public String toString() {
        return new ToStringBuilder(this,ToStringStyle.MULTI_LINE_STYLE)
            .append("batchId", getBatchId())
            .append("batchNo", getBatchNo())
            .append("batchName", getBatchName())
            .append("status", getStatus())
            .append("gateWsUrl", getGateWsUrl())
            .append("logWsUrl", getLogWsUrl())
            .append("totalCases", getTotalCases())
            .append("passCases", getPassCases())
            .append("failCases", getFailCases())
            .append("timeoutCases", getTimeoutCases())
            .append("passRate", getPassRate())
            .append("startTime", getStartTime())
            .append("endTime", getEndTime())
            .append("durationSeconds", getDurationSeconds())
            .append("createBy", getCreateBy())
            .append("createTime", getCreateTime())
            .append("updateBy", getUpdateBy())
            .append("updateTime", getUpdateTime())
            .append("remark", getRemark())
            .toString();
    }
}
