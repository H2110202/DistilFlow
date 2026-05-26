"""
OpenAI 兼容 API 层 — 让 IDE 插件（Continue.dev / Cline）可以直接接入。
实现 /v1/chat/completions 和 /v1/models 两个核心端点，
支持流式输出和 function calling。
这样 VS Code 里的 Continue.dev 插件可以直接连到我们的 Agent。
"""
import json
import uuid
import time
from datetime import datetime
from typing import Optional, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path

from backend.config import get_settings, reset_settings
from backend.agent import get_agent
from backend.tools.registry import registry

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="DistilFlow API")

_server_start_time = time.time()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "distilflow", "uptime": round(time.time() - _server_start_time, 1)}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = FRONTEND_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")


@app.get("/api/hot-reload")
async def hot_reload_check():
    html_path = FRONTEND_DIR / "index.html"
    mtime = 0
    try:
        mtime = html_path.stat().st_mtime
    except Exception:
        pass
    return {
        "server_start": _server_start_time,
        "frontend_mtime": mtime,
    }


@app.get("/api/agent/memory")
async def get_memory(user_id: str = "default"):
    from backend.distillation.engine import MemoryManager
    mgr = MemoryManager(user_id)
    memory = mgr.read_memory()
    user_profile = mgr.read_user()
    return {"memory": memory, "user_profile": user_profile}


# ============================================================
# Request / Response Models (OpenAI 兼容格式)
# ============================================================

class Message(BaseModel):
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list] = None


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False
    tools: Optional[list[dict]] = None
    user: Optional[str] = None


class ModelObject(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "enterprise-agent"


# ============================================================
# /v1/models — 返回可用模型列表
# ============================================================

@app.get("/v1/models")
async def list_models():
    settings = get_settings()
    return {
        "object": "list",
        "data": [
            ModelObject(id=settings.default_model, owned_by="enterprise-agent").model_dump(),
            ModelObject(id="enterprise-agent-default", owned_by="enterprise-agent").model_dump(),
        ],
    }


@app.get("/api/providers/models")
async def fetch_provider_models(provider: str = "openrouter"):
    import httpx

    settings = get_settings()

    key = settings.get_provider_key(provider)
    base_url = settings.get_provider_base_url(provider)

    if not key:
        return {"object": "list", "data": [], "error": "No API key configured for this provider"}

    if not base_url:
        return {"object": "list", "data": [], "error": "Unknown provider"}

    url = f"{base_url.rstrip('/')}/models"

    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://distilflow.local"
    if provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"object": "list", "data": [], "error": f"API returned {resp.status_code}"}
            data = resp.json()

            models_list = data.get("data", data.get("models", []))
            result = []
            for m in models_list:
                mid = m.get("id", "")
                if not mid:
                    continue
                result.append({
                    "id": mid,
                    "name": m.get("name", m.get("display_name", mid)),
                    "owned_by": m.get("owned_by", ""),
                })
            return {"object": "list", "data": result}
    except Exception as e:
        return {"object": "list", "data": [], "error": str(e)}


