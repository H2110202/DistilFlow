"""
蒸馏引擎 — 员工无感蒸馏的核心实现。
借鉴 Hermes 的四层记忆体系 + 自主 Skill 创建机制，
同时融合 mem0 的语义检索能力。

核心设计：
1. Memory 层：MEMORY.md(事实) + USER.md(画像) + 会话存档(情景)
2. Skill 层：自动创建 + 质量门禁 + 版本管理
3. 定期总结：自动聚合近期交互，提取模式
4. 工具汇总：将多个小技能合并为大工具
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from backend.config import get_settings
from backend.llm_client import chat


# ============================================================
# Memory Manager — 借鉴 Hermes 的 MEMORY.md / USER.md 模式
# ============================================================

class MemoryManager:
    """
    四层记忆管理：
    - MEMORY.md: 长期事实记忆（Agent 自动写入）
    - USER.md: 用户画像（偏好、风格、习惯）
    - 会话存档: 每次对话的摘要
    - 语义检索: 通过 mem0 做语义搜索（可选）
    """

    MEMORY_MAX_CHARS = 3000
    USER_MAX_CHARS = 2000

    def __init__(self, user_id: str = "default"):
        self.settings = get_settings()
        self.user_id = user_id
        self._ensure_files()

    def _ensure_files(self):
        self.settings.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.user_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.settings.memory_file.exists():
            self.settings.memory_file.write_text("# Agent 长期记忆\n\n", encoding="utf-8")
        if not self.settings.user_file.exists():
            self.settings.user_file.write_text(
                "# 用户画像\n\n## 基本信息\n- 姓名: \n- 部门: \n- 职位: \n\n## 偏好\n\n## 工作习惯\n\n",
                encoding="utf-8",
            )

    def read_memory(self) -> str:
        return self.settings.memory_file.read_text(encoding="utf-8")

    def read_user(self) -> str:
        return self.settings.user_file.read_text(encoding="utf-8")

    def add_memory(self, entry: str):
        """追加一条记忆，超过容量时自动策展"""
        current = self.read_memory()
        new_content = current.rstrip() + f"\n- {entry}\n"

        if len(new_content) > self.MEMORY_MAX_CHARS:
            new_content = self._curate_memory(new_content)

        self.settings.memory_file.write_text(new_content, encoding="utf-8")

    def replace_memory(self, old: str, new: str):
        """替换记忆中的某条内容"""
        current = self.read_memory()
        updated = current.replace(old, new)
        self.settings.memory_file.write_text(updated, encoding="utf-8")

    def update_user_field(self, field: str, value: str):
        """更新用户画像中的某个字段"""
        current = self.read_user()
        pattern = rf"({re.escape(field)}.*?)(?=\n##|\Z)"
        replacement = f"{field}: {value}\n"
        if re.search(pattern, current, re.DOTALL):
            updated = re.sub(pattern, replacement, current, flags=re.DOTALL)
        else:
            updated = current.rstrip() + f"\n{field}: {value}\n"
        self.settings.user_file.write_text(updated, encoding="utf-8")

    def _curate_memory(self, content: str) -> str:
        """记忆策展：超过容量时，用 LLM 压缩合并"""
        prompt = f"""你是一个记忆策展助手。以下内容是一个 AI Agent 的长期记忆文件，但已经超过了容量上限。
请保留最重要的信息，删除过时的、重复的、不重要的内容，合并相似条目。
保持 Markdown 格式，总字符数不超过 {self.MEMORY_MAX_CHARS - 500}。

当前记忆内容：
{content}

请输出策展后的记忆内容："""

        curated = chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=2000)
        return curated if curated else content[: self.MEMORY_MAX_CHARS]

    def get_context_for_prompt(self) -> str:
        """组装注入 system prompt 的记忆上下文"""
        parts = []
        memory = self.read_memory().strip()
        user = self.read_user().strip()
        if user and len(user) > 50:
            parts.append(f"<user_profile>\n{user}\n</user_profile>")
        if memory and len(memory) > 30:
            parts.append(f"<memory>\n{memory}\n</memory>")
        return "\n\n".join(parts)

    def save_session_summary(self, session_id: str, summary: str, tool_calls_count: int = 0):
        """保存会话摘要到存档"""
        session_dir = self.settings.sessions_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "tool_calls_count": tool_calls_count,
        }
        file_path = session_dir / f"{session_id}.json"
        file_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# Skill Manager — 借鉴 Hermes 的自主 Skill 创建 + 质量门禁
# ============================================================

class SkillManager:
    """
    技能管理器：
    - 自动创建：任务完成后，满足触发条件时自动沉淀为 Skill
    - 质量门禁：新 Skill 先为 draft，验证后变 active
    - 版本管理：每次 patch 记录 diff
    - 工具汇总：多个小 Skill 合并为大工具
    """

    SKILL_TEMPLATE = """---
