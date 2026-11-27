package com.ruoyi.autotest.websocket;

import java.io.IOException;
import java.util.concurrent.CopyOnWriteArraySet;
import javax.websocket.*;
import javax.websocket.server.ServerEndpoint;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;

/**
 * 前端实时测试监控 WebSocket 终结点
 */
@ServerEndpoint("/ws/autotest")
@Component
public class AutoTestWebSocketServer {

    private static final Logger log = LoggerFactory.getLogger(AutoTestWebSocketServer.class);

    private static final CopyOnWriteArraySet<AutoTestWebSocketServer> webSocketSet = new CopyOnWriteArraySet<>();

    private Session session;

    @OnOpen
    public void onOpen(Session session) {
        this.session = session;
        webSocketSet.add(this);
        log.info("【WebSocket】新前端客户端连接，当前在线数：{}", webSocketSet.size());
        
        // 发送欢迎与连接成功事件
        JSONObject msg = new JSONObject();
        msg.put("type", "connected");
        msg.put("message", "测试监控通道连接成功");
        sendMessage(msg.toJSONString());
    }

    @OnClose
    public void onClose() {
        webSocketSet.remove(this);
        log.info("【WebSocket】前端客户端断开连接，当前在线数：{}", webSocketSet.size());
    }

    @OnMessage
    public void onMessage(String message, Session session) {
        // 前端心跳响应
    }

    @OnError
    public void onError(Session session, Throwable error) {
        log.error("【WebSocket】发生错误: {}", error.getMessage());
    }

    public void sendMessage(String message) {
        try {
            if (this.session != null && this.session.isOpen()) {
                this.session.getBasicRemote().sendText(message);
            }
        } catch (IOException e) {
            log.error("【WebSocket】发送消息失败: {}", e.getMessage());
        }
    }

    /**
     * 群发消息
     */
    public static void broadcast(String type, Object data) {
        JSONObject payload = new JSONObject();
        payload.put("type", type);
        payload.put("data", data);
        payload.put("timestamp", System.currentTimeMillis());
        String text = payload.toJSONString();

        for (AutoTestWebSocketServer item : webSocketSet) {
            try {
                item.sendMessage(text);
            } catch (Exception ignored) {
            }
        }
    }
}