@app.post("/api/analyze-file")
async def analyze_uploaded_file(file_path: str = ""):
    if not file_path:
        return {"error": "file_path is required"}

    path = Path(file_path)
    if not path.exists():
        return {"error": "file not found"}

    ext = path.suffix.lower()
    info = {"file_name": path.name, "file_type": ext, "actions": []}

    if ext in (".xlsx", ".xls", ".csv"):
        try:
            from backend.tools.excel_reader import SmartExcelReader
            reader = SmartExcelReader()
            result = reader._read(str(path))
            sheets = result.get("sheets", [])
            info["sheets"] = []
            for s in sheets:
                sheet_info = {
                    "name": s.get("name", ""),
                    "rows": s.get("row_count", 0),
                    "columns": s.get("col_count", 0),
                    "headers": s.get("headers", []),
                    "sample_rows": s.get("rows", [])[:3],
                }
                info["sheets"].append(sheet_info)

            headers = sheets[0].get("headers", []) if sheets else []
            has_number = any(kw in "".join(headers).lower() for kw in ["金额", "价格", "数量", "工资", "费用", "收入", "成本", "amount", "price", "qty", "salary"])
            has_date = any(kw in "".join(headers).lower() for kw in ["日期", "时间", "月", "年", "date", "time", "month"])
            has_dept = any(kw in "".join(headers).lower() for kw in ["部门", "科室", "组", "department", "team"])
            has_name = any(kw in "".join(headers).lower() for kw in ["姓名", "名字", "人员", "员工", "name"])

            actions = [
                {"id": "summary", "label": "📊 数据汇总", "desc": "按某列分组，汇总数值列", "prompt": "帮我汇总这个表的数据"},
                {"id": "filter", "label": "🔍 筛选数据", "desc": "按条件筛选出需要的行", "prompt": "帮我筛选这个表的数据"},
                {"id": "split", "label": "✂️ 拆分表格", "desc": "按某列拆分成多个文件", "prompt": "帮我按某列拆分这个表"},
            ]
            if has_number:
                actions.append({"id": "calc", "label": "🧮 计算统计", "desc": "求和、平均值、最大最小值", "prompt": "帮我计算这个表的统计数据"})
            if has_date:
                actions.append({"id": "trend", "label": "📈 趋势分析", "desc": "按时间维度看变化趋势", "prompt": "帮我分析这个表的时间趋势"})
            if has_dept or has_name:
                actions.append({"id": "rank", "label": "🏆 排名对比", "desc": "按部门/人员排名", "prompt": "帮我按部门/人员做排名"})
            actions.append({"id": "clean", "label": "🧹 清洗整理", "desc": "去重、补空值、统一格式", "prompt": "帮我清洗整理这个表"})
            actions.append({"id": "merge", "label": "🔗 合并多表", "desc": "多个工作表或文件合并", "prompt": "帮我把多个表合并"})
            actions.append({"id": "export", "label": "📤 格式转换", "desc": "转成其他格式导出", "prompt": "帮我把这个表转成其他格式"})
            info["actions"] = actions
        except Exception as e:
            info["error"] = str(e)

    elif ext == ".pdf":
        try:
            from backend.tools.pdf_reader import SmartPDFReader
            reader = SmartPDFReader()
            result = reader._read(str(path))
            info["pages"] = result.get("page_count", 0)
            info["has_tables"] = bool(result.get("tables"))
            info["has_images"] = bool(result.get("images"))
            info["text_preview"] = (result.get("text", "") or "")[:500]

            actions = [
                {"id": "extract", "label": "📄 提取文字", "desc": "提取PDF中的全部文字", "prompt": "帮我提取这个PDF的文字内容"},
                {"id": "table", "label": "📊 提取表格", "desc": "提取PDF中的表格数据", "prompt": "帮我提取这个PDF里的表格"},
            ]
            if result.get("images"):
                actions.append({"id": "ocr", "label": "🖼️ 图片OCR", "desc": "识别PDF中的图片文字", "prompt": "帮我识别这个PDF中图片的文字"})
            actions.append({"id": "summarize", "label": "📋 内容摘要", "desc": "总结PDF的主要内容", "prompt": "帮我总结这个PDF的内容"})
            actions.append({"id": "keyinfo", "label": "🔑 关键信息", "desc": "提取合同/报告的关键条款", "prompt": "帮我提取这个文件的关键信息"})
            info["actions"] = actions
        except Exception as e:
            info["error"] = str(e)

    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):
        info["actions"] = [
            {"id": "ocr", "label": "🖼️ 识别文字", "desc": "识别图片中的文字", "prompt": "帮我识别这张图片里的文字"},
            {"id": "describe", "label": "📝 描述内容", "desc": "描述图片内容", "prompt": "帮我描述这张图片的内容"},
            {"id": "extract_table", "label": "📊 提取表格", "desc": "识别图片中的表格", "prompt": "帮我识别这张图片中的表格数据"},
        ]
    else:
        info["actions"] = [
            {"id": "read", "label": "📄 读取内容", "desc": "读取文件内容", "prompt": "帮我读取这个文件的内容"},
        ]

    return info

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    user_id = req.user or "default"
    agent = get_agent(user_id)

    # 提取最后一条用户消息
    last_user_msg = ""
    for msg in reversed(req.messages):
        if msg.role == "user" and msg.content:
            last_user_msg = msg.content
            break

    if not last_user_msg:
        raise HTTPException(status_code=400, detail="No user message found")

    if req.stream:
        return StreamingResponse(
            _stream_response(agent, last_user_msg, req.model),
            media_type="text/event-stream",
        )
    else:
        full_response = ""
        async for chunk in agent.chat(last_user_msg):
            full_response += chunk

        return _build_response(full_response, req.model)


