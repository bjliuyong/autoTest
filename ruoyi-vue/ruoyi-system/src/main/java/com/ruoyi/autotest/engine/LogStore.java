package com.ruoyi.autotest.engine;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 日志仓库：缓存待测系统实时日志，识别会话、失败节点、通行事件，并抽取单旅客日志切片。
 * （对应 Python autotest/logstore.py）
 */
public class LogStore {

    public static class LogLine {
        public final int index;
        public final long timestamp;
        public final String text;

        public LogLine(int index, long timestamp, String text) {
            this.index = index;
            this.timestamp = timestamp;
            this.text = text;
        }
    }

    public static class SessionState {
        public final String sid;
        public final int createIndex;
        public final long createTime;
        public final Set<String> identities = Collections.newSetFromMap(new ConcurrentHashMap<>());
        public final List<String> failedNodes = new CopyOnWriteArrayList<>();
        public volatile boolean exitSeen = false;
        public volatile int lastIndex;

        public SessionState(String sid, int createIndex, long createTime) {
            this.sid = sid;
            this.createIndex = createIndex;
            this.createTime = createTime;
            this.lastIndex = createIndex;
        }
    }

    public static class CaseResolution {
        public boolean settled = false;
        public String sid;
        public Boolean allowPass = null;
        public String failedNode;
        public boolean contradictory = false;
        public String note = "";
        public String logSlice = "";
    }

    private final List<Pattern> sessionCreatePatterns = new ArrayList<>();
    private final List<Pattern> nodeFailurePatterns = new ArrayList<>();
    private final List<Pattern> passengerExitPatterns = new ArrayList<>();
    private static final Pattern PASSENGER_MARKER_PATTERN = Pattern.compile("旅客\\[(?<key>[^\\]]+)\\]");
    private static final Pattern SID_PATTERN = Pattern.compile("会话ID\\[(?<sid>[^\\]]+)\\]");

    private static final int MAX_BUFFER_LINES = 10000;
    private final List<LogLine> buffer = new CopyOnWriteArrayList<>();
    private final Map<String, SessionState> sessions = new ConcurrentHashMap<>();
    private final List<String> sessionOrder = new CopyOnWriteArrayList<>();
    private int nextIndex = 0;

    public LogStore(String sessionCreatePattern, String nodeFailurePattern, String passengerExitPattern) {
        initPatterns(sessionCreatePatterns, sessionCreatePattern, "\\[流程引擎\\]\\s*创建新会话[：:\\s]*\\[?(?<sid>[^\\s\\[\\]，,。;；]+)\\]?");
        initPatterns(nodeFailurePatterns, nodeFailurePattern, "会话ID\\[(?<sid>[^\\]]+)\\]\\s*[-—－–:：]\\s*节点\\s*(?<node>.+?)\\s*执行失败");
        initPatterns(passengerExitPatterns, passengerExitPattern,
                "旅客\\[(?<key>[^\\]]+)\\][^\\n]{0,16}?离开通道\n旅客\\[(?<key>[^\\]]+)\\][^\\n]{0,16}?(?:已放行|开闸成功|闸门已开)\n离开通道\n(?:已放行|开闸成功|闸门已开)");
    }

