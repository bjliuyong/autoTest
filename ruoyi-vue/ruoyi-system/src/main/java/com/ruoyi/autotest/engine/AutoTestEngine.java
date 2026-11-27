package com.ruoyi.autotest.engine;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.URI;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;

import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import com.alibaba.fastjson2.JSONObject;
import com.ruoyi.autotest.domain.*;
import com.ruoyi.autotest.service.*;
import com.ruoyi.autotest.websocket.AutoTestWebSocketServer;

/**
 * 自动化测试核心调度引擎
 * 负责：批次管理、双WebSocket接入、日志缓存与判定、持久化入库、前端实时广播
 */
@Component
public class AutoTestEngine {

    private static final Logger log = LoggerFactory.getLogger(AutoTestEngine.class);

    @Autowired
    private IAutoTestBatchService batchService;

    @Autowired
    private IAutoTestCaseService caseService;

    @Autowired
    private IAutoTestConfigService configService;

    private final AtomicBoolean isRunning = new AtomicBoolean(false);
    private AutoTestBatch currentBatch;
    private AutoTestConfig currentConfig;
    private LogStore logStore;
    private JudgeEngine judgeEngine;

    private WebSocketClient gateClient;
    private WebSocketClient logClient;

    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(4);
    private final ExecutorService caseWorkerPool = Executors.newFixedThreadPool(8);

    @PostConstruct
    public void init() {
        log.info("【AutoTestEngine】自动化测试引擎初始化完成");
    }

    @PreDestroy
    public void destroy() {
        stopTest();
        scheduler.shutdownNow();
        caseWorkerPool.shutdownNow();
    }

    public synchronized boolean isRunning() {
        return isRunning.get();
    }

    public synchronized AutoTestBatch getCurrentBatch() {
        return currentBatch;
    }

    /**
     * 启动测试批次
     */
    public synchronized String startTest(Long configId, String batchName) {
        if (isRunning.get()) {
            return "测试已在运行中，请勿重复启动";
        }

        // 1. 加载配置
        if (configId != null) {
            currentConfig = configService.selectAutoTestConfigByConfigId(configId);
        }
        if (currentConfig == null) {
            currentConfig = configService.selectDefaultConfig();
        }

        // 2. 初始化引擎组件
        logStore = new LogStore(
                currentConfig.getSessionCreatePattern(),
                currentConfig.getNodeFailurePattern(),
                currentConfig.getPassengerExitPattern()
        );
        judgeEngine = new JudgeEngine(currentConfig.getNodeAliases());

        // 3. 创建批次记录并持久化到数据库
        String batchNo = "BATCH_" + System.currentTimeMillis();
        currentBatch = new AutoTestBatch();
        currentBatch.setBatchNo(batchNo);
        currentBatch.setBatchName(batchName != null && !batchName.trim().isEmpty() ? batchName : "闸机自动化测试批次-" + batchNo);
        currentBatch.setStatus("RUNNING");
        currentBatch.setGateWsUrl(currentConfig.getGateWsUrl());
        currentBatch.setLogWsUrl(currentConfig.getLogWsUrl());
        currentBatch.setTotalCases(0);
        currentBatch.setPassCases(0);
        currentBatch.setFailCases(0);
        currentBatch.setTimeoutCases(0);
        currentBatch.setPassRate(BigDecimal.ZERO);
        currentBatch.setStartTime(new Date());

        batchService.insertAutoTestBatch(currentBatch);
        isRunning.set(true);

        // 4. 连接闸机与日志 WebSocket 通道
        connectGateChannel(currentConfig.getGateWsUrl());
        connectLogChannel(currentConfig.getLogWsUrl());

        // 5. 广播状态给前端
        broadcastEngineStatus("RUNNING", "测试任务已启动，等待用例与日志接入...");
        return "启动成功，批次号：" + batchNo;
    }