async def _stream_response(agent, user_msg: str, model: str):
    """SSE 流式输出（OpenAI 格式）"""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async for chunk in agent.chat(user_msg):
        data = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 结束标记
    done_data = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _build_response(content: str, model: str) -> dict:
    """构建非流式响应（OpenAI 格式）"""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ============================================================
# /v1/files — 文件上传/下载端点
# ============================================================

from fastapi import UploadFile, File

@app.post("/v1/files")
async def upload_file(file: UploadFile = File(...)):
    """上传文件到 Agent 工作区"""
    settings = get_settings()
    upload_dir = Path(settings.data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    return {
        "id": f"file-{uuid.uuid4().hex[:12]}",
        "object": "file",
        "filename": file.filename,
        "bytes": len(content),
        "path": str(file_path),
    }


@app.get("/v1/files/{file_id}/content")
async def download_file(file_id: str, path: Optional[str] = None):
    """下载 Agent 生成的文件"""
    if not path:
        raise HTTPException(status_code=400, detail="path parameter required")

    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={file_path.name}"},
    )


# ============================================================
# /api/agent/status — Agent 状态查询
# ============================================================

@app.get("/api/agent/status")
async def agent_status(user_id: str = "default"):
    agent = get_agent(user_id)
    return agent.get_status()


# ============================================================
# /api/agent/skills — 技能管理
# ============================================================

@app.get("/api/agent/skills")
async def list_skills(user_id: str = "default", status: Optional[str] = None):
    from backend.distillation.engine import SkillManager
    mgr = SkillManager(user_id)
    return {"skills": mgr.list_skills(status)}


class ConsolidateRequest(BaseModel):
    user_id: str = "default"
    skill_names: list[str] = []
    new_name: str = "综合工具"
    new_trigger: str = "综合处理"


@app.post("/api/agent/skills/consolidate")
async def consolidate_skills(req: ConsolidateRequest):
    from backend.distillation.engine import SkillManager
    mgr = SkillManager(req.user_id)
    result = mgr.consolidate_skills(req.skill_names, req.new_name, req.new_trigger)
    return result


@app.post("/api/agent/skills/{skill_file:path}/activate")
async def activate_skill(skill_file: str, user_id: str = "default"):
    from backend.distillation.engine import SkillManager
    mgr = SkillManager(user_id)
    mgr.activate_skill(skill_file)
    return {"status": "activated"}


# ============================================================
# /api/agent/summary — 生成工作总结
# ============================================================

@app.post("/api/agent/summary")
async def generate_summary(user_id: str = "default"):
    from backend.distillation.engine import DistillationPipeline
    pipeline = DistillationPipeline(user_id)
    summary = pipeline.generate_summary()
    return {"summary": summary}


# ============================================================
# /api/sessions — 会话管理（创建/列表/读取/删除）
# ============================================================

class SessionCreateRequest(BaseModel):
    title: str = "新对话"

