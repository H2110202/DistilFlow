import json
import re
from pathlib import Path
from typing import Optional
from backend.config import get_settings


class SkillTemplates:
    def __init__(self):
        self.settings = get_settings()
        self.templates_dir = Path(self.settings.data_dir) / "skill_templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()

    def _ensure_defaults(self):
        defaults = [_doc_fill_template(), _data_summary_template(), _file_convert_template()]
        for tpl in defaults:
            path = self.templates_dir / f"{tpl['id']}.json"
            if not path.exists():
                path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_templates(self) -> list[dict]:
        templates = []
        for f in self.templates_dir.glob("*.json"):
            try:
                templates.append(json.loads(f.read_text(encoding="utf-8")))
            except:
                pass
        return templates

    def match_template(self, user_message: str) -> Optional[dict]:
        msg_lower = user_message.lower()
        best = None
        best_score = 0
        for tpl in self.list_templates():
            score = 0
            for kw in tpl.get("trigger", []):
                if kw.lower() in msg_lower:
                    score += len(kw)
            if score > best_score:
                best_score = score
                best = tpl
        return best if best_score > 0 else None

    def get_template(self, template_id: str) -> Optional[dict]:
        path = self.templates_dir / f"{template_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def to_workflow(self, template: dict, user_message: str) -> dict:
        from backend.workflows.engine import WorkflowEngine
        engine = WorkflowEngine()
        wf = engine.create_workflow(
            name=template.get("name", user_message.strip()[:30]),
            trigger=template.get("trigger", []),
            steps=template.get("steps", []),
            variables=template.get("variables", {}),
        )
        return wf

    def get_injection_prompt(self, template: dict) -> str:
        steps_desc = []
        for s in template.get("steps", []):
            if s.get("type") == "tool":
                steps_desc.append(f"{s.get('id')}. 调用 {s.get('tool')} — {s.get('name', '')}")
            elif s.get("type") == "human_confirm":
                steps_desc.append(f"{s.get('id')}. 人工确认 — {s.get('message', '')}")
            elif s.get("type") == "condition":
                steps_desc.append(f"{s.get('id')}. 条件判断 — {s.get('name', '')}")
        rules = "\n".join(f"- {r}" for r in template.get("rules", []))
        return f"<skill>\n技能: {template.get('name', '')}\n描述: {template.get('description', '')}\n步骤:\n" + "\n".join(steps_desc) + f"\n规则:\n{rules}\n请严格按步骤执行，不要跳步。\n</skill>"


def _doc_fill_template() -> dict:
    return {
        "id": "doc_fill",
        "name": "文档智能填充",
        "description": "从数据来源文档中提取信息，精准填入输出模板",
        "trigger": ["填充", "填入", "填表", "录入", "整理到", "填入模板", "数据填入", "汇总到", "填到表里", "把数据填"],
        "rules": [
            "先理解再动手：先扫描和读取，搞清楚数据结构再匹配",
            "匹配不确定时必须问员工：不要猜",
            "填充前必须确认：展示匹配结果让员工确认后再写入",
            "保留原始模板格式：填充时不要破坏模板的格式和公式",
            "信息不足时用编号列表追问，一次问完",
        ],
        "steps": [
            {"id": "step_1", "name": "扫描数据来源", "type": "tool", "tool": "scan_folder", "args": {"folder_path": "{{variables.source_folder}}", "recursive": True}, "output_key": "scan_result"},
            {"id": "step_2", "name": "批量提取源数据", "type": "tool", "tool": "batch_extract", "args": {"folder_path": "{{variables.source_folder}}"}, "output_key": "source_data"},
            {"id": "step_3", "name": "分析输出模板", "type": "tool", "tool": "analyze_template", "args": {"file_path": "{{variables.template_path}}"}, "output_key": "template_fields"},
            {"id": "step_4", "name": "语义匹配", "type": "tool", "tool": "match_fields", "args": {}, "output_key": "match_result"},
            {"id": "step_5", "name": "确认匹配结果", "type": "human_confirm", "message": "匹配结果如上，确认填入？", "options": ["确认", "取消"]},
            {"id": "step_6", "name": "精准填充", "type": "tool", "tool": "fill_template", "args": {"template_path": "{{variables.template_path}}"}, "output_key": "fill_result"},
        ],
        "variables": {
            "source_folder": {"type": "string", "required": True, "description": "数据来源文件夹路径"},
            "template_path": {"type": "string", "required": True, "description": "输出模板文件路径"},
        },
    }


def _data_summary_template() -> dict:
    return {
        "id": "data_summary",
        "name": "数据汇总分析",
        "description": "读取数据文件，按指定维度汇总统计",
        "trigger": ["汇总", "统计", "分析", "合计", "总计", "求和", "平均值", "排名"],
        "rules": [
            "先读取文件汇报结构，等员工确认再处理",
            "汇总维度不明确时必须追问",
            "输出格式不明确时必须追问",
        ],
        "steps": [
            {"id": "step_1", "name": "读取数据文件", "type": "tool", "tool": "read_excel", "args": {"file_path": "{{variables.file_path}}"}, "output_key": "file_data"},
            {"id": "step_2", "name": "确认数据结构", "type": "human_confirm", "message": "文件结构如上，确认开始汇总？", "options": ["确认", "取消"]},
            {"id": "step_3", "name": "执行汇总", "type": "tool", "tool": "execute_code", "args": {}, "output_key": "summary_result"},
            {"id": "step_4", "name": "保存结果", "type": "tool", "tool": "save_file", "args": {}, "output_key": "save_result"},
        ],
        "variables": {
            "file_path": {"type": "string", "required": True, "description": "数据文件路径"},
        },
    }


def _file_convert_template() -> dict:
    return {
        "id": "file_convert",
        "name": "文件格式转换",
        "description": "将文件从一种格式转换为另一种格式",
        "trigger": ["转换", "转成", "变成", "导出为", "另存为", "格式转换"],
        "rules": [
            "先读取文件确认内容完整",
            "转换前确认目标格式",
        ],
        "steps": [
            {"id": "step_1", "name": "读取源文件", "type": "tool", "tool": "read_excel", "args": {"file_path": "{{variables.file_path}}"}, "output_key": "source_data"},
            {"id": "step_2", "name": "确认转换", "type": "human_confirm", "message": "文件已读取，确认转换格式？", "options": ["确认", "取消"]},
            {"id": "step_3", "name": "执行转换", "type": "tool", "tool": "execute_code", "args": {}, "output_key": "convert_result"},
            {"id": "step_4", "name": "保存结果", "type": "tool", "tool": "save_file", "args": {}, "output_key": "save_result"},
        ],
        "variables": {
            "file_path": {"type": "string", "required": True, "description": "源文件路径"},
        },
    }