    /**
     * 停止测试批次
     */
    public synchronized String stopTest() {
        if (!isRunning.get()) {
            return "当前无正在运行的测试任务";
        }

        isRunning.set(false);
        closeWs(gateClient);
        closeWs(logClient);

        if (currentBatch != null) {
            currentBatch.setStatus("COMPLETED");
            currentBatch.setEndTime(new Date());
            if (currentBatch.getStartTime() != null) {
                long duration = (currentBatch.getEndTime().getTime() - currentBatch.getStartTime().getTime()) / 1000;
                currentBatch.setDurationSeconds(duration);
            }
            batchService.updateAutoTestBatch(currentBatch);
        }

        broadcastEngineStatus("COMPLETED", "测试任务已结束");
        return "测试已停止";
    }

    /**
     * 连接闸机通道
     */
    private void connectGateChannel(String wsUrl) {
        if (wsUrl == null || !wsUrl.startsWith("ws")) return;
        try {
            gateClient = new WebSocketClient(new URI(wsUrl)) {
                @Override
                public void onOpen(ServerHandshake handshakedata) {
                    log.info("【闸机通道】已连接: {}", wsUrl);
                    AutoTestWebSocketServer.broadcast("log_line", "[系统通知] 闸机通道 WebSocket 连接成功: " + wsUrl);
                }

                @Override
                public void onMessage(String message) {
                    handleGateMessage(message);
                }

                @Override
                public void onClose(int code, String reason, boolean remote) {
                    log.warn("【闸机通道】已断开: {}", reason);
                }

                @Override
                public void onError(Exception ex) {
                    log.error("【闸机通道】连接错误: {}", ex.getMessage());
                }
            };
            gateClient.connect();
        } catch (Exception e) {
            log.error("【闸机通道】连接异常: {}", e.getMessage());
        }
    }

    /**
     * 连接待测系统日志通道
     */
    private void connectLogChannel(String wsUrl) {
        if (wsUrl == null || !wsUrl.startsWith("ws")) return;
        try {
            logClient = new WebSocketClient(new URI(wsUrl)) {
                @Override
                public void onOpen(ServerHandshake handshakedata) {
                    log.info("【待测日志通道】已连接: {}", wsUrl);
                    AutoTestWebSocketServer.broadcast("log_line", "[系统通知] 待测日志通道 WebSocket 连接成功: " + wsUrl);
                }

                @Override
                public void onMessage(String message) {
                    handleLogMessage(message);
                }

                @Override
                public void onClose(int code, String reason, boolean remote) {
                    log.warn("【待测日志通道】已断开: {}", reason);
                }

                @Override
                public void onError(Exception ex) {
                    log.error("【待测日志通道】连接错误: {}", ex.getMessage());
                }
            };
            logClient.connect();
        } catch (Exception e) {
            log.error("【待测日志通道】连接异常: {}", e.getMessage());
        }
    }

    /**
     * 处理待测系统日志行
     */
    public void handleLogMessage(String rawLine) {
        if (rawLine == null || rawLine.trim().isEmpty()) return;

        // 如果是 JSON 格式如 {"type":"log","line":"..."}
        String line = rawLine;
        if (rawLine.startsWith("{") && rawLine.endsWith("}")) {
            try {
                JSONObject json = JSONObject.parseObject(rawLine);
                if (json.containsKey("line")) {
                    line = json.getString("line");
                }
            } catch (Exception ignored) {
            }
        }

        if (logStore != null) {
            logStore.appendLine(line);
        }

        // 广播实时日志给前端
        AutoTestWebSocketServer.broadcast("log_line", line);
    }

    /**
     * 处理闸机采集用例报文
     */
    public void handleGateMessage(String message) {
        GateCapture capture = GateMessageParser.parse(message);
        if (capture == null) return;

        // 1. 发送 ACK 回执给闸机端
        sendAck(capture.getCaseId());

        // 2. 异步进入用例判定与持久化流程
        caseWorkerPool.submit(() -> processCase(capture));
    }

    private void sendAck(String caseId) {
        if (gateClient != null && gateClient.isOpen()) {
            JSONObject ack = new JSONObject();
            ack.put("type", "gate.ack");
            ack.put("case_id", caseId);
            ack.put("status", "accepted");
            ack.put("reason", "");
            try {
                gateClient.send(ack.toJSONString());
            } catch (Exception ignored) {
            }
        }
    }