class SessionSaveRequest(BaseModel):
    messages: list[dict[str, Any]] = []
    title: str = ""

def _sessions_dir() -> Path:
    p = Path(get_settings().data_dir) / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"

@app.post("/api/sessions")
async def create_session(req: SessionCreateRequest):
    sid = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()
    data = {
        "id": sid,
        "title": req.title or "新对话",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    _session_path(sid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

@app.get("/api/sessions")
async def list_sessions():
    sessions = []
    for f in sorted(_sessions_dir().glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "id": d.get("id", f.stem),
                "title": d.get("title", "新对话"),
                "updated_at": d.get("updated_at", ""),
                "message_count": len(d.get("messages", [])),
            })
        except:
            pass
    return {"sessions": sessions}

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    p = _session_path(session_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return json.loads(p.read_text(encoding="utf-8"))

@app.put("/api/sessions/{session_id}")
async def save_session(session_id: str, req: SessionSaveRequest):
    p = _session_path(session_id)
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing["messages"] = req.messages
    if req.title:
        existing["title"] = req.title
    existing["updated_at"] = datetime.now().isoformat()
    p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "saved", "message_count": len(req.messages)}

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    p = _session_path(session_id)
    if p.exists():
        p.unlink()
    return {"status": "deleted"}


class SettingsRequest(BaseModel):
    provider: str = "openrouter"
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""
    company: str = ""
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_corp_id: str = ""


@app.post("/api/settings")
async def save_settings(req: SettingsRequest):
    settings = get_settings()

    if req.apiKey:
        if not settings.provider_keys:
            settings.provider_keys = {}
        settings.provider_keys[req.provider] = req.apiKey

    if req.baseUrl:
        if not settings.provider_base_urls:
            settings.provider_base_urls = {}
        settings.provider_base_urls[req.provider] = req.baseUrl

    if req.model:
        settings.default_model = req.model
    if req.company:
        settings.company_name = req.company
    if req.dingtalk_app_key:
        settings.dingtalk_app_key = req.dingtalk_app_key
    if req.dingtalk_app_secret:
        settings.dingtalk_app_secret = req.dingtalk_app_secret
    if req.dingtalk_corp_id:
        settings.dingtalk_corp_id = req.dingtalk_corp_id
    settings.llm_provider = req.provider

    config_path = Path(__file__).parent.parent / "data" / "providers.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config_data = {
        "provider_keys": settings.provider_keys,
        "provider_base_urls": settings.provider_base_urls,
        "llm_provider": settings.llm_provider,
        "default_model": settings.default_model,
        "company_name": settings.company_name,
        "dingtalk_app_key": settings.dingtalk_app_key,
        "dingtalk_app_secret": settings.dingtalk_app_secret,
        "dingtalk_corp_id": settings.dingtalk_corp_id,
    }
    config_path.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")

    reset_settings()

    return {"status": "saved"}


@app.get("/api/settings")
async def get_settings_api():
    settings = get_settings()
    provider_keys_masked = {}
    for k, v in (settings.provider_keys or {}).items():
        if v and len(v) > 4:
            provider_keys_masked[k] = "****" + v[-4:]
        else:
            provider_keys_masked[k] = ""

    return {
        "provider": settings.llm_provider,
        "provider_keys": provider_keys_masked,
        "provider_base_urls": settings.provider_base_urls or {},
        "model": settings.default_model,
        "company": settings.company_name,
        "dingtalk_app_key": settings.dingtalk_app_key,
        "dingtalk_corp_id": settings.dingtalk_corp_id,
        "dingtalk_configured": bool(settings.dingtalk_app_key and settings.dingtalk_app_secret),
    }


# ============================================================
# /api/workflows — 工作流管理
# ============================================================

class WorkflowCreateRequest(BaseModel):
    name: str
    trigger: list[str] = []
    steps: list[dict] = []
    variables: dict = {}

class WorkflowActivateRequest(BaseModel):
    status: str = "active"