name: {name}
version: {version}
status: {status}
created_by: auto
created_at: {created_at}
usage_count: 0
success_rate: 0.0
category: {category}
trigger: {trigger}
---

# {name}

## 触发条件
{trigger}

## 执行步骤
{steps}

## 注意事项
{notes}

## 验证方法
{validation}
"""

    def __init__(self, user_id: str = "default"):
        self.settings = get_settings()
        self.user_id = user_id
        self.skills_dir = self.settings.skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def create_skill(
        self,
        name: str,
        trigger: str,
        steps: str,
        notes: str = "",
        validation: str = "",
        category: str = "general",
    ) -> dict:
        """创建一个新 Skill（状态为 draft）"""
        safe_name = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)
        existing = list(self.skills_dir.glob(f"{safe_name}_v*.md"))
        version = len(existing) + 1

        content = self.SKILL_TEMPLATE.format(
            name=name,
            version=version,
            status="draft",
            created_at=datetime.now().isoformat(),
            category=category,
            trigger=trigger,
            steps=steps,
            notes=notes or "暂无",
            validation=validation or "手动验证",
        )

        file_path = self.skills_dir / f"{safe_name}_v{version}.md"
        file_path.write_text(content, encoding="utf-8")

        return {"name": name, "version": version, "status": "draft", "file": str(file_path)}

    def list_skills(self, status: Optional[str] = None) -> list[dict]:
        """列出所有技能"""
        skills = []
        for f in self.skills_dir.glob("*.md"):
            meta = self._parse_frontmatter(f)
            if status is None or meta.get("status") == status:
                meta["file"] = str(f)
                skills.append(meta)
        return skills

    def activate_skill(self, skill_file: str):
        """将 draft 技能激活为 active"""
        path = Path(skill_file)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        content = content.replace("status: draft", "status: active", 1)
        path.write_text(content, encoding="utf-8")

    def patch_skill(self, skill_file: str, old_text: str, new_text: str):
        """修补技能内容（借鉴 Hermes 的 patch 机制）"""
        path = Path(skill_file)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        # fuzzy match: 容忍空格差异
        old_normalized = re.sub(r"\s+", " ", old_text.strip())
        content_normalized = re.sub(r"\s+", " ", content)

        if old_normalized in content_normalized:
            # 找到原始位置并替换
            idx = content_normalized.find(old_normalized)
            start = _find_original_position(content, idx, old_text)
            if start >= 0:
                content = content[:start] + new_text + content[start + len(old_text):]
        else:
            content = content.replace(old_text, new_text)

        # 更新版本号
        content = re.sub(r"version: (\d+)", lambda m: f"version: {int(m.group(1)) + 1}", content, count=1)
        path.write_text(content, encoding="utf-8")

    def increment_usage(self, skill_file: str, success: bool):
        """记录技能使用情况"""
        path = Path(skill_file)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        meta = self._parse_frontmatter(path)
        count = int(meta.get("usage_count", 0)) + 1
        rate = float(meta.get("success_rate", 0.0))
        new_rate = (rate * (count - 1) + (1.0 if success else 0.0)) / count

        content = re.sub(r"usage_count: \d+", f"usage_count: {count}", content)
        content = re.sub(r"success_rate: [\d.]+", f"success_rate: {round(new_rate, 2)}", content)
        path.write_text(content, encoding="utf-8")

    def get_active_skills_context(self) -> str:
        """获取所有 active 技能的上下文（注入 prompt）"""
        skills = self.list_skills(status="active")
        if not skills:
            return ""
        parts = []
        for s in skills:
            path = Path(s["file"])
            content = path.read_text(encoding="utf-8")
            # 只取名称和步骤部分，不加载完整内容
            name = s.get("name", "unknown")
            trigger = s.get("trigger", "")
            parts.append(f"- 技能「{name}」：触发条件「{trigger}」")
        return "<available_skills>\n" + "\n".join(parts) + "\n</available_skills>"

    def consolidate_skills(self, skill_names: list[str], new_name: str, new_trigger: str) -> dict:
        """
        工具汇总：将多个小技能合并为一个大工具。
        这是用户要求的核心功能 — 把已开发好的工具功能汇总为一个大工具。
        """
        all_steps = []
        all_notes = []
        category = "consolidated"

        for name in skill_names:
            skills = self.list_skills()
            for s in skills:
                if s.get("name") == name:
                    path = Path(s["file"])
                    content = path.read_text(encoding="utf-8")
                    steps_match = re.search(r"## 执行步骤\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
                    notes_match = re.search(r"## 注意事项\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
                    if steps_match:
                        all_steps.append(f"### {name}\n{steps_match.group(1).strip()}")
                    if notes_match:
                        all_notes.append(f"### {name}\n{notes_match.group(1).strip()}")

        combined_steps = "\n\n".join(all_steps)
        combined_notes = "\n\n".join(all_notes)

        result = self.create_skill(
            name=new_name,
            trigger=new_trigger,
            steps=combined_steps,
            notes=combined_notes,
            validation="执行所有子技能的验证方法",
            category=category,
        )
        return result

    def _parse_frontmatter(self, file_path: Path) -> dict:
        """解析 Markdown frontmatter"""
        content = file_path.read_text(encoding="utf-8")
        meta = {}
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                frontmatter = content[3:end].strip()
                for line in frontmatter.split("\n"):
                    if ":" in line:
                        key, _, val = line.partition(":")
                        meta[key.strip()] = val.strip()
        return meta


# ============================================================
# Distillation Pipeline — 蒸馏触发判断 + 自动执行
# ============================================================

class DistillationPipeline:
    """
    蒸馏管线：在每次对话后自动判断是否触发蒸馏。
    借鉴 Hermes 的五个触发点：
    1. 员工提出需求偏好 → 更新 USER.md
    2. 员工纠正错误 → patch Skill 或创建规则
    3. 员工改变流程 → 更新 MEMORY.md
    4. 重复性任务再次出现 → 自动创建 Skill
    5. 定期自省 → 扫描近期活动，策展记忆
    """

    def __init__(self, user_id: str = "default"):
        self.memory = MemoryManager(user_id)
        self.skills = SkillManager(user_id)
        self._correction_count = 0
        self._task_count = 0
        self._recent_tasks: list[str] = []

    def after_interaction(self, user_message: str, agent_response: str, tool_calls: list[dict]):
        """每次交互后调用，判断是否触发蒸馏"""
        self._task_count += 1
        triggers = []

        # 触发点1: 检测偏好表达
        if self._detect_preference(user_message):
            pref = self._extract_preference(user_message, agent_response)
            if pref:
                self.memory.add_memory(f"[偏好] {pref}")
                triggers.append("preference")

        # 触发点2: 检测纠正
        if self._detect_correction(user_message):
            self._correction_count += 1
            correction = self._extract_correction(user_message, agent_response)
            if correction:
                self.memory.add_memory(f"[纠正] {correction}")
                # 闭环：纠正后自动 patch 最近使用的技能
                self._auto_patch_on_correction(correction, user_message)
                triggers.append("correction")

        # 触发点3: 检测流程变更
        if self._detect_workflow_change(user_message):
            workflow = self._extract_workflow(user_message, agent_response)
            if workflow:
                self.memory.add_memory(f"[流程] {workflow}")
                triggers.append("workflow")

        # 触发点4: 工具调用 ≥ 5 次 → 自动创建 Skill
        if len(tool_calls) >= 5:
            skill = self._auto_create_skill(user_message, tool_calls, agent_response)
            if skill:
                triggers.append("skill_created")

        # 触发点5: 每 15 次任务自省一次
        if self._task_count % 15 == 0:
            self._periodic_reflection()
            triggers.append("reflection")

        # 触发点6: 检测重复任务 → 自动生成工作流草稿
        self._recent_tasks.append(user_message)
        if len(self._recent_tasks) > 20:
            self._recent_tasks = self._recent_tasks[-20:]
        if self._detect_repeated_task(user_message):
            workflow_draft = self._auto_generate_workflow_draft(user_message, agent_response, tool_calls)
            if workflow_draft:
                triggers.append("workflow_draft")

        return triggers

    def _detect_repeated_task(self, current_msg: str) -> bool:
        """检测是否为重复任务 — 与近期任务语义相似"""
        if len(self._recent_tasks) < 3:
            return False
        similar_count = sum(
            1 for t in self._recent_tasks[:-1]
            if self._task_similarity(t, current_msg) > 0.7
        )
        return similar_count >= 2

    def _task_similarity(self, task_a: str, task_b: str) -> float:
        """简单的任务相似度计算（基于关键词重叠）"""
        words_a = set(re.findall(r"[\w\u4e00-\u9fff]+", task_a.lower()))
        words_b = set(re.findall(r"[\w\u4e00-\u9fff]+", task_b.lower()))
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    def _auto_generate_workflow_draft(self, user_msg: str, agent_resp: str, tool_calls: list[dict]) -> Optional[dict]:
        from backend.workflows.engine import WorkflowEngine
        wf_engine = WorkflowEngine(self.user_id)

        steps = []
        for i, tc in enumerate(tool_calls):
            step = {
                "id": f"step_{i + 1}",
                "name": tc.get("name", f"步骤{i + 1}"),
                "type": "tool",
                "tool": tc.get("name", ""),
                "args": {},
                "output_key": tc.get("name", f"output_{i + 1}"),
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

        trigger_keywords = self._extract_trigger_keywords(user_msg)

        workflow = wf_engine.create_workflow(
            name=user_msg.strip()[:30],
            trigger=trigger_keywords,
            steps=steps,
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

    def _detect_preference(self, msg: str) -> bool:
        keywords = ["我喜欢", "我习惯", "不要用", "用这个", "换成", "以后都这样", "记住", "默认"]
        return any(kw in msg for kw in keywords)

    def _detect_correction(self, msg: str) -> bool:
        keywords = ["不对", "不是这样", "改一下", "错了", "应该是", "不是", "别这样", "重新"]
        return any(kw in msg for kw in keywords)

    def _detect_workflow_change(self, msg: str) -> bool:
        keywords = ["先给", "先发", "抄送", "审批", "找谁", "问一下", "流程", "步骤"]
        return any(kw in msg for kw in keywords)

    def _extract_preference(self, user_msg: str, agent_resp: str) -> Optional[str]:
        prompt = f"""从以下对话中提取用户的偏好信息，用一句话概括。
