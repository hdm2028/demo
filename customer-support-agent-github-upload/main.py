import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
ORDERS = json.loads((BASE_DIR / "data_orders.json").read_text(encoding="utf-8"))

app = FastAPI(title="智能售后 Agent Portfolio Demo")

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    use_llm: bool = False
    stream_tokens: bool = True


def find_order(text: str):
    m = re.search(r"(?:订单|order)\s*[#号:]?\s*(\d{4,})", text, re.I)
    if not m:
        return None
    oid = m.group(1)
    for item in ORDERS if isinstance(ORDERS, list) else ORDERS.values():
        if str(item.get("order_id")) == oid:
            return item
    return {"order_id": oid, "order_status": "未找到", "shipping_status": "未知"}


def route_for(text: str, order: dict | None):
    t = text.lower()
    if any(k in t for k in ["退款", "退钱", "refund"]):
        intent, agent = "refund_request", "After Sales Agent"
    elif any(k in t for k in ["物流", "快递", "配送", "没更新", "运输"]):
        intent, agent = "logistics_exception", "After Sales Agent"
    elif any(k in t for k in ["坏了", "维修", "保修", "维修"]):
        intent, agent = "repair_policy", "Customer Agent"
    elif any(k in t for k in ["发票", "电子发票"]):
        intent, agent = "invoice_policy", "Customer Agent"
    else:
        intent, agent = "general_support", "Customer Agent"

    risk = "low"
    need_risk = False
    manual = False
    if "不要审核" in t or "直接退款" in t or "异常" in t and "退款" in t:
        risk, need_risk, manual = "high", True, True

    return {
        "intent": intent,
        "confidence": 0.96,
        "order_id": order.get("order_id") if order else None,
        "need_order": bool(order),
        "need_policy": intent in {"repair_policy", "invoice_policy", "logistics_exception", "refund_request"},
        "need_refund_request": intent == "refund_request",
        "need_risk_check": need_risk,
        "need_ticket": intent in {"logistics_exception", "repair_policy"},
        "manual_review_required": manual,
        "handoff_required": manual,
        "risk_level": risk,
        "agent_plan": [agent],
        "tool_plan": ((["order_lookup"] if order else [])
                      + (["policy_search"] if intent in {"repair_policy", "invoice_policy", "logistics_exception", "refund_request"} else [])
                      + (["risk_check"] if need_risk else [])),
    }


def execute(text: str, cid: str):
    started = time.perf_counter()
    order = find_order(text)
    route = route_for(text, order)
    tools = []
    timings = {}

    if order:
        tools.append({"tool_name": "order_lookup", "success": True, "result": {
            "order_id": order.get("order_id"),
            "order_status": order.get("order_status", "运输中"),
            "shipping_status": order.get("shipping_status", "暂无更新"),
        }})
        timings["tool.order_lookup"] = {"step": "tool.order_lookup", "duration_ms": 18}

    intent = route["intent"]
    if route["need_policy"]:
        source = {
            "logistics_exception": "物流规则：超过 48 小时无轨迹更新可创建物流异常工单。",
            "repair_policy": "售后 FAQ：符合保修条件的设备可提交维修工单。",
            "invoice_policy": "发票政策：订单完成后可申请电子发票。",
            "refund_request": "退款政策：退款申请按订单状态与风险等级进入相应流程。",
        }.get(intent, "售后政策知识库")
        tools.append({"tool_name": "policy_search", "success": True, "result": [{"source": "Demo Knowledge Base", "citation": source}]})
        timings["tool.policy_search"] = {"step": "tool.policy_search", "duration_ms": 24}

    if route["need_risk_check"]:
        tools.append({"tool_name": "risk_check", "success": True, "result": {
            "risk_level": "high", "risk_flags": ["用户要求跳过审核", "退款流程需人工确认"],
            "review_reason": "高风险退款请求",
        }})
        timings["tool.risk_check"] = {"step": "tool.risk_check", "duration_ms": 21}

    if intent == "refund_request" and order and not route["need_risk_check"]:
        rid = "RF-DEMO-" + str(order["order_id"])
        tools.append({"tool_name": "refund_apply", "success": True, "result": {
            "refund_id": rid, "status": "待处理", "mq_message_id": "MQ-DEMO-" + str(order["order_id"])
        }})
        timings["tool.refund_apply"] = {"step": "tool.refund_apply", "duration_ms": 29}

    if intent in {"logistics_exception", "repair_policy"}:
        tid = "TK-DEMO-" + str(order["order_id"] if order else "001")
        tools.append({"tool_name": "create_ticket", "success": True, "result": {
            "ticket_id": tid, "issue_type": intent, "status": "pending_human"
        }})
        timings["tool.create_ticket"] = {"step": "tool.create_ticket", "duration_ms": 26}

    if route["manual_review_required"]:
        rid = "RV-DEMO-" + str(order["order_id"] if order else "001")
        tools.append({"tool_name": "create_manual_review", "success": True, "result": {
            "review_id": rid, "risk_level": "high", "status": "pending"
        }})
        timings["tool.create_manual_review"] = {"step": "tool.create_manual_review", "duration_ms": 23}

    if intent == "logistics_exception":
        oid = order.get("order_id") if order else "该订单"
        reply = f"我已查询订单 {oid}。当前物流状态为“{order.get('shipping_status', '暂无更新')}”。根据售后知识库规则，系统已创建物流异常工单，后续由人工跟进。"
    elif intent == "refund_request" and route["manual_review_required"]:
        reply = "这笔退款请求涉及高风险操作，系统不会跳过人工审核。已创建审核单，请由人工确认后继续处理。"
    elif intent == "refund_request":
        oid = order.get("order_id") if order else "该订单"
        reply = f"已查询订单 {oid}，并按照退款政策创建退款申请。退款任务已进入 Demo MQ 队列，状态为待处理。"
    elif intent == "repair_policy":
        reply = "根据售后知识库，设备故障可先核验保修条件。系统已创建维修工单，人工客服可以继续跟进。"
    elif intent == "invoice_policy":
        reply = "根据发票政策，订单完成后可以申请电子发票。当前 Demo 已完成知识库检索，可继续按订单信息处理。"
    else:
        reply = "我是智能售后 Agent。可以帮你查询订单、处理物流异常、退款、维修、发票以及高风险人工审核。"

    timings["node.orchestrator"] = {"step": "node.orchestrator", "duration_ms": 12}
    timings["node.agent"] = {"step": "node.agent", "duration_ms": max(15, int((time.perf_counter() - started) * 1000))}
    return {
        "reply": reply,
        "conversation_id": cid,
        "route": route,
        "tool_results": tools,
        "timings": timings,
        "duration_ms": max(1, int((time.perf_counter() - started) * 1000)),
        "token_usage": {"prompt_tokens_estimated": len(text) * 2, "completion_tokens_estimated": len(reply) * 2, "total_tokens_estimated": (len(text) + len(reply)) * 2},
    }


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "web" / "index.html")