class WorkflowStepRequest(BaseModel):
    step: dict = {}

class WorkflowStepUpdateRequest(BaseModel):
    updates: dict = {}

@app.get("/api/workflows")
async def list_workflows(status: Optional[str] = None):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    return {"workflows": engine.list_workflows(status=status)}

@app.post("/api/workflows")
async def create_workflow(req: WorkflowCreateRequest):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.create_workflow(
        name=req.name,
        trigger=req.trigger,
        steps=req.steps,
        variables=req.variables,
    )
    return wf

@app.get("/api/workflows/{wf_id}")
async def get_workflow(wf_id: str):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.get_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf

@app.put("/api/workflows/{wf_id}")
async def update_workflow(wf_id: str, req: WorkflowActivateRequest):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.update_workflow(wf_id, {"status": req.status})
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf

@app.delete("/api/workflows/{wf_id}")
async def delete_workflow(wf_id: str):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    if not engine.delete_workflow(wf_id):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "deleted"}

@app.post("/api/workflows/{wf_id}/activate")
async def activate_workflow(wf_id: str):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.activate_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf

@app.post("/api/workflows/{wf_id}/steps")
async def add_workflow_step(wf_id: str, req: WorkflowStepRequest):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.add_step(wf_id, req.step)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf

@app.put("/api/workflows/{wf_id}/steps/{step_id}")
async def update_workflow_step(wf_id: str, step_id: str, req: WorkflowStepUpdateRequest):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.update_step(wf_id, step_id, req.updates)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow or step not found")
    return wf

