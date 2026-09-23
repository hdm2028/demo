# 智能售后 Agent｜Portfolio Demo

一个可直接在线展示的 Multi-Agent 售后系统 Demo。

## 展示链路

Orchestrator → 客服 / 售后 / 风控 Agent → RAG → Tool Calling → 订单 / 工单 / 退款 / 人工审核。

本仓库是为作品集公网演示准备的轻量版本：默认使用确定性 Demo Router、内置合成订单数据和本地知识规则，不需要访客提供 API Key、MySQL、Redis 或 MQ。

## 快速运行

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

打开 http://localhost:8000。

## Render

仓库根目录包含 `render.yaml`，可直接作为 Render Blueprint 部署。健康检查为 `/health`。

> 说明：这是 Portfolio Demo 模式。它保留真实项目的 Agent 架构与交互链路，但外部基础设施和真实 LLM 调用在公开演示环境中使用轻量确定性实现，避免暴露密钥并降低部署依赖。
