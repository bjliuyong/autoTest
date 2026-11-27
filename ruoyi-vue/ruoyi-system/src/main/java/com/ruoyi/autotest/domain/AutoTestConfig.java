package com.ruoyi.autotest.domain;

import com.ruoyi.common.annotation.Excel;
import com.ruoyi.common.core.domain.BaseEntity;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

/**
 * 自动化测试参数配置表 auto_test_config
 */
public class AutoTestConfig extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 配置主键 */
    private Long configId;

    /** 配置方案名称 */
    @Excel(name = "配置方案名称")
    private String configName;

    /** 闸机WS地址 */
    @Excel(name = "闸机WS地址")
    private String gateWsUrl;

    /** 日志WS地址 */
    @Excel(name = "日志WS地址")
    private String logWsUrl;

    /** 是否作为闸机服务端监听(1:是, 0:否) */
    @Excel(name = "闸机服务模式", readConverterExp = "1=服务端监听,0=客户端连接")
    private Integer isGateServer;

    /** 作为服务端时的监听端口 */
    @Excel(name = "服务端监听端口")
    private Integer gateServerPort;

    /** 用例日志等待超时时间(秒) */
    @Excel(name = "超时时间(秒)")
    private Integer timeoutSeconds;

    /** 会话创建正则 */
    private String sessionCreatePattern;

    /** 节点失败正则 */
    private String nodeFailurePattern;

    /** 旅客放行/离开正则 */
    private String passengerExitPattern;

    /** 节点别名映射(JSON) */
    private String nodeAliases;

    /** 是否默认配置(1:是, 0:否) */
    @Excel(name = "默认配置", readConverterExp = "1=是,0=否")
    private Integer isDefault;

    public void setConfigId(Long configId) 
    {
        this.configId = configId;
    }

    public Long getConfigId() 
    {
        return configId;
    }

    public void setConfigName(String configName) 
    {
        this.configName = configName;
    }

    public String getConfigName() 
    {
        return configName;
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

    public void setIsGateServer(Integer isGateServer) 
    {
        this.isGateServer = isGateServer;
    }

    public Integer getIsGateServer() 
    {
        return isGateServer;
    }

    public void setGateServerPort(Integer gateServerPort) 
    {
        this.gateServerPort = gateServerPort;
    }

    public Integer getGateServerPort() 
    {
        return gateServerPort;
    }

    public void setTimeoutSeconds(Integer timeoutSeconds) 
    {
        this.timeoutSeconds = timeoutSeconds;
    }

    public Integer getTimeoutSeconds() 
    {
        return timeoutSeconds;
    }

    public void setSessionCreatePattern(String sessionCreatePattern) 
    {
        this.sessionCreatePattern = sessionCreatePattern;
    }

    public String getSessionCreatePattern() 
    {
        return sessionCreatePattern;
    }

    public void setNodeFailurePattern(String nodeFailurePattern) 
    {
        this.nodeFailurePattern = nodeFailurePattern;
    }

    public String getNodeFailurePattern() 
    {
        return nodeFailurePattern;
    }

    public void setPassengerExitPattern(String passengerExitPattern) 
    {
        this.passengerExitPattern = passengerExitPattern;
    }

    public String getPassengerExitPattern() 
    {
        return passengerExitPattern;
    }

    public void setNodeAliases(String nodeAliases) 
    {
        this.nodeAliases = nodeAliases;
    }

    public String getNodeAliases() 
    {
        return nodeAliases;
    }

    public void setIsDefault(Integer isDefault) 
    {
        this.isDefault = isDefault;
    }

    public Integer getIsDefault() 
    {
        return isDefault;
    }

    @Override
    public String toString() {
        return new ToStringBuilder(this,ToStringStyle.MULTI_LINE_STYLE)
            .append("configId", getConfigId())
            .append("configName", getConfigName())
            .append("gateWsUrl", getGateWsUrl())
            .append("logWsUrl", getLogWsUrl())
            .append("isGateServer", getIsGateServer())
            .append("gateServerPort", getGateServerPort())
            .append("timeoutSeconds", getTimeoutSeconds())
            .append("isDefault", getIsDefault())
            .append("createTime", getCreateTime())
            .append("updateTime", getUpdateTime())
            .toString();
    }
}
