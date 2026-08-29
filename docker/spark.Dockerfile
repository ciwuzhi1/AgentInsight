# 本地构建 Spark 3.5.1 运行镜像（替代直连 Docker Hub 的大镜像拉取）
# 组成：Temurin JRE 17 (Ubuntu 22.04) + python3 + pip 安装 pyspark（阿里云镜像源）
FROM eclipse-temurin:17-jre-jammy

RUN sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g; s|security.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple pyspark==3.5.1 \
    && python3 -c 'import pyspark,os;dst=os.path.join(os.path.dirname(pyspark.__file__),"bin","spark-submit");src="/usr/local/bin/spark-submit";os.path.exists(src) or os.symlink(dst,src)'
