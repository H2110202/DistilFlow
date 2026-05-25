"""
Chainlit 前端 — 员工对话式工作助手界面。
核心功能：
1. 聊天对话 — 员工描述需求，Agent 执行并流式回复
2. 文件上传 — 聊天框内拖拽/粘贴文件，自动传给 Agent
3. 快捷工具面板 — 一键触发常用操作
4. 技能管理 — 查看/激活/合并技能
5. 工作总结 — 一键生成定期总结
"""
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

import chainlit as cl

from backend.config import get_settings
from backend.agent import get_agent
from backend.tools.registry import registry
from backend.distillation.engine import SkillManager, DistillationPipeline


settings = get_settings()
UPLOAD_DIR = Path(settings.data_dir) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@cl.on_chat_start
async def on_chat_start():
    """会话开始时初始化"""
    agent = get_agent("default")
    status = agent.get_status()

    welcome = f"""👋 你好！我是你的智能工作助手。

我可以帮你：
- 📊 **处理 Excel** — 汇总、统计、清洗、生成图表
- 📄 **读取 PDF** — 提取文本、表格、关键信息
- 🖼️ **识别图片** — OCR 文字识别
- 🐍 **执行代码** — 任何 Python 能做的事
- 📝 **写文档** — 报告、总结、邮件

**直接告诉我你需要什么，或者拖拽文件到聊天框。**

当前状态：记忆 {status['memory_entries']} 条 | 技能 {status['active_skills']} 个
"""
    await cl.Message(content=welcome).send()

    # 设置快捷操作按钮
    actions = [
        cl.Action(name="summary", value="summary", label="📋 生成工作总结", description="汇总近期工作内容"),
        cl.Action(name="skills", value="skills", label="🛠 查看我的技能", description="查看已沉淀的技能"),
        cl.Action(name="consolidate", value="consolidate", label="🔗 合并技能为大工具", description="把多个小技能合并"),
        cl.Action(name="status", value="status", label="📊 查看蒸馏状态", description="查看学习进度"),
    ]
    await cl.Message(content="快捷操作：", actions=actions).send()


@cl.on_message
async def on_message(message: cl.Message):
    """处理用户消息"""
    agent = get_agent("default")

    # 处理文件上传
    uploaded_files = []
    if message.elements:
        for elem in message.elements:
            if hasattr(elem, "path") and elem.path:
                # 复制到上传目录
                file_name = getattr(elem, "name", Path(elem.path).name)
                dest = UPLOAD_DIR / file_name
                shutil.copy2(elem.path, dest)
                uploaded_files.append(str(dest))

    # 流式输出 Agent 回复
    msg = cl.Message(content="")
    await msg.send()

    full_response = ""
    async for chunk in agent.chat(message.content, files=uploaded_files if uploaded_files else None):
        full_response += chunk
        await msg.stream_token(chunk)

    await msg.update()


@cl.on_action
async def on_action(action: cl.Action):
    """处理快捷操作按钮"""
    agent = get_agent("default")

    if action.value == "summary":
        pipeline = DistillationPipeline("default")
        summary = pipeline.generate_summary()
        await cl.Message(content=f"📋 **工作总结**\n\n{summary}").send()

    elif action.value == "skills":
        skill_mgr = SkillManager("default")
        skills = skill_mgr.list_skills()
        if not skills:
            await cl.Message(content="🛠 暂无技能。多和我交互，我会自动沉淀技能！").send()
            return

        lines = ["🛠 **我的技能库**\n"]
        for s in skills:
            status_icon = "✅" if s.get("status") == "active" else "📝"
            name = s.get("name", "未命名")
            trigger = s.get("trigger", "")
            usage = s.get("usage_count", 0)
            rate = s.get("success_rate", 0)
            lines.append(f"{status_icon} **{name}** — 触发: {trigger} | 使用: {usage}次 | 成功率: {rate}")

        await cl.Message(content="\n".join(lines)).send()

    elif action.value == "consolidate":
        skill_mgr = SkillManager("default")
        skills = skill_mgr.list_skills(status="active")
        if len(skills) < 2:
            await cl.Message(content="🔗 至少需要 2 个活跃技能才能合并。继续使用，我会沉淀更多技能！").send()
            return

        skill_names = [s.get("name", "") for s in skills]
        lines = ["🔗 **可合并的技能**\n"]
        for i, name in enumerate(skill_names, 1):
            lines.append(f"{i}. {name}")
        lines.append("\n请告诉我：要把哪些技能合并？合并后叫什么名字？")
        lines.append("例如：把「销售汇总」和「月度统计」合并为「销售月报生成器」")

        await cl.Message(content="\n".join(lines)).send()

    elif action.value == "status":
        status = agent.get_status()
        pipeline = DistillationPipeline("default")
        memory = pipeline.memory.read_memory()

        lines = [
            "📊 **蒸馏状态**\n",
            f"- 用户: {status['user_id']}",
            f"- 会话: {status['session_id']}",
            f"- 记忆条目: {status['memory_entries']}",
            f"- 活跃技能: {status['active_skills']}",
            f"- 草稿技能: {status['draft_skills']}",
            f"- 本次会话工具调用: {status['tool_calls_this_session']}",
            f"\n**记忆摘要（前500字）:**\n{memory[:500]}",
        ]
        await cl.Message(content="\n".join(lines)).send()