只提取明确的偏好表达，不要推测。

用户说: {user_msg}
助手回复: {agent_resp}

偏好（一句话）:"""
        result = chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=200)
        return result.strip() if result and len(result.strip()) > 5 else None

    def _extract_correction(self, user_msg: str, agent_resp: str) -> Optional[str]:
        prompt = f"""用户纠正了助手的做法。提取纠正的核心内容，用一句话概括。

用户说: {user_msg}
助手之前的回复: {agent_resp}

纠正内容（一句话）:"""
        result = chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=200)
        return result.strip() if result and len(result.strip()) > 5 else None

    def _extract_workflow(self, user_msg: str, agent_resp: str) -> Optional[str]:
        prompt = f"""用户提到了工作流程或协作关系。提取关键信息，用一句话概括。

用户说: {user_msg}

流程信息（一句话）:"""
        result = chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=200)
        return result.strip() if result and len(result.strip()) > 5 else None

    def _auto_create_skill(self, user_msg: str, tool_calls: list[dict], agent_resp: str) -> Optional[dict]:
        """自动创建 Skill（借鉴 Hermes 的自主技能创建）"""
        prompt = f"""用户完成了一个复杂任务，使用了 {len(tool_calls)} 次工具调用。
请将这个任务提炼为一个可复用的技能。

