# AgentInsight 常用命令（P6）；Windows 无 make 时手动执行等价命令
.PHONY: dev up up-build down test bench eval init-data init-db

dev:            ## 本机开发：后端 + 前端（两个终端）
	python -m uvicorn app.main:app --host 127.0.0.1 --port 8100   # 在 backend/ 下
	cd frontend && npm run dev

up:             ## Docker 全栈（含构建）
	docker compose up -d --build

up-build: up

down:
	docker compose down

test:           ## 单元测试（离线）
	cd backend && python -m pytest tests/unit -q

bench:          ## 接口延迟基准
	python scripts/api_bench.py --n 10

eval:           ## 100 case 评测
	cd backend && python -m app.evaluation.runner

init-data:      ## 生成演示数据
	python scripts/gen_data.py

init-db:        ## 初始化本机 MySQL（先填 .env 密码）
	python scripts/init_db.py