    /**
     * 用例处理核心：等待日志结论 -> 综合比对判定 -> 持久化数据库 -> 前端实时推送
     */
    public void processCase(GateCapture capture) {
        int timeoutSec = currentConfig != null && currentConfig.getTimeoutSeconds() != null
                ? currentConfig.getTimeoutSeconds() : 15;
        long startTime = capture.getReceivedTimeMillis();
        long deadline = startTime + timeoutSec * 1000L;

        List<String> candidateKeys = Arrays.asList(capture.getCertNo(), capture.getPassengerName());
        LogStore.CaseResolution resolution = null;

        while (System.currentTimeMillis() < deadline) {
            if (logStore != null) {
                resolution = logStore.resolve(candidateKeys);
                if (resolution.settled) {
                    break;
                }
            }
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }

        boolean timedOut = (resolution == null || !resolution.settled);
        long finishTime = System.currentTimeMillis();
        long durationMs = finishTime - startTime;

        Boolean actualAllow = resolution != null ? resolution.allowPass : null;
        String actualNode = resolution != null ? resolution.failedNode : null;
        String sid = resolution != null ? resolution.sid : null;
        String slice = resolution != null ? resolution.logSlice : "";
        boolean contradictory = resolution != null && resolution.contradictory;
        String note = resolution != null ? resolution.note : "超时未捕获到日志结论";

        // 执行严格判定
        Verdict verdict = judgeEngine.evaluate(
                capture.isExpectedAllowPass(),
                capture.getExpectedFailedNode(),
                actualAllow,
                actualNode,
                sid,
                slice,
                timedOut,
                contradictory,
                note
        );

        // 1. 构建用例实体并落库持久化 (Requirement 3: 每次测试都可以追溯，数据记录数据库)
        AutoTestCase caseRecord = new AutoTestCase();
        if (currentBatch != null) {
            caseRecord.setBatchId(currentBatch.getBatchId());
            caseRecord.setBatchNo(currentBatch.getBatchNo());
        } else {
            caseRecord.setBatchId(0L);
            caseRecord.setBatchNo("MOCK");
        }
        caseRecord.setCaseId(capture.getCaseId());
        caseRecord.setSerial(capture.getSerial());
        caseRecord.setGateId(capture.getGateId());
        caseRecord.setPassengerName(capture.getPassengerName());
        caseRecord.setCertType(capture.getCertType());
        caseRecord.setCertNo(capture.getCertNo());
        caseRecord.setPhotoUrl(capture.getPhotoBase64());
        caseRecord.setSessionId(sid != null ? sid : "");
        caseRecord.setExpectedAllowPass(capture.isExpectedAllowPass() ? 1 : 0);
        caseRecord.setExpectedFailedNode(capture.getExpectedFailedNode());
        caseRecord.setActualAllowPass(actualAllow != null ? (actualAllow ? 1 : 0) : null);
        caseRecord.setActualFailedNode(actualNode);
        caseRecord.setVerdict(verdict.getVerdictCode());
        caseRecord.setReasons(verdict.getReasonsAsString());
        caseRecord.setRawCapture(capture.getRawJson());
        caseRecord.setLogSlice(slice);
        caseRecord.setDurationMs(durationMs);
        caseRecord.setReceivedTime(new Date(startTime));
        caseRecord.setFinishTime(new Date(finishTime));

        caseService.insertAutoTestCase(caseRecord);

        // 2. 统计更新批次汇总指标
        if (currentBatch != null) {
            synchronized (currentBatch) {
                int total = currentBatch.getTotalCases() + 1;
                int pass = currentBatch.getPassCases();
                int fail = currentBatch.getFailCases();
                int timeout = currentBatch.getTimeoutCases();

                if ("PASS".equals(verdict.getVerdictCode())) {
                    pass++;
                } else if ("TIMEOUT".equals(verdict.getVerdictCode())) {
                    fail++;
                    timeout++;
                } else {
                    fail++;
                }

                BigDecimal rate = BigDecimal.valueOf((double) pass * 100 / total)
                        .setScale(2, RoundingMode.HALF_UP);

                currentBatch.setTotalCases(total);
                currentBatch.setPassCases(pass);
                currentBatch.setFailCases(fail);
                currentBatch.setTimeoutCases(timeout);
                currentBatch.setPassRate(rate);

                batchService.updateAutoTestBatch(currentBatch);
            }
        }

        // 3. 实时广播用例判定与批次最新大盘指标
        AutoTestWebSocketServer.broadcast("case_result", caseRecord);
        AutoTestWebSocketServer.broadcast("batch_update", currentBatch);
    }