用户需求: {user_msg}
工具调用: {json.dumps([tc.get('name', '') for tc in tool_calls], ensure_ascii=False)}

请输出以下格式的技能描述：
名称:
触发条件:
执行步骤:
注意事项:"""

        result = chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1000)
        if not result:
            return None

        parts = {}
        for key in ["名称", "触发条件", "执行步骤", "注意事项"]:
            match = re.search(rf"{key}[:：]\s*(.*?)(?=\n(?:名称|触发条件|执行步骤|注意事项)[:：]|\Z)", result, re.DOTALL)
            if match:
                parts[key] = match.group(1).strip()

        if not parts.get("名称") or not parts.get("执行步骤"):
            return None

        return self.skills.create_skill(
            name=parts["名称"],
            trigger=parts.get("触发条件", user_msg[:50]),
            steps=parts["执行步骤"],
            notes=parts.get("注意事项", ""),
        )

    def _periodic_reflection(self):
        """定期自省 — 借鉴 Hermes 的 Periodic Nudge"""
        memory = self.memory.read_memory()
        active_skills = self.skills.list_skills(status="active")
        draft_skills = self.skills.list_skills(status="draft")

        prompt = f"""你是一个自省助手。请回顾以下信息，判断：
1. 是否有可以合并的技能？
2. 是否有 draft 技能可以激活？
3. 记忆中是否有过时或矛盾的内容？