    private void initPatterns(List<Pattern> target, String patternStr, String defaultStr) {
        String effective = (patternStr != null && !patternStr.trim().isEmpty()) ? patternStr : defaultStr;
        String[] lines = effective.split("\n");
        for (String line : lines) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty()) {
                try {
                    // 兼容 Python 命名组格式 (?P<name>...) -> Java (?<name>...)
                    String javaRegex = trimmed.replace("?P<", "?<");
                    target.add(Pattern.compile(javaRegex));
                } catch (Exception ignored) {
                }
            }
        }
    }

    public synchronized void appendLine(String lineText) {
        if (lineText == null) return;
        int idx = nextIndex++;
        long now = System.currentTimeMillis();
        LogLine logLine = new LogLine(idx, now, lineText);

        if (buffer.size() >= MAX_BUFFER_LINES) {
            buffer.remove(0);
        }
        buffer.add(logLine);

        // 1. 匹配会话创建
        for (Pattern pattern : sessionCreatePatterns) {
            Matcher m = pattern.matcher(lineText);
            if (m.find()) {
                try {
                    String sid = cleanId(m.group("sid"));
                    if (!sid.isEmpty() && !sessions.containsKey(sid)) {
                        SessionState state = new SessionState(sid, idx, now);
                        sessions.put(sid, state);
                        sessionOrder.add(sid);
                    }
                } catch (Exception ignored) {}
            }
        }

        // 2. 匹配会话ID引用
        Matcher sidMatcher = SID_PATTERN.matcher(lineText);
        String currentSid = null;
        if (sidMatcher.find()) {
            currentSid = cleanId(sidMatcher.group("sid"));
        }

        // 3. 关联旅客标识
        Matcher markerMatcher = PASSENGER_MARKER_PATTERN.matcher(lineText);
        if (markerMatcher.find()) {
            String key = cleanId(markerMatcher.group("key"));
            if (currentSid != null && sessions.containsKey(currentSid)) {
                sessions.get(currentSid).identities.add(key);
                sessions.get(currentSid).lastIndex = idx;
            } else if (!sessionOrder.isEmpty()) {
                String latestSid = sessionOrder.get(sessionOrder.size() - 1);
                sessions.get(latestSid).identities.add(key);
                sessions.get(latestSid).lastIndex = idx;
            }
        }

        // 4. 匹配节点失败
        for (Pattern pattern : nodeFailurePatterns) {
            Matcher m = pattern.matcher(lineText);
            if (m.find()) {
                try {
                    String sid = cleanId(m.group("sid"));
                    String node = m.group("node").trim();
                    if (!sid.isEmpty()) {
                        SessionState state = sessions.computeIfAbsent(sid, s -> {
                            sessionOrder.add(s);
                            return new SessionState(s, idx, now);
                        });
                        state.failedNodes.add(node);
                        state.lastIndex = idx;
                    }
                } catch (Exception ignored) {}
            }
        }

        // 5. 匹配离开通道/放行
        for (Pattern pattern : passengerExitPatterns) {
            Matcher m = pattern.matcher(lineText);
            if (m.find()) {
                String key = null;
                try {
                    key = cleanId(m.group("key"));
                } catch (Exception ignored) {}

                if (key != null && !key.isEmpty()) {
                    for (SessionState state : sessions.values()) {
                        if (state.identities.contains(key)) {
                            state.exitSeen = true;
                            state.lastIndex = idx;
                        }
                    }
                } else if (currentSid != null && sessions.containsKey(currentSid)) {
                    sessions.get(currentSid).exitSeen = true;
                    sessions.get(currentSid).lastIndex = idx;
                } else if (!sessionOrder.isEmpty()) {
                    String latestSid = sessionOrder.get(sessionOrder.size() - 1);
                    sessions.get(latestSid).exitSeen = true;
                    sessions.get(latestSid).lastIndex = idx;
                }
            }
        }
    }

    public CaseResolution resolve(List<String> candidateKeys) {
        CaseResolution res = new CaseResolution();

        // 寻找匹配的会话
        SessionState targetSession = null;
        for (String sid : sessionOrder) {
            SessionState state = sessions.get(sid);
            if (state == null) continue;
            for (String key : candidateKeys) {
                if (key != null && !key.isEmpty() && state.identities.contains(key)) {
                    targetSession = state;
                    break;
                }
            }
            if (targetSession != null) break;
        }

        // 如果未通过标识匹配上，但只有一个正在运行的会话，且该会话有事件，采用兜底
        if (targetSession == null && !sessionOrder.isEmpty()) {
            SessionState latest = sessions.get(sessionOrder.get(sessionOrder.size() - 1));
            if (latest != null && (!latest.failedNodes.isEmpty() || latest.exitSeen)) {
                targetSession = latest;
            }
        }

        if (targetSession != null) {
            res.sid = targetSession.sid;
            boolean hasFailure = !targetSession.failedNodes.isEmpty();
            boolean hasExit = targetSession.exitSeen;

            if (hasFailure && hasExit) {
                res.contradictory = true;
            }

            if (hasFailure) {
                res.settled = true;
                res.allowPass = false;
                res.failedNode = targetSession.failedNodes.get(targetSession.failedNodes.size() - 1);
                res.note = "节点执行失败：" + res.failedNode;
            } else if (hasExit) {
                res.settled = true;
                res.allowPass = true;
                res.note = "旅客正常离开通道/放行";
            }

            // 提取该会话的日志切片
            res.logSlice = extractSlice(targetSession.createIndex, targetSession.lastIndex);
        } else {
            // 兜底提取最新日志切片
            int start = Math.max(0, buffer.size() - 25);
            res.logSlice = extractSlice(start, buffer.size() - 1);
        }

        return res;
    }

    private String extractSlice(int startIdx, int endIdx) {
        StringBuilder sb = new StringBuilder();
        for (LogLine line : buffer) {
            if (line.index >= startIdx && line.index <= endIdx + 5) {
                sb.append(line.text).append("\n");
            }
        }
        return sb.toString();
    }

    public static String cleanId(String val) {
        if (val == null) return "";
        String s = val.trim();
        String wrappers = "[](){}<>（）【】〔〕「」『』〈〉\"'“”‘’ \t\r\n:：,，。;；、-—_=|/\\";
        while (!s.isEmpty() && wrappers.indexOf(s.charAt(0)) >= 0) {
            s = s.substring(1);
        }
        while (!s.isEmpty() && wrappers.indexOf(s.charAt(s.length() - 1)) >= 0) {
            s = s.substring(0, s.length() - 1);
        }
        return s.trim();
    }
}
