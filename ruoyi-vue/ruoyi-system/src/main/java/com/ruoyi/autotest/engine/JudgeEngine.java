package com.ruoyi.autotest.engine;

import java.text.Normalizer;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;
import com.alibaba.fastjson2.JSON;
import com.ruoyi.autotest.domain.Verdict;

/**
 * 判定引擎：严格按照现存 Python 判定逻辑实现
 * 比对「预期结果」和「待测系统日志推断的实际结果」
 * 只有「是否允许通行」与「失败节点名称」都一致才算用例通过。
 */
public class JudgeEngine {

    private static final Pattern PUNCT_PATTERN = Pattern.compile("[\\s\\[\\]【】()（）<>《》〈〉:：,，。.、/\\\\|_\\-—－–~`'\"“”‘’!！?？;；]+");

    private final Map<String, String> nodeAliases = new HashMap<>();

    public JudgeEngine(String aliasesJson) {
        if (aliasesJson != null && !aliasesJson.trim().isEmpty()) {
            try {
                Map<String, Object> map = JSON.parseObject(aliasesJson);
                for (Map.Entry<String, Object> entry : map.entrySet()) {
                    if (entry.getValue() != null) {
                        nodeAliases.put(normalizeToken(entry.getKey()), normalizeToken(entry.getValue().toString()));
                    }
                }
            } catch (Exception ignored) {
            }
        }
    }

    /**
     * 节点名归一化：全角转半角、去空白与标点、小写，再套别名表
     */
    public String normalizeToken(String text) {
        if (text == null || text.trim().isEmpty()) {
            return "";
        }
        String normalized = Normalizer.normalize(text.trim(), Normalizer.Form.NFKC).toLowerCase();
        return PUNCT_PATTERN.matcher(normalized).replaceAll("");
    }

    public String resolveAlias(String node) {
        String token = normalizeToken(node);
        return nodeAliases.getOrDefault(token, token);
    }

    /**
     * 综合判定
     */
    public Verdict evaluate(boolean expectedAllow, String expectedNode,
                            Boolean actualAllow, String actualNode,
                            String sessionId, String logSlice,
                            boolean timedOut, boolean contradictory, String resolutionNote) {
        Verdict verdict = new Verdict();
        verdict.setExpectedAllow(expectedAllow);
        verdict.setExpectedNode(expectedNode);
        verdict.setActualAllow(actualAllow);
        verdict.setActualNode(actualNode);
        verdict.setSessionId(sessionId);
        verdict.setLogSlice(logSlice);
        verdict.setTimedOut(timedOut);

        // 1. 超时且未拿到结论
        if (actualAllow == null) {
            verdict.setPassed(false);
            verdict.setVerdictCode("TIMEOUT");
            if (timedOut) {
                verdict.addReason("超时未在待测系统日志中检测到「执行失败」或「离开通道」记录（未判定实际结果，按不通过处理）");
            } else {
                verdict.addReason("尚未从日志中判定出实际结果：" + resolutionNote);
            }
            return verdict;
        }

        // 2. 自相矛盾（既有节点失败，又有离开通道）
        if (contradictory) {
            verdict.addReason("警告：同一会话内既出现「节点执行失败」又出现「旅客离开通道」，需人工核验");
        }

        // 3. 通行结论一致性比对
        String expectedAllowText = expectedAllow ? "允许通行" : "不允许通行";
        String actualAllowText = actualAllow ? "允许通行" : "不允许通行";

        if (expectedAllow != actualAllow) {
            verdict.setPassed(false);
            verdict.setVerdictCode("FAIL");
            verdict.addReason(String.format("通行结论不一致：预期「%s」，实际「%s」", expectedAllowText, actualAllowText));
            return verdict;
        }

        // 4. 不允许通行时，比对失败节点名称
        if (!expectedAllow) {
            String normExpectedNode = resolveAlias(expectedNode);
            String normActualNode = resolveAlias(actualNode);

            if (!normExpectedNode.isEmpty() && !normActualNode.isEmpty() && !normExpectedNode.equals(normActualNode)) {
                verdict.setPassed(false);
                verdict.setVerdictCode("FAIL");
                verdict.addReason(String.format("拦截节点不一致：预期在「%s」拦截，实际在「%s」拦截",
                        expectedNode != null ? expectedNode : "未指定",
                        actualNode != null ? actualNode : "未识别"));
                return verdict;
            }
        }

        // 5. 校验全部通过
        verdict.setPassed(true);
        verdict.setVerdictCode("PASS");
        verdict.addReason(String.format("判定通过：预期「%s」与实际「%s」完全一致", expectedAllowText, actualAllowText));
        if (!expectedAllow && expectedNode != null && !expectedNode.isEmpty()) {
            verdict.addReason(String.format("失败节点「%s」核验一致", expectedNode));
        }

        return verdict;
    }
}
