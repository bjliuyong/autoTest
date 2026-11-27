# =============================================================
# Stage 1: 基于 Maven 镜像进行多阶段编译构建 (Hermetic Build)
# =============================================================
FROM maven:3.8-eclipse-temurin-8 AS builder

WORKDIR /build

# 复制 Maven 项目定义文件
COPY pom.xml .
COPY ruoyi-common/pom.xml ruoyi-common/
COPY ruoyi-framework/pom.xml ruoyi-framework/
COPY ruoyi-system/pom.xml ruoyi-system/
COPY ruoyi-quartz/pom.xml ruoyi-quartz/
COPY ruoyi-generator/pom.xml ruoyi-generator/
COPY ruoyi-admin/pom.xml ruoyi-admin/

# 复制各子模块源代码
COPY ruoyi-common/src ruoyi-common/src
COPY ruoyi-framework/src ruoyi-framework/src
COPY ruoyi-system/src ruoyi-system/src
COPY ruoyi-quartz/src ruoyi-quartz/src
COPY ruoyi-generator/src ruoyi-generator/src
COPY ruoyi-admin/src ruoyi-admin/src

# 编译打包 (跳过单元测试)
RUN mvn clean package -DskipTests -Dmaven.test.skip=true

# =============================================================
# Stage 2: 生产运行镜像 (Eclipse Temurin 8 JRE 官方维护镜像)
# =============================================================
FROM eclipse-temurin:8-jre

LABEL maintainer="ruoyi-autotest"

# 设置东八区时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 创建应用目录与上传持久化目录
RUN mkdir -p /home/ruoyi/uploadPath && \
    mkdir -p /home/ruoyi/logs

WORKDIR /home/ruoyi

# 从编译阶段获取产物包
COPY --from=builder /build/ruoyi-admin/target/ruoyi-admin.jar /home/ruoyi/ruoyi-admin.jar

# 暴露后端端口 (8080: Web API, 8765: 闸机通道监听端口)
EXPOSE 8080
EXPOSE 8765

# JVM 优化运行参数
ENV JAVA_OPTS="-Xms512m -Xmx1024m -XX:+UseG1GC -Djava.security.egd=file:/dev/./urandom"
ENV SPRING_PROFILES_ACTIVE="druid,docker"

# 启动命令
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /home/ruoyi/ruoyi-admin.jar --spring.profiles.active=$SPRING_PROFILES_ACTIVE"]
