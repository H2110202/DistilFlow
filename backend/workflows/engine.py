import json
import re
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, AsyncGenerator
from backend.config import get_settings
from backend.tools.registry import registry


class WorkflowEngine:
    def __init__(self, user_id: str = "default"):
        self.settings = get_settings()
        self.user_id = user_id
        self.workflows_dir = Path(self.settings.data_dir) / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self._context: dict = {}
        self._paused_at: Optional[str] = None
        self._paused_message: str = ""
        self._building_wf_id: Optional[str] = None

    def create_workflow(
        self,
        name: str,
        trigger: list[str],
        steps: Optional[list[dict]] = None,
        variables: Optional[dict] = None,
        source_sessions: Optional[list[str]] = None,
    ) -> dict:
        wf_id = f"wf_{uuid.uuid4().hex[:8]}"
        workflow = {
            "id": wf_id,
            "name": name,
            "trigger": trigger,
            "status": "building",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "created_from": "auto" if source_sessions else "manual",
            "source_sessions": source_sessions or [],
            "usage_count": 0,
            "success_count": 0,
            "steps": steps or [],
            "variables": variables or {},
        }
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        self._building_wf_id = wf_id
        return workflow

    def add_step(self, wf_id: str, step: dict) -> Optional[dict]:
        wf = self.get_workflow(wf_id)
        if not wf:
            return None
        steps = wf.get("steps", [])
        if "id" not in step:
            step["id"] = f"step_{len(steps) + 1}"
        steps.append(step)
        wf["steps"] = steps
        wf["updated_at"] = datetime.now().isoformat()
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return wf

    def update_step(self, wf_id: str, step_id: str, updates: dict) -> Optional[dict]:
        wf = self.get_workflow(wf_id)
        if not wf:
            return None
        for step in wf.get("steps", []):
            if step.get("id") == step_id:
                step.update(updates)
                break
        wf["updated_at"] = datetime.now().isoformat()
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return wf

    def remove_step(self, wf_id: str, step_id: str) -> Optional[dict]:
        wf = self.get_workflow(wf_id)
        if not wf:
            return None
        wf["steps"] = [s for s in wf.get("steps", []) if s.get("id") != step_id]
        wf["updated_at"] = datetime.now().isoformat()
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return wf

    def list_workflows(self, status: Optional[str] = None) -> list[dict]:
        workflows = []
        for f in self.workflows_dir.glob("*.json"):
            try:
                wf = json.loads(f.read_text(encoding="utf-8"))
                if status is None or wf.get("status") == status:
                    workflows.append(wf)
            except:
                pass
        return workflows

    def get_workflow(self, wf_id: str) -> Optional[dict]:
        path = self.workflows_dir / f"{wf_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_workflow(self, wf_id: str, updates: dict) -> Optional[dict]:
        wf = self.get_workflow(wf_id)
        if not wf:
            return None
        wf.update(updates)
        wf["updated_at"] = datetime.now().isoformat()
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return wf

    def delete_workflow(self, wf_id: str) -> bool:
        path = self.workflows_dir / f"{wf_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def activate_workflow(self, wf_id: str) -> Optional[dict]:
        return self.update_workflow(wf_id, {"status": "active"})

    def match_workflow(self, user_message: str) -> Optional[dict]:
        active_workflows = self.list_workflows(status="active")
        if not active_workflows:
            return None
        msg_lower = user_message.lower()
        best_match = None
        best_score = 0
        for wf in active_workflows:
            score = 0
            for keyword in wf.get("trigger", []):
                if keyword.lower() in msg_lower:
                    score += len(keyword)
            if score > best_score:
                best_score = score
                best_match = wf
        return best_match if best_score > 0 else None

    def find_related_workflows(self, user_message: str, tool_calls: Optional[list[str]] = None) -> list[dict]:
        active_workflows = self.list_workflows(status="active")
        if not active_workflows:
            return []
        msg_lower = user_message.lower()
        results = []
        for wf in active_workflows:
            score = 0
            for keyword in wf.get("trigger", []):
                if keyword.lower() in msg_lower:
                    score += len(keyword) * 2
            if tool_calls:
                wf_tools = [s.get("tool", "") for s in wf.get("steps", []) if s.get("type") == "tool"]
                overlap = set(tool_calls) & set(wf_tools)
                score += len(overlap) * 3
            if score >= 3:
                results.append({"workflow": wf, "score": score})
        results.sort(key=lambda x: x["score"], reverse=True)
        return [r["workflow"] for r in results[:3]]

    def extend_workflow(self, wf_id: str, new_steps: list[dict]) -> Optional[dict]:
        wf = self.get_workflow(wf_id)
        if not wf:
            return None
        existing_steps = wf.get("steps", [])
        existing_tools = {s.get("tool") for s in existing_steps if s.get("type") == "tool"}
        offset = len(existing_steps)
        for i, step in enumerate(new_steps):
            if step.get("type") == "tool" and step.get("tool") in existing_tools:
                continue
            if "id" not in step:
                step["id"] = f"step_{offset + i + 1}"
            existing_steps.append(step)
        wf["steps"] = existing_steps
        wf["updated_at"] = datetime.now().isoformat()
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return wf

    def _resolve_template(self, value, context: dict) -> str:
        if not isinstance(value, str):
            return value
        def replacer(match):
            key = match.group(1)
            parts = key.split(".")
            val = context
            for part in parts:
                if isinstance(val, dict):
                    val = val.get(part, match.group(0))
                else:
                    return match.group(0)
            if isinstance(val, (dict, list)):
                return json.dumps(val, ensure_ascii=False)
            return str(val)
        return re.sub(r"\{\{(\w+(?:\.\w+)*)\}\}", replacer, value)

    def _resolve_args(self, args: dict, context: dict) -> dict:
        resolved = {}
        for k, v in args.items():
            if isinstance(v, str):
                resolved[k] = self._resolve_template(v, context)
            elif isinstance(v, dict):
                resolved[k] = self._resolve_args(v, context)
            else:
                resolved[k] = v
        return resolved

    def _evaluate_condition(self, step: dict, context: dict) -> str:
        condition_expr = step.get("condition", "")
        if not condition_expr:
            return step.get("default_branch", "pass")
        try:
            evaluated = self._resolve_template(condition_expr, context)
            if evaluated in ("True", "true", "1", "yes"):
                return step.get("pass_branch", "next")
            return step.get("fail_branch", "next")
        except:
            return step.get("default_branch", "next")

    async def execute_workflow(
        self,
        wf_id: str,
        variables: Optional[dict] = None,
        resume_from: Optional[str] = None,
        confirm_response: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        wf = self.get_workflow(wf_id)
        if not wf:
            yield {"type": "error", "content": f"工作流 {wf_id} 不存在"}
            return

        self._context = {
            "variables": {**(wf.get("variables") or {}), **(variables or {})},
            "steps_output": {},
        }

        if resume_from and confirm_response:
            self._context["steps_output"][resume_from] = {"confirmed": True, "response": confirm_response}

        steps = wf.get("steps", [])
        start_idx = 0
        if resume_from:
            for i, step in enumerate(steps):
                if step.get("id") == resume_from:
                    start_idx = i + 1
                    break

        yield {"type": "workflow_start", "workflow_id": wf_id, "workflow_name": wf["name"], "total_steps": len(steps)}

        idx = start_idx
        loop_counts: dict[str, int] = {}
        while idx < len(steps):
            step = steps[idx]
            step_id = step.get("id", f"step_{idx}")
            step_name = step.get("name", f"步骤 {idx + 1}")
            step_type = step.get("type", "tool")

            yield {"type": "step_start", "step_id": step_id, "step_name": step_name, "step_index": idx + 1, "total_steps": len(steps)}

            if step_type == "human_confirm":
                message = self._resolve_template(step.get("message", "请确认是否继续？"), self._context)
                options = step.get("options", ["确认", "取消"])
                yield {"type": "human_confirm", "step_id": step_id, "message": message, "options": options}
                self._paused_at = step_id
                self._paused_message = message
                return

            elif step_type == "tool":
                tool_name = step.get("tool", "")
                tool_args = self._resolve_args(step.get("args", {}), self._context)
                yield {"type": "tool_call", "step_id": step_id, "tool": tool_name, "args": tool_args}
                result = registry.execute(tool_name, tool_args)
                output_key = step.get("output_key", step_id)
                try:
                    parsed = json.loads(result)
                except:
                    parsed = {"raw": result}
                self._context["steps_output"][output_key] = parsed
                if isinstance(parsed, dict) and parsed.get("error"):
                    retry = step.get("retry_on_error", False)
                    if retry:
                        yield {"type": "step_error", "step_id": step_id, "error": parsed["error"], "retrying": True}
                        result = registry.execute(tool_name, tool_args)
                        try:
                            parsed = json.loads(result)
                        except:
                            parsed = {"raw": result}
                        self._context["steps_output"][output_key] = parsed
                    if isinstance(parsed, dict) and parsed.get("error"):
                        yield {"type": "step_error", "step_id": step_id, "error": parsed["error"], "retrying": False}
                        yield {"type": "workflow_failed", "workflow_name": wf["name"], "failed_step": step_name}
                        return
                yield {"type": "step_done", "step_id": step_id, "output_key": output_key}

            elif step_type == "condition":
                branch = self._evaluate_condition(step, self._context)
                self._context["steps_output"][step_id] = {"branch": branch}
                yield {"type": "condition_result", "step_id": step_id, "branch": branch}
                if branch != "next" and branch in step:
                    target_id = step[branch]
                    for si, s in enumerate(steps):
                        if s.get("id") == target_id:
                            idx = si - 1
                            break
                yield {"type": "step_done", "step_id": step_id}

            elif step_type == "loop":
                loop_id = step_id
                loop_counts[loop_id] = loop_counts.get(loop_id, 0) + 1
                max_iterations = step.get("max_iterations", 5)
                loop_condition = step.get("loop_condition", "")
                loop_body_start = step.get("body_start", "")
                loop_body_end = step.get("body_end", "")

                if loop_counts[loop_id] > max_iterations:
                    yield {"type": "step_done", "step_id": step_id, "detail": "max_iterations_reached"}
                else:
                    should_continue = True
                    if loop_condition:
                        evaluated = self._resolve_template(loop_condition, self._context)
                        should_continue = evaluated in ("True", "true", "1", "yes")
                    if should_continue and loop_body_start:
                        for si, s in enumerate(steps):
                            if s.get("id") == loop_body_start:
                                idx = si - 1
                                break
                    yield {"type": "step_done", "step_id": step_id, "detail": f"iteration_{loop_counts[loop_id]}"}

            elif step_type == "parallel":
                parallel_steps = step.get("parallel_steps", [])
                if parallel_steps:
                    yield {"type": "parallel_start", "step_id": step_id, "branch_count": len(parallel_steps)}
                    results = {}
                    for ps in parallel_steps:
                        ps_tool = ps.get("tool", "")
                        ps_args = self._resolve_args(ps.get("args", {}), self._context)
                        ps_id = ps.get("id", ps_tool)
                        yield {"type": "tool_call", "step_id": ps_id, "tool": ps_tool, "args": ps_args}
                        r = registry.execute(ps_tool, ps_args)
                        try:
                            results[ps_id] = json.loads(r)
                        except:
                            results[ps_id] = {"raw": r}
                        yield {"type": "step_done", "step_id": ps_id}
                    self._context["steps_output"][step_id] = results
                    yield {"type": "parallel_done", "step_id": step_id}
                else:
                    yield {"type": "step_done", "step_id": step_id}

            elif step_type == "sub_workflow":
                sub_wf_id = step.get("workflow_id", "")
                sub_vars = self._resolve_args(step.get("variables", {}), self._context)
                yield {"type": "sub_workflow_start", "step_id": step_id, "sub_workflow_id": sub_wf_id}
                sub_outputs = {}
                async for event in self.execute_workflow(sub_wf_id, variables=sub_vars):
                    if event.get("type") == "workflow_done":
                        sub_outputs = event.get("outputs", {})
                    elif event.get("type") in ("step_start", "step_done", "tool_call"):
                        yield event
                self._context["steps_output"][step_id] = sub_outputs
                yield {"type": "sub_workflow_done", "step_id": step_id}

            idx += 1

        self._update_usage(wf_id, success=True)
        yield {"type": "workflow_done", "workflow_id": wf_id, "workflow_name": wf["name"], "outputs": {k: v for k, v in self._context.get("steps_output", {}).items() if not k.startswith("_")}}

    def _update_usage(self, wf_id: str, success: bool):
        wf = self.get_workflow(wf_id)
        if not wf:
            return
        wf["usage_count"] = wf.get("usage_count", 0) + 1
        if success:
            wf["success_count"] = wf.get("success_count", 0) + 1
        path = self.workflows_dir / f"{wf_id}.json"
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")

    def auto_generate_from_session(
        self,
        session_messages: list[dict],
        session_id: str,
    ) -> Optional[dict]:
        tool_calls = []
        for msg in session_messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    })
        if len(tool_calls) < 2:
            return None
        user_messages = [m for m in session_messages if m.get("role") == "user"]
        if not user_messages:
            return None
        first_user_msg = user_messages[0].get("content", "")
        steps = []
        for i, tc in enumerate(tool_calls):
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
        trigger_keywords = self._extract_trigger_keywords(first_user_msg)
        workflow = self.create_workflow(
            name=first_user_msg.strip()[:30],
            trigger=trigger_keywords,
            steps=steps,
            source_sessions=[session_id],
        )
        return workflow

    def _extract_trigger_keywords(self, user_msg: str) -> list[str]:
        keywords = []
        patterns = [
            r"(?:帮我|请|给我|把|将|处理|做|生成|汇总|提取|拆分|转换|分析|整理)([^\s,，。！？]{2,10})",
        ]
        for p in patterns:
            matches = re.findall(p, user_msg)
            keywords.extend(matches[:3])
        if not keywords:
            words = user_msg.strip().split()
            keywords = words[:3]
        return keywords[:5]
