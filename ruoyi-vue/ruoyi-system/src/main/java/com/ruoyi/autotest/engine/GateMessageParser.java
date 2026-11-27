package com.ruoyi.autotest.engine;

import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.ruoyi.autotest.domain.GateCapture;

/**
 * 闸机报文解析器（与 Python autotest/protocol.py 保持完全兼容）
 */
public class GateMessageParser {

    public static GateCapture parse(String message) {
        if (message == null || message.trim().isEmpty()) {
            return null;
        }

        JSONObject json;
        try {
            json = JSON.parseObject(message);
        } catch (Exception e) {
            return null;
        }

        // 过滤非用例报文（例如心跳、响应等）
        String type = json.getString("type");
        if (type != null && !type.isEmpty()) {
            if ("gate.ack".equalsIgnoreCase(type) || "heartbeat".equalsIgnoreCase(type) || "ping".equalsIgnoreCase(type)) {
                return null;
            }
        }

        GateCapture capture = new GateCapture();
        capture.setRawJson(message);
        capture.setReceivedTimeMillis(System.currentTimeMillis());

        // 1. 基本字段
        String caseId = json.getString("case_id");
        if (caseId == null || caseId.trim().isEmpty()) {
            caseId = json.getString("caseId");
        }
        if (caseId == null || caseId.trim().isEmpty()) {
            caseId = "C-" + System.currentTimeMillis() % 1000000;
        }
        capture.setCaseId(caseId);

        String serial = json.getString("serial");
        if (serial == null || serial.trim().isEmpty()) {
            serial = caseId + "_" + System.currentTimeMillis();
        }
        capture.setSerial(serial);

        String gateId = json.getString("gate_id");
        if (gateId == null) {
            gateId = json.getString("gateId");
        }
        capture.setGateId(gateId != null ? gateId : "G01");
        capture.setTimestamp(new Date());

        // 2. 预期结果解析 (兼容嵌套 expected 和顶层 expected 字段)
        JSONObject expectedObj = json.getJSONObject("expected");
        boolean allowPass = false;
        String failedNode = null;

        if (expectedObj != null) {
            allowPass = parseBoolean(expectedObj.get("allow_pass"), expectedObj.get("pass"), expectedObj.get("allow"));
            failedNode = expectedObj.getString("failed_node");
            if (failedNode == null) {
                failedNode = expectedObj.getString("node");
            }
        } else {
            allowPass = parseBoolean(json.get("allow_pass"), json.get("pass"), json.get("allow"));
            failedNode = json.getString("failed_node");
            if (failedNode == null) {
                failedNode = json.getString("node");
            }
        }
        capture.setExpectedAllowPass(allowPass);
        capture.setExpectedFailedNode(failedNode);

        // 3. 旅客信息解析 (兼容嵌套 capture 和顶层信息)
        JSONObject captureObj = json.getJSONObject("capture");
        if (captureObj == null) {
            captureObj = json;
        }

        Map<String, Object> dataMap = new HashMap<>(captureObj);
        capture.setCaptureData(dataMap);

        String passengerName = captureObj.getString("passenger_name");
        if (passengerName == null) passengerName = captureObj.getString("passengerName");
        if (passengerName == null) passengerName = captureObj.getString("name");
        capture.setPassengerName(passengerName != null ? passengerName : "未知旅客");

        String certType = captureObj.getString("cert_type");
        if (certType == null) certType = captureObj.getString("certType");
        capture.setCertType(certType != null ? certType : "ID_CARD");

        String certNo = captureObj.getString("cert_no");
        if (certNo == null) certNo = captureObj.getString("certNo");
        if (certNo == null) certNo = captureObj.getString("id_no");
        capture.setCertNo(certNo != null ? certNo : "");

        String photoBase64 = captureObj.getString("photo_base64");
        if (photoBase64 == null) photoBase64 = captureObj.getString("photo");
        if (photoBase64 == null) photoBase64 = captureObj.getString("photoUrl");
        capture.setPhotoBase64(photoBase64);

        return capture;
    }

    private static boolean parseBoolean(Object... candidates) {
        for (Object item : candidates) {
            if (item == null) continue;
            if (item instanceof Boolean) return (Boolean) item;
            String str = item.toString().trim().toLowerCase();
            if ("true".equals(str) || "1".equals(str) || "yes".equals(str) || "y".equals(str)
                    || "pass".equals(str) || "allow".equals(str) || "允许".equals(str) || "允许通行".equals(str)) {
                return true;
            }
        }
        return false;
    }
}
