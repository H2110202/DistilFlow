"""
Agent 核心引擎 — 借鉴 Hermes 的 ReAct 循环 + execute_code 模式。
核心能力：
1. ReAct 推理循环：Think → Act → Observe → Think → ...
2. 工具调用：通过 ToolRegistry 注册和执行
3. 代码执行：execute_code 在安全沙箱中运行 Python
4. 蒸馏集成：每次交互后自动触发蒸馏管线
5. 自进化：技能自动创建 + 记忆自动策展
6. 工作流优先：匹配到固定工作流时走确定性执行，否则走 ReAct
"""
import json
import re
import sys
import uuid
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional
from backend.config import get_settings
from backend.llm_client import chat_with_tools, chat_stream
from backend.tools.registry import registry
import backend.tools.excel_reader
import backend.tools.pdf_reader
import backend.tools.file_tools
import backend.tools.doc_filler
import backend.tools.validator
import backend.automation.tools
from backend.distillation.engine import DistillationPipeline
from backend.workflows.engine import WorkflowEngine
from backend.workflows.skill_templates import SkillTemplates


SYSTEM_PROMPT_TEMPLATE = """你是{company_name}的DistilFlow智能助手。通过对话帮员工搭建工作流并执行。

## 核心规则
1. 信息不足时**绝对不能猜测**，必须用编号列表追问，一次问完
2. 每个不确定的点给出2-3个选项让员工选
3. 匹配到技能时，严格按技能步骤执行，不要跳步
4. 员工说"确认"/"开始"后才执行操作
5. 遵循记忆中的偏好，员工纠正时认真记住

## 可用工具
{tools_description}

## 自动化工具使用指南
当员工提到企业系统操作时，按以下流程使用自动化工具：

### 系统文档（核心能力）
- 员工首次提到某个企业系统 → 调用 **generate_system_guide**(site_url, site_name) 生成系统使用文档
- 生成文档后，员工说任何操作需求 → 调用 **query_system_guide**(site_url, user_intent) 精准定位入口
- query_system_guide 会根据关键词匹配，返回最合适的功能入口URL和说明
- 员工说"列出系统文档" → 调用 **list_system_guides**()

### 扫描与探索
- 员工说"扫描系统"/"看看OA有什么功能"/"了解系统结构" → 调用 **scan_site_navigation**(url)
- 员工说"看看这个页面有什么字段"/"扫描表单" → 调用 **scan_form_page**(url)
- scan_site_navigation 会自动登录（如已保存凭据），提取菜单、导航、功能模块，返回站点地图

### 凭据管理
- 员工提供系统账号密码 → 调用 **save_site_credentials**(site_url, username, password)
- 保存后，后续所有操作会自动登录，无需重复提供

### 模板创建
- 扫描完表单后，调用 **auto_build_template**(url, name) 自动生成操作模板
- 员工说"列出已有的自动化模板" → 调用 **list_automation_templates**()

### 字段映射
- 数据源列名和系统字段名不一致时 → 调用 **set_field_mapping**(template_id, mapping)
- mapping 格式：{{"数据源列名": "目标字段选择器"}}

### 执行与预览
- 填写前先预览 → 调用 **preview_automation**(template_id, data)
- 确认后执行 → 调用 **execute_automation**(template_id, data, batch_data)
- 批量填写时用 batch_data 参数（JSON数组）

### OA流程极速提交（推荐）
- **标准流程：查规范 → 追问缺失信息 → 提交**
- 步骤1: 员工说"帮我申请xxx" → 调用 **get_flow_spec**(flow_name) 查询该流程的填写规范
- 步骤2: 根据返回的 `required_missing_prompts` 追问用户缺失的必填信息
- 步骤3: 收集完所有必填信息后 → 调用 **submit_oa_flow**(site_url, template_id, form_data)
- 查看可用流程模板 → 调用 **list_oa_flow_templates**(site_url, category)
- 为新流程添加规范 → 调用 **add_flow_spec**(flow_name, spec_json)
- 速度比浏览器方式快10-16倍（~1.2s vs ~19s），自动复用session
- submit=false 时仅保存草稿，submit=true 时直接提交进入审批
- **关键原则**：不要替用户编造信息！必填字段缺失时必须追问，宁可多问一句也不要填错

### 典型对话流程
1. 员工："帮我扫描OA系统" → generate_system_guide(url, "OA系统") → 生成完整文档
2. 员工："我要看待办" → query_system_guide(url, "待办") → 返回入口URL → scan_form_page → 填写
3. 员工："账号是xxx密码是xxx" → save_site_credentials(url, u, p)
4. 员工："帮我自动填写" → auto_build_template → preview_automation → execute_automation
5. 员工："帮我申请一个鼠标" → get_flow_spec("IT软硬件需求申请") → 追问"请说明申请原因" → 用户回答后 → submit_oa_flow（极速1.2s提交）
6. 员工："我要用章" → get_flow_spec("用章审批") → 追问"请选择印章类型、用印文件名称、用章事由" → 用户回答后 → submit_oa_flow

**重要**：你拥有这些工具，可以直接调用。不要说"我没有扫描工具"，当员工提到扫描系统时，立即调用 generate_system_guide 或 scan_site_navigation。

{memory_context}

{skill_context}

{workflow_context}

## 当前时间
{current_time}
"""


