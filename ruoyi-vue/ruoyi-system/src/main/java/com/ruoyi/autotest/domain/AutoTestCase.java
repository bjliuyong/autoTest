package com.ruoyi.autotest.domain;

import java.util.Date;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.ruoyi.common.annotation.Excel;
import com.ruoyi.common.core.domain.BaseEntity;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

/**
 * 自动化测试用例记录表 auto_test_case
 */
public class AutoTestCase extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 用例记录主键 */
    private Long caseRecordId;

    /** 归属测试批次ID */
    private Long batchId;

    /** 批次号 */
    @Excel(name = "批次号")
    private String batchNo;

    /** 用例编号 */
    @Excel(name = "用例编号")
    private String caseId;

    /** 单次采集唯一序号 */
    private String serial;

    /** 闸机编号 */
    @Excel(name = "闸机编号")
    private String gateId;

    /** 旅客姓名 */
    @Excel(name = "旅客姓名")
    private String passengerName;

    /** 证件类型 */
    @Excel(name = "证件类型")
    private String certType;

    /** 证件号码 */
    @Excel(name = "证件号码")
    private String certNo;

    /** 采集照片(Base64或路径/URL) */
    private String photoUrl;

    /** 关联的待测系统会话ID */
    @Excel(name = "会话ID")
    private String sessionId;

    /** 预期是否放行(1:放行, 0:拦截) */
    @Excel(name = "预期放行", readConverterExp = "1=放行,0=拦截")
    private Integer expectedAllowPass;

    /** 预期失败节点名称 */
    @Excel(name = "预期失败节点")
    private String expectedFailedNode;

    /** 实际是否放行(1:放行, 0:拦截) */
    @Excel(name = "实际放行", readConverterExp = "1=放行,0=拦截")
    private Integer actualAllowPass;

    /** 实际失败节点名称 */
    @Excel(name = "实际失败节点")
    private String actualFailedNode;

    /** 判定结果(PASS:通过, FAIL:不通过, TIMEOUT:超时, ERROR:异常) */
    @Excel(name = "判定结果")
    private String verdict;

    /** 判定结论与原因详情 */
    @Excel(name = "判定原因")
    private String reasons;

    /** 闸机原始推送采集报文(JSON) */
    private String rawCapture;

    /** 该旅客在待测系统中的全量日志切片 */
    private String logSlice;

    /** 从采集到判定耗时(毫秒) */
    @Excel(name = "耗时(ms)")
    private Long durationMs;

    /** 用例接收时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "接收时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date receivedTime;

    /** 判定完成时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "完成时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date finishTime;

    public void setCaseRecordId(Long caseRecordId) 
    {
        this.caseRecordId = caseRecordId;
    }

    public Long getCaseRecordId() 
    {
        return caseRecordId;
    }

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

    public void setCaseId(String caseId) 
    {
        this.caseId = caseId;
    }

    public String getCaseId() 
    {
        return caseId;
    }

    public void setSerial(String serial) 
    {
        this.serial = serial;
    }

    public String getSerial() 
    {
        return serial;
    }

    public void setGateId(String gateId) 
    {
        this.gateId = gateId;
    }

    public String getGateId() 
    {
        return gateId;
    }

    public void setPassengerName(String passengerName) 
    {
        this.passengerName = passengerName;
    }

    public String getPassengerName() 
    {
        return passengerName;
    }

    public void setCertType(String certType) 
    {
        this.certType = certType;
    }

    public String getCertType() 
    {
        return certType;
    }

    public void setCertNo(String certNo) 
    {
        this.certNo = certNo;
    }

    public String getCertNo() 
    {
        return certNo;
    }

    public void setPhotoUrl(String photoUrl) 
    {
        this.photoUrl = photoUrl;
    }

    public String getPhotoUrl() 
    {
        return photoUrl;
    }

    public void setSessionId(String sessionId) 
    {
        this.sessionId = sessionId;
    }

    public String getSessionId() 
    {
        return sessionId;
    }

    public void setExpectedAllowPass(Integer expectedAllowPass) 
    {
        this.expectedAllowPass = expectedAllowPass;
    }

    public Integer getExpectedAllowPass() 
    {
        return expectedAllowPass;
    }

    public void setExpectedFailedNode(String expectedFailedNode) 
    {
        this.expectedFailedNode = expectedFailedNode;
    }

    public String getExpectedFailedNode() 
    {
        return expectedFailedNode;
    }

    public void setActualAllowPass(Integer actualAllowPass) 
    {
        this.actualAllowPass = actualAllowPass;
    }

    public Integer getActualAllowPass() 
    {
        return actualAllowPass;
    }

    public void setActualFailedNode(String actualFailedNode) 
    {
        this.actualFailedNode = actualFailedNode;
    }

    public String getActualFailedNode() 
    {
        return actualFailedNode;
    }

    public void setVerdict(String verdict) 
    {
        this.verdict = verdict;
    }

    public String getVerdict() 
    {
        return verdict;
    }

    public void setReasons(String reasons) 
    {
        this.reasons = reasons;
    }

    public String getReasons() 
    {
        return reasons;
    }

    public void setRawCapture(String rawCapture) 
    {
        this.rawCapture = rawCapture;
    }

    public String getRawCapture() 
    {
        return rawCapture;
    }

    public void setLogSlice(String logSlice) 
    {
        this.logSlice = logSlice;
    }

    public String getLogSlice() 
    {
        return logSlice;
    }

    public void setDurationMs(Long durationMs) 
    {
        this.durationMs = durationMs;
    }

    public Long getDurationMs() 
    {
        return durationMs;
    }

    public void setReceivedTime(Date receivedTime) 
    {
        this.receivedTime = receivedTime;
    }

    public Date getReceivedTime() 
    {
        return receivedTime;
    }

    public void setFinishTime(Date finishTime) 
    {
        this.finishTime = finishTime;
    }

    public Date getFinishTime() 
    {
        return finishTime;
    }

    @Override
    public String toString() {
        return new ToStringBuilder(this,ToStringStyle.MULTI_LINE_STYLE)
            .append("caseRecordId", getCaseRecordId())
            .append("batchId", getBatchId())
            .append("batchNo", getBatchNo())
            .append("caseId", getCaseId())
            .append("serial", getSerial())
            .append("gateId", getGateId())
            .append("passengerName", getPassengerName())
            .append("certType", getCertType())
            .append("certNo", getCertNo())
            .append("sessionId", getSessionId())
            .append("expectedAllowPass", getExpectedAllowPass())
            .append("expectedFailedNode", getExpectedFailedNode())
            .append("actualAllowPass", getActualAllowPass())
            .append("actualFailedNode", getActualFailedNode())
            .append("verdict", getVerdict())
            .append("reasons", getReasons())
            .append("durationMs", getDurationMs())
            .append("receivedTime", getReceivedTime())
            .append("finishTime", getFinishTime())
            .append("createTime", getCreateTime())
            .toString();
    }
}