当前记忆:
{memory[:1000]}

活跃技能: {len(active_skills)} 个
草稿技能: {len(draft_skills)} 个

请给出具体建议（如果有）："""

        result = chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=500)
        if result and len(result.strip()) > 20:
            self.memory.add_memory(f"[自省] {result.strip()[:200]}")

    def generate_summary(self) -> str:
        """
        定期总结 — 把近期的工作内容和信息汇总。
        这是用户要求的核心功能：收集工作内容和信息，定期总结。
        """
        memory = self.memory.read_memory()
        skills = self.skills.list_skills()
        skill_names = [s.get("name", "") for s in skills]

        prompt = f"""请根据以下信息，生成一份工作总结：

长期记忆:
{memory[:2000]}

已掌握的技能: {', '.join(skill_names) if skill_names else '暂无'}

请输出：
1. 近期工作重点
2. 积累的关键知识
3. 需要注意的事项
4. 建议合并为一个大工具的技能组合（如果有多个相关小技能）"""

        result = chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=1500)

        summary_path = self.settings.summary_file
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        full_summary = f"# 工作总结 ({timestamp})\n\n{result}"

        existing = ""
        if summary_path.exists():
            existing = summary_path.read_text(encoding="utf-8")

        summary_path.write_text(full_summary + "\n\n---\n\n" + existing, encoding="utf-8")
        return result

    def _auto_patch_on_correction(self, correction: str, user_message: str):
        """
        纠正自动 patch 技能 — 自学习的关键闭环。
        当员工纠正了 Agent 的做法时，自动找到相关技能并修补。
        借鉴 Hermes 的 KEPA 机制：错误 → 分析原因 → 修补技能。
        """
        active_skills = self.skills.list_skills(status="active")
        if not active_skills:
            return

        # 找到最可能相关的技能
        skill_list = "\n".join(
            f"- {s.get('name', '')}: {s.get('trigger', '')}"
            for s in active_skills
        )

        prompt = f"""用户纠正了助手的行为。请判断这个纠正是否与某个已有技能相关。

纠正内容: {correction}
用户原始消息: {user_message}

已有技能:
{skill_list}

如果相关，返回技能名称；如果不相关，返回"无"。
技能名称:"""

        result = chat([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=50)
        if not result or "无" in result:
            return

        matched_name = result.strip()
        for s in active_skills:
            if s.get("name", "") == matched_name or matched_name in s.get("name", ""):
                # 生成修补内容
                patch_prompt = f"""基于用户的纠正，修补技能「{s.get('name')}」的注意事项部分。

当前纠正: {correction}

请输出一句话的注意事项补充（将被追加到技能的注意事项中）："""

                patch_result = chat(
                    [{"role": "user", "content": patch_prompt}],
                    temperature=0.2,
                    max_tokens=100,
                )
                if patch_result and len(patch_result.strip()) > 5:
                    # patch 技能：在注意事项末尾追加
                    path = Path(s["file"])
                    content = path.read_text(encoding="utf-8")
                    notes_match = re.search(r"## 注意事项\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
                    if notes_match:
                        old_notes = notes_match.group(1).strip()
                        new_notes = old_notes + f"\n- {patch_result.strip()}"
                        self.skills.patch_skill(s["file"], old_notes, new_notes)
                    else:
                        # 没有注意事项部分，追加
                        content = content.rstrip() + f"\n\n## 注意事项\n- {patch_result.strip()}\n"
                        path.write_text(content, encoding="utf-8")
                break


def _find_original_position(content: str, normalized_idx: int, old_text: str) -> int:
    """在原始内容中找到 fuzzy match 对应的位置"""
    # 简化实现：直接用原始文本搜索
    pos = content.find(old_text[:20])
    return pos if pos >= 0 else -1