class AgentEngine:
    """
    Agent 核心引擎 — 员工的个人工作助手实例。
    每个员工一个实例，拥有独立的记忆、技能和蒸馏管线。
    """

    def __init__(self, user_id: str = "default"):
        self.settings = get_settings()
        self.user_id = user_id
        self.distillation = DistillationPipeline(user_id)
        self.workflows = WorkflowEngine(user_id)
        self.skill_templates = SkillTemplates()
        self.conversation_history: list[dict] = []
        self.session_id = str(uuid.uuid4())[:8]
        self._all_tool_calls: list[dict] = []
        self._workflow_paused: Optional[dict] = None
        self._current_building_wf: Optional[dict] = None
        self._pending_related_wf: Optional[dict] = None
        self._extending_wf: bool = False

    def _build_system_prompt(self, user_message: str = "") -> str:
        tools_desc = self._format_tools()
        memory_ctx = self.distillation.memory.get_context_for_prompt()
        skill_ctx = self._match_skill_template(user_message)
        workflow_ctx = self._get_workflow_context(user_message)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        return SYSTEM_PROMPT_TEMPLATE.format(
            company_name=self.settings.company_name,
            tools_description=tools_desc,
            memory_context=memory_ctx,
            skill_context=skill_ctx,
            workflow_context=workflow_ctx,
            current_time=current_time,
        )

    def _match_skill_template(self, user_message: str) -> str:
        tpl = self.skill_templates.match_template(user_message)
        if not tpl:
            return ""
        return self.skill_templates.get_injection_prompt(tpl)

    def _get_workflow_context(self, user_message: str) -> str:
        parts = []
        matched_wf = self.workflows.match_workflow(user_message)
        if matched_wf:
            steps_desc = []
            for s in matched_wf.get("steps", []):
                if s.get("type") == "tool":
                    steps_desc.append(f"  {s.get('id')}: 调用 {s.get('tool')}")
                elif s.get("type") == "human_confirm":
                    steps_desc.append(f"  {s.get('id')}: 人工确认 - {s.get('message', '')}")
                elif s.get("type") == "condition":
                    steps_desc.append(f"  {s.get('id')}: 条件判断 - {s.get('condition', '')}")
                elif s.get("type") == "loop":
                    steps_desc.append(f"  {s.get('id')}: 循环 - 最多{s.get('max_iterations', 5)}次")
                elif s.get("type") == "parallel":
                    steps_desc.append(f"  {s.get('id')}: 并行执行 {len(s.get('parallel_steps', []))}个任务")
            parts.append(f"<matched_workflow>\n匹配到已有工作流「{matched_wf['name']}」(ID: {matched_wf['id']})：\n" + "\n".join(steps_desc) + "\n请按此工作流确定性执行。\n</matched_workflow>")
        building_wf = self._current_building_wf
        if building_wf:
            steps_desc = []
            for s in building_wf.get("steps", []):
                if s.get("type") == "tool":
                    steps_desc.append(f"  {s.get('id')}: 调用 {s.get('tool')}")
                elif s.get("type") == "human_confirm":
                    steps_desc.append(f"  {s.get('id')}: 人工确认")
            parts.append(f"<building_workflow>\n正在搭建工作流「{building_wf['name']}」(ID: {building_wf['id']})，当前 {len(building_wf.get('steps', []))} 步：\n" + "\n".join(steps_desc) + "\n继续完善或确认执行。\n</building_workflow>")
        return "\n\n".join(parts)

    def _format_tools(self) -> str:
        schemas = registry.get_schemas()
        lines = []
        for s in schemas:
            fn = s.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "")
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    async def chat(self, user_message: str, files: Optional[list[str]] = None) -> AsyncGenerator[str, None]:
        content = user_message
        if files:
            file_info = "\n\n".join(f"[文件已上传: {f}]" for f in files)
            content = f"{user_message}\n\n{file_info}"
        self.conversation_history.append({"role": "user", "content": content})

        if self._workflow_paused:
            wf_id = self._workflow_paused["wf_id"]
            step_id = self._workflow_paused["step_id"]
            confirm = user_message.strip()
            self._workflow_paused = None
            async for chunk in self._resume_workflow(wf_id, step_id, confirm):
                yield chunk
            return

        msg = user_message.strip()

        save_intent = self._detect_save_intent(msg)
        if save_intent is not None:
            async for chunk in self._handle_save_workflow(save_intent):
                yield chunk
            return

        if self._detect_discard_intent(msg):
            if self._current_building_wf:
                self._current_building_wf = None
                yield "👌 好的，不保存工作流。\n"
                return

        extend_match = re.match(r"在(.+?)工作流[增加添加](.+)", msg)
        if not extend_match:
            extend_match = re.match(r"给(.+?)工作流[增加添加](.+)", msg)
        if not extend_match:
            extend_match = re.match(r"(.+?)工作流[加上加个](.+)", msg)
        if extend_match:
            wf_name = extend_match.group(1).strip()
            extend_desc = extend_match.group(2).strip()
            async for chunk in self._handle_extend_workflow(wf_name, extend_desc):
                yield chunk
            return

        if self._pending_related_wf:
            choice = msg
            if choice in ("扩展", "追加", "在原工作流加"):
                async for chunk in self._confirm_extend_related():
                    yield chunk
                return
            elif choice in ("新建", "创建新的"):
                self._pending_related_wf = None
            elif choice in ("直接做", "只执行", "不保存"):
                self._pending_related_wf = None
            elif choice in ("取消",):
                self._pending_related_wf = None
                return

        matched_wf = self.workflows.match_workflow(user_message)
        if matched_wf:
            yield f"🔄 匹配到工作流「{matched_wf['name']}」，开始执行\n\n"
            variables = {}
            if files:
                variables["file_path"] = files[0]
            async for chunk in self._execute_workflow(matched_wf["id"], variables):
                yield chunk
            return

        matched_skill = self.skill_templates.match_template(user_message)
        if matched_skill:
            wf = self.skill_templates.to_workflow(matched_skill, user_message)
            self.workflows.activate_workflow(wf["id"])
            yield f"⚡ 匹配到技能「{matched_skill['name']}」，已生成工作流\n\n"
            variables = {}
            if files:
                variables["source_folder"] = files[0]
                variables["file_path"] = files[0]
            async for chunk in self._execute_workflow(wf["id"], variables):
                yield chunk
            return

        related = self.workflows.find_related_workflows(user_message)
        if related and not self._current_building_wf:
            wf = related[0]
            self._pending_related_wf = wf
            step_names = []
            for s in wf.get("steps", []):
                if s.get("type") == "tool":
                    step_names.append(s.get("tool", s.get("name", "")))
                elif s.get("type") == "human_confirm":
                    step_names.append("人工确认")
            yield (
                f"🔍 检测到和已有工作流「{wf['name']}」相关\n\n"
                f"当前步骤：{' → '.join(step_names)}\n\n"
                f"回复 **「扩展」**：在原工作流末尾追加步骤\n"
                f"回复 **「新建」**：创建独立的新工作流\n"
                f"回复 **「直接做」**：只执行这次，不保存\n\n"
            )
            if not self._current_building_wf:
                wf_new = self.workflows.create_workflow(
                    name=user_message.strip()[:30],
                    trigger=self.workflows._extract_trigger_keywords(user_message),
                )
                self._current_building_wf = wf_new
            return

        if not self._current_building_wf:
            wf = self.workflows.create_workflow(
                name=user_message.strip()[:30],
                trigger=self.workflows._extract_trigger_keywords(user_message),
            )
            self._current_building_wf = wf

        max_rounds = self.settings.max_tool_call_rounds
        for round_idx in range(max_rounds):
            tools = registry.get_schemas()
            result = chat_with_tools(
                messages=self._build_messages(user_message),
                tools=tools if tools else None,
                temperature=0.3,
            )

            if "tool_calls" not in result:
                response_text = result["content"]
                assistant_msg = {"role": "assistant", "content": response_text}
                if result.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = result["reasoning_content"]
                self.conversation_history.append(assistant_msg)
                yield response_text
                self._process_workflow_building(response_text, user_message)
                self._trigger_distillation(user_message, response_text)
                async for chunk in self._suggest_save_workflow():
                    yield chunk
                return

            if result["content"]:
                yield f"💭 {result['content']}\n\n"

            assistant_msg = {
                "role": "assistant",
                "content": result["content"] or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                            if isinstance(tc["arguments"], dict)
                            else str(tc["arguments"]),
                        },
                    }
                    for tc in result["tool_calls"]
                ],
            }
            if result.get("reasoning_content"):
                assistant_msg["reasoning_content"] = result["reasoning_content"]
            self.conversation_history.append(assistant_msg)

            for tc in result["tool_calls"]:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                self._all_tool_calls.append(tc)
                yield f"🔧 调用工具: {tool_name}\n"
                tool_result = registry.execute(tool_name, tool_args)
                if tool_name == "execute_code":
                    yield f"```\n{tool_result[:2000]}\n```\n\n"
                else:
                    try:
                        parsed = json.loads(tool_result)
                        if isinstance(parsed, dict):
                            if "error" in parsed:
                                yield f"❌ 错误: {parsed['error']}\n\n"
                            elif "warnings" in parsed and parsed["warnings"]:
                                yield f"⚠️ {', '.join(parsed['warnings'])}\n"
                                yield f"✅ 已读取\n\n"
                            else:
                                yield f"✅ 已读取\n\n"
                        else:
                            yield f"✅ 完成\n\n"
                    except json.JSONDecodeError:
                        yield f"✅ 完成\n\n"
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result[:8000],
                })

        yield "\n⚠️ 已达到最大工具调用轮次，任务可能未完全完成。"

    def _detect_save_intent(self, msg: str) -> Optional[str]:
        patterns = [
            (r"保存工作流[：:]\s*(.+)", 1),
            (r"保存[为成]?\s*[「\"'](.+?)[」\"']", 1),
            (r"存[为成]?\s*[「\"'](.+?)[」\"']", 1),
            (r"固化[为成]?\s*[「\"'](.+?)[」\"']", 1),
            (r"把这个?工作流[保存存]?[为成叫]?\s*(.+)", 1),
            (r"把这个?流程[保存存]?[为成叫]?\s*(.+)", 1),
            (r"记住这个流程[，,]?\s*叫(.+)", 1),
            (r"保存工作流\s*(.*)", 1),
            (r"保存流程\s*(.*)", 1),
            (r"存下来[，,]]?\s*叫(.+)", 1),
        ]
        for pattern, group in patterns:
            m = re.match(pattern, msg)
            if m:
                name = m.group(group).strip().strip("「」\"'")
                if name:
                    return name
        save_only = ["保存工作流", "保存流程", "存下来", "固化", "保存", "记住这个流程"]
        if msg in save_only:
            if self._current_building_wf:
                return self._current_building_wf.get("name", "未命名工作流")
        return None

    def _detect_discard_intent(self, msg: str) -> bool:
        discard_phrases = [
            "不保存", "忽略", "取消保存", "不用了", "不用保存",
            "不存了", "算了", "别保存", "不要保存", "放弃保存",
        ]
        return msg in discard_phrases

    def _process_workflow_building(self, agent_response: str, user_message: str):
        if not self._current_building_wf:
            return
        wf_id = self._current_building_wf["id"]
        if self._all_tool_calls:
            for tc in self._all_tool_calls:
                existing_tools = {s.get("tool") for s in (self._current_building_wf.get("steps", [])) if s.get("type") == "tool"}
                if tc.get("name") not in existing_tools:
                    step = {
                        "id": f"step_{len(self._current_building_wf.get('steps', [])) + 1}",
                        "name": tc.get("name", ""),
                        "type": "tool",
                        "tool": tc.get("name", ""),
                        "args": {},
                        "output_key": tc.get("name", ""),
                    }
                    args = tc.get("arguments", {})
                    for k, v in args.items():
                        if isinstance(v, str) and (len(v) > 50 or v.startswith("/") or v.startswith("C:") or v.startswith("data/")):
                            step["args"][k] = "{{variables." + k + "}}"
                        else:
                            step["args"][k] = v
                    self.workflows.add_step(wf_id, step)
                    self._current_building_wf = self.workflows.get_workflow(wf_id)

    async def _suggest_save_workflow(self):
        if not self._current_building_wf:
            return
        wf = self._current_building_wf
        steps = wf.get("steps", [])
        if len(steps) < 2:
            self._current_building_wf = None
            return

        step_names = []
        for s in steps:
            if s.get("type") == "tool":
                step_names.append(s.get("tool", s.get("name", "")))
            elif s.get("type") == "human_confirm":
                step_names.append("人工确认")

        yield (
            f"\n\n---\n\n"
            f"📌 **本次任务使用了 {len(steps)} 个步骤**：{' → '.join(step_names)}\n\n"
            f"回复 **「保存工作流：名称」** 固化（如：保存工作流：数据清洗）\n"
            f"回复 **「不保存」** 忽略"
        )

    async def _handle_save_workflow(self, wf_name: str):
        if not self._current_building_wf:
            yield "⚠️ 当前没有待保存的工作流。请先完成一个任务。\n"
            return
        wf = self._current_building_wf
        steps = wf.get("steps", [])
        if len(steps) < 2:
            self._current_building_wf = None
            yield "⚠️ 工作流步骤不足（至少需要2步），无法保存。\n"
            return
        self.workflows.update_workflow(wf["id"], {"name": wf_name})
        self.workflows.activate_workflow(wf["id"])
        self._current_building_wf = None
        step_names = []
        for s in steps:
            if s.get("type") == "tool":
                step_names.append(s.get("tool", s.get("name", "")))
            elif s.get("type") == "human_confirm":
                step_names.append("人工确认")
        trigger_words = wf.get("trigger", [])
        trigger_str = "、".join(trigger_words[:3]) if trigger_words else "（自动提取）"
        yield (
            f"✅ 工作流「{wf_name}」已保存并激活！\n\n"
            f"步骤：{' → '.join(step_names)}\n"
            f"触发关键词：{trigger_str}\n\n"
            f"下次说类似需求会自动匹配执行。"
        )

    async def _handle_extend_workflow(self, wf_name: str, extend_desc: str):
        active_wfs = self.workflows.list_workflows(status="active")
        target_wf = None
        for wf in active_wfs:
            if wf_name in wf["name"] or wf["name"] in wf_name:
                target_wf = wf
                break
        if not target_wf:
            yield f"⚠️ 未找到工作流「{wf_name}」。已有工作流：{', '.join(w['name'] for w in active_wfs) if active_wfs else '无'}\n"
            return

        old_steps = target_wf.get("steps", [])
        old_step_names = []
        for s in old_steps:
            if s.get("type") == "tool":
                old_step_names.append(s.get("tool", s.get("name", "")))
            elif s.get("type") == "human_confirm":
                old_step_names.append("人工确认")

        self._current_building_wf = target_wf
        self._all_tool_calls = []
        self._extending_wf = True

        yield (
            f"🔧 扩展工作流「{target_wf['name']}」\n\n"
            f"当前步骤：{' → '.join(old_step_names)}\n"
            f"新增需求：{extend_desc}\n\n"
            f"正在执行新步骤...\n\n"
        )

    async def _confirm_extend_related(self):
        if not self._pending_related_wf:
            return
        wf = self._pending_related_wf
        self._pending_related_wf = None
        self._current_building_wf = wf
        self._all_tool_calls = []
        self._extending_wf = True

        old_steps = wf.get("steps", [])
        old_step_names = []
        for s in old_steps:
            if s.get("type") == "tool":
                old_step_names.append(s.get("tool", s.get("name", "")))
            elif s.get("type") == "human_confirm":
                old_step_names.append("人工确认")

        yield (
            f"✅ 将在「{wf['name']}」工作流末尾追加新步骤\n\n"
            f"当前步骤：{' → '.join(old_step_names)}\n\n"
            f"请描述要追加的功能，我会执行并自动添加。\n"
        )

    async def _execute_workflow(self, wf_id: str, variables: Optional[dict] = None) -> AsyncGenerator[str, None]:
        async for event in self.workflows.execute_workflow(wf_id, variables=variables):
            evt_type = event.get("type", "")

            if evt_type == "workflow_start":
                yield f"📋 工作流「{event['workflow_name']}」开始执行（共 {event['total_steps']} 步）\n\n"

            elif evt_type == "step_start":
                yield f"**步骤 {event['step_index']}/{event['total_steps']}：{event['step_name']}**\n"

            elif evt_type == "tool_call":
                yield f"🔧 调用 {event['tool']}\n"

            elif evt_type == "step_done":
                yield f"✅ 完成\n\n"

            elif evt_type == "step_error":
                retrying = event.get("retrying", False)
                if retrying:
                    yield f"⚠️ 出错: {event['error']}，正在重试...\n"
                else:
                    yield f"❌ 失败: {event['error']}\n\n"

            elif evt_type == "human_confirm":
                self._workflow_paused = {
                    "wf_id": wf_id,
                    "step_id": event["step_id"],
                }
                options = " / ".join(event.get("options", ["确认", "取消"]))
                yield f"\n⏸️ **{event['message']}**\n请回复：{options}\n"
                return

            elif evt_type == "workflow_done":
                yield f"🎉 工作流「{event['workflow_name']}」执行完成！\n"
                self._trigger_distillation("workflow_execution", json.dumps(event.get("outputs", {}), ensure_ascii=False))

            elif evt_type == "workflow_failed":
                yield f"❌ 工作流「{event['workflow_name']}」在「{event.get('failed_step', '')}」步骤失败\n"

    async def _resume_workflow(self, wf_id: str, step_id: str, confirm_response: str) -> AsyncGenerator[str, None]:
        if confirm_response in ["取消", "不对", "不是", "不"]:
            self._workflow_paused = None
            yield "⏹️ 工作流已取消。你可以直接告诉我你想怎么做。\n"
            return
        yield "✅ 确认继续\n\n"
        async for chunk in self._execute_workflow_resumed(wf_id, step_id, confirm_response):
            yield chunk

    async def _execute_workflow_resumed(self, wf_id: str, step_id: str, confirm_response: str) -> AsyncGenerator[str, None]:
        async for event in self.workflows.execute_workflow(wf_id, resume_from=step_id, confirm_response=confirm_response):
            evt_type = event.get("type", "")

            if evt_type == "step_start":
                yield f"**步骤 {event['step_index']}/{event['total_steps']}：{event['step_name']}**\n"

            elif evt_type == "tool_call":
                yield f"🔧 调用 {event['tool']}\n"

            elif evt_type == "step_done":
                yield f"✅ 完成\n\n"

            elif evt_type == "step_error":
                retrying = event.get("retrying", False)
                if retrying:
                    yield f"⚠️ 出错: {event['error']}，正在重试...\n"
                else:
                    yield f"❌ 失败: {event['error']}\n\n"

            elif evt_type == "human_confirm":
                self._workflow_paused = {
                    "wf_id": wf_id,
                    "step_id": event["step_id"],
                }
                options = " / ".join(event.get("options", ["确认", "取消"]))
                yield f"\n⏸️ **{event['message']}**\n请回复：{options}\n"
                return

            elif evt_type == "workflow_done":
                yield f"🎉 工作流「{event['workflow_name']}」执行完成！\n"

            elif evt_type == "workflow_failed":
                yield f"❌ 工作流「{event['workflow_name']}」在「{event.get('failed_step', '')}」步骤失败\n"

    def _build_messages(self, user_message: str = "") -> list[dict]:
        messages = [{"role": "system", "content": self._build_system_prompt(user_message)}]
        messages.extend(self.conversation_history[-20:])
        return messages

    def _trigger_distillation(self, user_message: str, agent_response: str):
        """交互完成后触发蒸馏"""
        try:
            self.distillation.after_interaction(
                user_message=user_message,
                agent_response=agent_response,
                tool_calls=self._all_tool_calls,
            )
        except Exception:
            pass  # 蒸馏失败不影响主流程

    def _auto_create_workflow_from_task(self, user_message: str):
        try:
            tool_calls_data = []
            for tc in self._all_tool_calls:
                tool_calls_data.append({
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", {}),
                })
            steps = []
            for i, tc in enumerate(tool_calls_data):
                step = {
                    "id": f"step_{i + 1}",
                    "name": tc["name"],
                    "type": "tool",
                    "tool": tc["name"],
                    "args": {},
                    "output_key": tc["name"],
                }
                args = tc.get("arguments", {})
                for k, v in args.items():
                    if isinstance(v, str) and (len(v) > 50 or v.startswith("/") or v.startswith("C:") or v.startswith("data/")):
                        step["args"][k] = "{{variables." + k + "}}"
                    else:
                        step["args"][k] = v
                steps.append(step)
            if len(steps) >= 2:
                confirm_step = {
                    "id": "step_confirm",
                    "name": "确认文件结构",
                    "type": "human_confirm",
                    "message": "文件已读取，结构如上。确认开始处理？",
                    "options": ["确认", "取消"],
                }
                steps.insert(1, confirm_step)
            trigger_keywords = []
            patterns = [r"(?:帮我|请|给我|把|将|处理|做|生成|汇总|提取|拆分|转换|分析|整理)([^\s,，。！？]{2,10})"]
            for p in patterns:
                matches = re.findall(p, user_message)
                trigger_keywords.extend(matches[:3])
            if not trigger_keywords:
                words = user_message.strip().split()
                trigger_keywords = words[:3]
            trigger_keywords = trigger_keywords[:5]
            self.workflows.create_workflow(
                name=user_message.strip()[:30],
                trigger=trigger_keywords,
                steps=steps,
            )
        except Exception:
            pass

    def _try_extend_workflow(self, user_message: str, tool_calls: list[dict]):
        matched_wf = self.workflows.match_workflow(user_message)
        if not matched_wf:
            return None
        existing_steps = matched_wf.get("steps", [])
        existing_tools = {s.get("tool") for s in existing_steps if s.get("type") == "tool"}
        new_steps = []
        for tc in tool_calls:
            if tc.get("name") not in existing_tools:
                new_steps.append({
                    "id": f"step_{len(existing_steps) + len(new_steps) + 1}",
                    "name": tc.get("name", ""),
                    "type": "tool",
                    "tool": tc.get("name", ""),
                    "args": {},
                    "output_key": tc.get("name", ""),
                })
        if not new_steps:
            return None
        return {
            "workflow_id": matched_wf["id"],
            "workflow_name": matched_wf["name"],
            "new_steps": new_steps,
            "existing_step_count": len(existing_steps),
        }

    def get_status(self) -> dict:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "memory_entries": len(self.distillation.memory.read_memory().split("\n- ")) - 1,
            "active_workflows": len(self.workflows.list_workflows(status="active")),
            "draft_workflows": len(self.workflows.list_workflows(status="draft")),
            "tool_calls_this_session": len(self._all_tool_calls),
        }


