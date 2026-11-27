package com.ruoyi.autotest.domain;

import java.io.Serializable;
import java.util.Date;
import java.util.Map;

/**
 * 闸机采集用例实体（接收 WebSocket 报文解析后的强类型表示）
 */
public class GateCapture implements Serializable {
    private static final long serialVersionUID = 1L;

    private String caseId;
    private String serial;
    private String gateId;
    private Date timestamp;
    private boolean expectedAllowPass;
    private String expectedFailedNode;
    private String passengerName;
    private String certType;
    private String certNo;
    private String photoBase64;
    private Map<String, Object> captureData;
    private String rawJson;
    private long receivedTimeMillis;

    public GateCapture() {
        this.receivedTimeMillis = System.currentTimeMillis();
    }

    public String getCaseId() {
        return caseId;
    }

    public void setCaseId(String caseId) {
        this.caseId = caseId;
    }

    public String getSerial() {
        return serial;
    }

    public void setSerial(String serial) {
        this.serial = serial;
    }

    public String getGateId() {
        return gateId;
    }

    public void setGateId(String gateId) {
        this.gateId = gateId;
    }

    public Date getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(Date timestamp) {
        this.timestamp = timestamp;
    }

    public boolean isExpectedAllowPass() {
        return expectedAllowPass;
    }

    public void setExpectedAllowPass(boolean expectedAllowPass) {
        this.expectedAllowPass = expectedAllowPass;
    }

    public String getExpectedFailedNode() {
        return expectedFailedNode;
    }

    public void setExpectedFailedNode(String expectedFailedNode) {
        this.expectedFailedNode = expectedFailedNode;
    }

    public String getPassengerName() {
        return passengerName;
    }

    public void setPassengerName(String passengerName) {
        this.passengerName = passengerName;
    }

    public String getCertType() {
        return certType;
    }

    public void setCertType(String certType) {
        this.certType = certType;
    }

    public String getCertNo() {
        return certNo;
    }

    public void setCertNo(String certNo) {
        this.certNo = certNo;
    }

    public String getPhotoBase64() {
        return photoBase64;
    }

    public void setPhotoBase64(String photoBase64) {
        this.photoBase64 = photoBase64;
    }

    public Map<String, Object> getCaptureData() {
        return captureData;
    }

    public void setCaptureData(Map<String, Object> captureData) {
        this.captureData = captureData;
    }

    public String getRawJson() {
        return rawJson;
    }

    public void setRawJson(String rawJson) {
        this.rawJson = rawJson;
    }

    public long getReceivedTimeMillis() {
        return receivedTimeMillis;
    }

    public void setReceivedTimeMillis(long receivedTimeMillis) {
        this.receivedTimeMillis = receivedTimeMillis;
    }
}