@app.get("/live")
def live():
    return {"success": True}

@app.get("/health")
@app.get("/ready")
def health():
    return {"success": True, "demo_mode": DEMO_MODE, "database_backend": "sqlite-demo", "mq_backend": "database-demo", "rag_retrieval_mode": "local-hybrid-demo", "redis_enabled": False, "cache": {"backend": "memory"}}

@app.get("/demo-config")
def demo_config():
    return {"demo_mode": DEMO_MODE, "llm_available": False, "title": "作品集 Demo"}

@app.get("/me")
def me():
    return {"user_id": "portfolio-demo", "role": "customer"}

@app.get("/info")
def info():
    return {"app_name": "智能售后 Agent Portfolio Demo", "default_model": "Deterministic Demo Router", "models": ["Deterministic Demo Router"], "rag_embedding_provider": "local", "has_llm_key": False, "agents": [
        {"key": "agent_orchestrator", "description": "负责请求路由与多 Agent 协调"},
        {"key": "customer_agent", "description": "客服问答与知识库检索"},
        {"key": "after_sales_agent", "description": "订单、退款、工单流程"},
        {"key": "risk_agent", "description": "高风险请求与人工审核"},
    ]}

@app.post("/agent/chat")
def chat(req: ChatRequest):
    cid = req.conversation_id or "conv-" + uuid.uuid4().hex[:12]
    return execute(req.message, cid)

@app.post("/agent/stream")
def stream(req: ChatRequest):
    cid = req.conversation_id or "conv-" + uuid.uuid4().hex[:12]
    result = execute(req.message, cid)
    def events():
        yield f"data: {json.dumps({'type':'route','content':result['route'],'conversation_id':cid}, ensure_ascii=False)}\n\n"
        for tool in result["tool_results"]:
            yield f"data: {json.dumps({'type':'tool_result','content':tool,'conversation_id':cid}, ensure_ascii=False)}\n\n"
        for timing in result["timings"].values():
            yield f"data: {json.dumps({'type':'timing','content':timing,'conversation_id':cid}, ensure_ascii=False)}\n\n"
        # token-like chunks make the UI feel like a real streaming Agent without an external LLM.
        reply = result["reply"]
        for i in range(0, len(reply), 12):
            yield f"data: {json.dumps({'type':'token','content':reply[i:i+12],'conversation_id':cid}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type':'done','content':result,'conversation_id':cid}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")

@app.post("/feedback")
def feedback():
    return {"success": True}

@app.get("/refunds")
def refunds(limit: int = Query(20, ge=1, le=100)):
    return {"success": True, "data": []}

@app.get("/tickets")
def tickets(limit: int = Query(20, ge=1, le=100), status: str = "all", cursor: str | None = None):
    return {"success": True, "data": [], "next_cursor": None}

@app.get("/manual-reviews")
def reviews(limit: int = 20):
    return {"success": True, "data": []}

@app.get("/mq/messages")
def mq_messages(limit: int = 20):
    return {"success": True, "data": []}

@app.get("/observability/metrics")
def metrics(limit: int = 20):
    return {"success": True, "data": []}

@app.get("/admin/operators")
def operators():
    return {"success": True, "data": []}

@app.get("/tickets/{ticket_id}")
def ticket(ticket_id: str):
    return {"success": True, "data": {"ticket_id": ticket_id, "status": "pending_human", "demo": True}}

@app.get("/refunds/{refund_id}")
def refund(refund_id: str):
    return {"success": True, "data": {"refund_id": refund_id, "status": "待处理", "demo": True}}

@app.get("/manual-reviews/{review_id}")
def review(review_id: str):
    return {"success": True, "data": {"review_id": review_id, "status": "pending", "demo": True}}

@app.post("/review-supplements")
def review_supplement():
    return {"success": True, "demo": True}

@app.post("/refunds/{refund_id}/cancel")
def cancel_refund(refund_id: str):
    return {"success": True, "data": {"refund_id": refund_id, "status": "cancelled"}}

@app.post("/admin/tickets/{ticket_id}/{action}")
def ticket_action(ticket_id: str, action: str):
    return {"success": True, "data": {"ticket_id": ticket_id, "action": action, "demo": True}}

@app.post("/manual-reviews/{review_id}/resolve")
def resolve_review(review_id: str):
    return {"success": True, "data": {"review_id": review_id, "status": "resolved"}}