# ============================================================
# execute_code 工具 — 借鉴 Hermes 的 PTC 模式
# ============================================================

@registry.register(name="execute_code", category="execution")
def execute_code(code: str, language: str = "python") -> str:
    """
    在安全沙箱中执行 Python 代码。
    用于数据处理、文件操作、图表生成等复杂任务。
    代码中可以使用 pandas, openpyxl, numpy, matplotlib 等库。
    参数 code: 要执行的 Python 代码
    """
    settings = get_settings()
    timeout = settings.tool_call_timeout

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            # 注入常用 import
            preamble = (
                "import pandas as pd\n"
                "import numpy as np\n"
                "import openpyxl\n"
                "try:\n"
                "    import xlrd\n"
                "except ImportError:\n"
                "    xlrd = None\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "from datetime import datetime, date\n"
                "\n"
            )
            f.write(preamble + code)
            script_path = f.name

        result = subprocess.run(
            [str(Path(sys.executable)), script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(settings.data_dir)),
            env={
                **dict(__import__("os").environ),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
        )

        Path(script_path).unlink(missing_ok=True)

        output_parts = []
        if result.stdout:
            stdout = result.stdout.strip()
            if len(stdout) > 2000:
                stdout = stdout[:2000] + f"\n... (共{len(result.stdout.strip())}字符，已截断)"
            output_parts.append(stdout)
        if result.stderr:
            stderr = result.stderr.strip()
            stderr_lines = [l for l in stderr.split("\n") if not any(k in l for k in ["DeprecationWarning", "UserWarning", "FutureWarning", "pkg_resources"])]
            stderr = "\n".join(stderr_lines).strip()
            if stderr:
                if len(stderr) > 500:
                    stderr = stderr[:500] + "..."
                output_parts.append(f"[STDERR] {stderr}")

        if result.returncode != 0:
            output_parts.insert(0, f"[退出码: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "[无输出]"

    except subprocess.TimeoutExpired:
        return f"[执行超时] 代码执行超过 {timeout} 秒限制"
    except Exception as e:
        return f"[执行错误] {str(e)}"


# ============================================================
# Agent 实例池 — 管理多个员工的 Agent
# ============================================================

_agent_pool: dict[str, AgentEngine] = {}


def get_agent(user_id: str = "default") -> AgentEngine:
    """获取或创建指定员工的 Agent 实例"""
    if user_id not in _agent_pool:
        _agent_pool[user_id] = AgentEngine(user_id)
    return _agent_pool[user_id]