@app.delete("/api/workflows/{wf_id}/steps/{step_id}")
async def remove_workflow_step(wf_id: str, step_id: str):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.remove_step(wf_id, step_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf

@app.post("/api/workflows/generate-from-session/{session_id}")
async def generate_workflow_from_session(session_id: str):
    from backend.workflows.engine import WorkflowEngine
    p = _session_path(session_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    session_data = json.loads(p.read_text(encoding="utf-8"))
    messages = session_data.get("messages", [])
    engine = WorkflowEngine()
    wf = engine.auto_generate_from_session(messages, session_id)
    if not wf:
        return {"error": "该会话工具调用不足，无法生成工作流"}
    return wf


class WorkflowExtendRequest(BaseModel):
    new_steps: list[dict] = []

@app.post("/api/workflows/{wf_id}/extend")
async def extend_workflow(wf_id: str, req: WorkflowExtendRequest):
    from backend.workflows.engine import WorkflowEngine
    engine = WorkflowEngine()
    wf = engine.get_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    existing_steps = wf.get("steps", [])
    for i, ns in enumerate(req.new_steps):
        ns["id"] = f"step_{len(existing_steps) + i + 1}"
        existing_steps.append(ns)
    wf = engine.update_workflow(wf_id, {"steps": existing_steps})
    return wf


@app.get("/api/workflows/{wf_id}/execute-stream")
async def execute_workflow_stream(wf_id: str, variables: str = "{}"):
    from backend.workflows.engine import WorkflowEngine
    import asyncio

    engine = WorkflowEngine()
    wf = engine.get_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        vars_dict = json.loads(variables)
    except:
        vars_dict = {}

    async def event_generator():
        async for event in engine.execute_workflow(wf_id, variables=vars_dict):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================
# 钉钉扫码登录
# ============================================================

class DingTalkCallbackRequest(BaseModel):
    auth_code: str = ""


@app.get("/api/dingtalk/config")
async def dingtalk_config():
    settings = get_settings()
    app_key = settings.dingtalk_app_key
    if not app_key:
        return {"configured": False}
    return {
        "configured": True,
        "app_key": app_key,
        "corp_id": settings.dingtalk_corp_id,
    }


@app.get("/api/dingtalk/redirect")
async def dingtalk_redirect(authCode: str = "", code: str = "", state: str = ""):
    auth_code = authCode or code
    if not auth_code:
        return HTMLResponse(content="<html><body><script>window.close();</script><p>授权失败：未获取到授权码</p></body></html>")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>登录中...</title>
<style>body{{font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#3B4A54;color:#E1EAF0}}</style>
</head><body>
<div style="text-align:center">
<p>正在登录...</p>
<script>
window.opener && window.opener.postMessage({{authCode: '{auth_code}'}}, '*');
if (window.DTFrameLogin) {{}} else {{ window.close(); }}
setTimeout(function() {{ window.close(); }}, 3000);
</script>
</div></body></html>"""
    return HTMLResponse(content=html)


@app.post("/api/dingtalk/callback")
async def dingtalk_callback(req: DingTalkCallbackRequest):
    from backend.auth import get_user_token, get_user_info, get_userid_by_unionid, get_user_detail_by_userid

    auth_code = req.auth_code
    if not auth_code:
        return {"error": "auth_code is required"}

    token_data = get_user_token(auth_code)
    if not token_data:
        return {"error": "获取用户token失败，请检查钉钉应用配置"}

    user_access_token = token_data["access_token"]
    user_info = get_user_info(user_access_token)
    if not user_info:
        return {"error": "获取用户信息失败"}

    user_id = user_info.get("open_id", "")
    user_name = user_info.get("name", "")
    union_id = user_info.get("union_id", "")

    extra_info = {}
    if union_id:
        userid = get_userid_by_unionid(union_id)
        if userid:
            detail = get_user_detail_by_userid(userid)
            if detail:
                extra_info = detail

    return {
        "user_id": user_id,
        "name": user_name or extra_info.get("name", ""),
        "avatar": user_info.get("avatar", "") or extra_info.get("avatar", ""),
        "mobile": user_info.get("mobile", "") or extra_info.get("mobile", ""),
        "email": user_info.get("email", "") or extra_info.get("email", ""),
        "dept_id": extra_info.get("dept_id", []),
        "title": extra_info.get("title", ""),
    }


# ============================================================
# 自动化操作
# ============================================================

@app.get("/api/automation/templates")
async def automation_templates():
    from backend.automation.models import AutomationStore
    store = AutomationStore()
    templates = store.list_all()
    return {"templates": templates}


@app.post("/api/automation/analyze")
async def automation_analyze(req: dict):
    url = req.get("url", "")
    name = req.get("name", "未命名操作")
    if not url:
        return {"error": "URL 不能为空"}
    from backend.automation.ai_assistant import AIAssistant
    assistant = AIAssistant()
    result = await assistant.auto_build_template(name=name, url=url, data_sample=req.get("data_sample"))
    return result or {"error": "分析失败"}


@app.post("/api/automation/execute")
async def automation_execute(req: dict):
    template_id = req.get("template_id", "")
    data = req.get("data", {})
    visible = req.get("visible", True)
    slow_mo = req.get("slow_mo", 500)
    if not template_id:
        return {"error": "template_id 不能为空"}
    from backend.automation.recorder import Player
    player = Player()
    results = []
    async for event in player.play(template_id=template_id, data=data, headless=not visible, slow_mo=slow_mo):
        results.append(event)
    errors = [r for r in results if r.get("type") == "step_error"]
    done = [r for r in results if r.get("type") == "step_done"]
    final = [r for r in results if r.get("type") in ("play_done", "play_failed")]
    final_event = final[0] if final else {}
    if final_event.get("type") == "play_done":
        return {"status": "success", "steps_completed": len(done), "errors": [{"step": e.get("step_id"), "error": e.get("error")} for e in errors]}
    return {"status": "failed", "error": final_event.get("error", "未知错误"), "steps_completed": len(done)}


@app.delete("/api/automation/templates/{template_id}")
async def automation_delete(template_id: str):
    from backend.automation.models import AutomationStore
    store = AutomationStore()
    if store.delete(template_id):
        return {"status": "deleted"}
    return {"error": "模板不存在"}
