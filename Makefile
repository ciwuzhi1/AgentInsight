# AgentInsight V3.0 常用命令；Windows 无 make 时手动执行等价命令
.PHONY: dev up up-build down logs test lint bench eval init-data init-db

dev:            ## 本机开发：后端 + 前端（两个终端）
	python -m uvicorn app.main:app --host 127.0.0.1 --port 8100   # 在 backend/ 下
	cd frontend && npm run dev

up:             ## Docker 全栈启动
	docker compose up -d

up-build:       ## Docker 全栈（含构建）
	docker compose up -d --build

down:           ## 停止并移除容器
	docker compose down

logs:           ## 跟踪全部服务日志
	docker compose logs -f

test:           ## 单元测试（离线）
	cd backend && python -m pytest tests/unit -q

lint:           ## 前端 TypeScript 类型检查
	cd frontend && npx tsc --noEmit

bench:          ## 接口延迟基准
	python scripts/api_bench.py --n 10

eval:           ## 100 case 评测
	cd backend && python -m app.evaluation.runner

init-data:      ## 生成演示数据
	python scripts/gen_data.py

init-db:        ## 初始化本机 MySQL（先填 .env 密码）
	python scripts/init_db.py