    /**
     * 运行自测模拟器（支持网页端一键执行 demo/selftest，无外部闸机设备也能即刻验证全部功能）
     */
    public void runSimulation(int count) {
        if (!isRunning.get()) {
            startTest(null, "内置模拟自测批次 (Mock Simulator)");
        }

        caseWorkerPool.submit(() -> {
            try {
                String[][] mockCases = new String[][]{
                        {"C-000101", "张伟", "110101199003071234", "true", "", "人证核验通过，旅客正常放行"},
                        {"C-000102", "李娜", "110101199205122345", "false", "证件核验", "证件信息已失效或过期，禁止通行"},
                        {"C-000103", "王强", "110101198808203456", "false", "人脸比对", "现场人脸与证件照相似度低于阈值，拦截"},
                        {"C-000104", "赵敏", "110101199511114567", "true", "", "绿码核验通过，旅客放行"},
                        {"C-000105", "周杰", "110101198502145678", "false", "行程校验", "无当日对应有效客票，禁止通行"}
                };

                for (int i = 0; i < count; i++) {
                    if (!isRunning.get()) break;
                    String[] def = mockCases[i % mockCases.length];
                    String caseId = def[0] + "-" + (i + 1);
                    String name = def[1];
                    String certNo = def[2];
                    boolean expectedAllow = Boolean.parseBoolean(def[3]);
                    String expectedNode = def[4];
                    String sid = "S" + String.format("%04d", (i + 1));

                    // 1. 发送闸机采集报文
                    JSONObject captureJson = new JSONObject();
                    captureJson.put("type", "gate.capture");
                    captureJson.put("case_id", caseId);
                    captureJson.put("gate_id", "G01");
                    JSONObject exp = new JSONObject();
                    exp.put("allow_pass", expectedAllow);
                    exp.put("failed_node", expectedNode);
                    captureJson.put("expected", exp);
                    JSONObject cap = new JSONObject();
                    cap.put("passenger_name", name);
                    cap.put("cert_type", "ID_CARD");
                    cap.put("cert_no", certNo);
                    captureJson.put("capture", cap);

                    // 模拟闸机推送用例
                    handleGateMessage(captureJson.toJSONString());

                    // 2. 模拟系统日志流实时推送到日志通道
                    Thread.sleep(200);
                    handleLogMessage("[流程引擎] 创建新会话: [" + sid + "]");
                    Thread.sleep(150);
                    handleLogMessage(String.format("会话ID[%s] - 旅客[%s] 刷证进入通道，证件号: %s", sid, name, certNo));
                    Thread.sleep(200);

                    if (expectedAllow) {
                        handleLogMessage(String.format("会话ID[%s] - 旅客[%s] 证件核验通过", sid, name));
                        Thread.sleep(150);
                        handleLogMessage(String.format("会话ID[%s] - 旅客[%s] 人脸比对通过(相似度92%%)", sid, name));
                        Thread.sleep(150);
                        handleLogMessage(String.format("会话ID[%s] - 旅客[%s] 行程核验通过", sid, name));
                        Thread.sleep(200);
                        handleLogMessage(String.format("会话ID[%s] - 旅客[%s] 离开通道", sid, name));
                    } else {
                        handleLogMessage(String.format("会话ID[%s] - 节点 %s 执行失败: 校验未通过", sid, expectedNode));
                    }

                    Thread.sleep(600);
                }
            } catch (Exception e) {
                log.error("自测模拟运行异常: {}", e.getMessage());
            }
        });
    }

    private void broadcastEngineStatus(String status, String message) {
        JSONObject data = new JSONObject();
        data.put("status", status);
        data.put("message", message);
        data.put("batch", currentBatch);
        AutoTestWebSocketServer.broadcast("status_change", data);
    }

    private void closeWs(WebSocketClient client) {
        if (client != null) {
            try {
                client.close();
            } catch (Exception ignored) {
            }
        }
    }
}
