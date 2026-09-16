# AgentInsight V3.0 — 后端镜像：FastAPI + Agent Runtime（DuckDB 单引擎）
FROM python:3.12-slim

WORKDIR /app

# 国内镜像源加速（可被 build-arg 覆盖）
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=$PIP_INDEX PIP_DISABLE_PIP_VERSION_CHECK=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
