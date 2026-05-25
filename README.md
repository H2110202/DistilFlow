<div align="center">

# 🔥 DistilFlow

**对话即建模 — 企业工作流自动化引擎**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen.svg)](CONTRIBUTING.md)

*员工描述需求 → AI追问清楚 → 自动搭建工作流 → 确认执行*
*首次任务自由探索，重复任务确定性执行*

[English](#english) · [功能特性](#-功能特性) · [快速开始](#-快速开始) · [架构设计](#-架构设计) · [FlowSpec协议](#-flowspec协议)

</div>

---

## ✨ 功能特性

### 🗣️ 对话即建模
- 员工用自然语言描述需求，AI自动理解并搭建可视化工作流
- 右侧面板实时显示DAG节点图，确认后一键执行
- 首次任务ReAct自由探索 → 自动生成工作流 → 重复任务确定性执行

### 📋 文档智能填充
- 提供数据来源文档 + 输出模板，AI自动识别提取信息并精准填入
- 支持Excel、PDF、Word多格式数据源
- 字段映射可视化，人工可校验

### 🏢 企业系统自动操作 (RPA)
- **极速API提交**：绕过浏览器渲染，直接HTTP POST提交表单（~1.2s vs 浏览器19s = **16x提速**）
- **浏览器自动化**：Playwright驱动，JS原生setter绕过React/Vue框架拦截
- **系统文档自动生成**：AI深度扫描企业系统 → 生成结构化文档 → Agent精准定位入口
- **FlowSpec硬协议**：字段映射+选项值+校验规则全部固化，保证100%准确率

### 🧠 员工知识蒸馏
- 偏好提取、纠正学习、流程记忆 — 在日常对话中无感完成
- 技能自动沉淀：工具调用≥5次的任务自动创建可复用技能
- 技能模板系统：按需注入Prompt，减少幻觉

### 🔐 企业级特性
- 钉钉扫码登录（OAuth2）
- 凭据加密存储
- 消息回退机制 + 任务中断
- 模型配置持久化 + 热重载

---

## 🚀 快速开始

### 前置要求
- Python 3.11+
- 一个LLM API Key（OpenAI / DeepSeek / OpenRouter / Qwen 均可）

### 安装

```bash
# 克隆项目
git clone https://github.com/H2110202/DistilFlow.git
cd distilflow

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 安装依赖
pip install -e .

# 安装Playwright浏览器（RPA功能需要）
playwright install chromium
```

### 配置

```bash
# 复制配置文件
cp .env.example .env

# 编辑 .env，填入你的 LLM API Key
# 至少填一个：
#   OPENAI_API_KEY=sk-xxx
#   OPENROUTER_API_KEY=sk-or-v1-xxx
```

### 启动

```bash
# Web模式（浏览器访问）
python start.py

# 桌面模式（pywebview窗口）
python desktop.py
```

浏览器打开 http://localhost:8000 即可使用。

---

## 🏗️ 架构设计

```
┌──────────────────────────────────────────────────────┐
│                   Frontend (HTML/CSS/JS)              │
│         Midnight主题 · 钉钉扫码 · 工作流可视化         │
├──────────────────────────────────────────────────────┤
│                    API Layer (FastAPI)                 │
│              WebSocket · REST · 文件上传               │
├──────────┬──────────┬───────────┬────────────────────┤
│  Agent   │ Workflow │Automation │    Distillation     │
│  Engine  │  Engine  │  Engine   │      Engine         │
│ (ReAct)  │  (DAG)  │ (RPA+API) │ (Memory+Skill)      │
├──────────┴──────────┴───────────┴────────────────────┤
│                  Tool Registry                         │
│  SmartExcel · SmartPDF · OCR · DocFiller · RPA · API  │
├──────────────────────────────────────────────────────┤
│                  LLM Client (httpx)                    │
│        OpenAI · DeepSeek · Qwen · Gemini · ...        │
└──────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 路径 | 说明 |
|------|------|------|
| Agent引擎 | `backend/agent.py` | ReAct循环 + 工具调用 + 系统提示词 |
| 工作流引擎 | `backend/workflows/engine.py` | DAG执行、三层匹配、工作流扩展 |
| 技能模板 | `backend/workflows/skill_templates.py` | 关键词匹配注入Prompt |
| 自动化引擎 | `backend/automation/` | RPA录制回放、API极速提交、系统文档 |
| 蒸馏引擎 | `backend/distillation/engine.py` | 记忆管理、技能沉淀、定期自省 |
| LLM客户端 | `backend/llm_client.py` | httpx直接HTTP调用，兼容reasoning_content |
| 工具注册 | `backend/tools/registry.py` | 统一工具注册与调用 |

---

## 📐 FlowSpec协议

FlowSpec是DistilFlow的核心创新——**表单交互硬协议**，保证OA/运维系统自动填写的100%准确率。

### 设计哲学

| 概念 | 类比 | 解决什么 |
|------|------|---------|
| Skill | 员工手册 | "我知道怎么做事" |
| Workflow | 作业指导书 | "按A→B→C顺序做" |
| **FlowSpec** | **填表模板** | **这个格子填这个，那个格子填那个，不许乱填** |

### 三层硬约束

1. **`api_name` 固定** — 字段映射永不变化
2. **`value_map` 硬翻译** — 用户说"领用"→ 一定提交"1"，LLM不猜测
3. **`required` + `ask_user` 校验** — 缺必填字段就拦截，宁可多问一句也不填错

### 标准流程

```
用户说"帮我申请一个鼠标"
        ↓
  get_flow_spec("IT申请")     ← 查规范，知道该问什么
        ↓
  追问: "请说明申请原因"        ← 缺必填字段，必须追问
        ↓
  用户回答后
        ↓
  submit_oa_flow()             ← value_map硬翻译 + 校验 → 极速提交
        ↓
  ~1.2s 完成（vs 浏览器19s）
```

### FlowSpec示例

```json
{
  "fields": {
    "category_type": {
      "api_name": "extendDataFormInfo.value(fd_3f0f1b5a502104)",
      "type": "radio",
      "required": true,
      "ask_user": true,
      "ask_prompt": "您是要领用还是归还？",
      "value_map": {
        "领用": "1",
        "归还": "2"
      }
    }
  }
}
```

---

## 🛠️ 工具链

| 工具 | 类别 | 说明 |
|------|------|------|
| `smart_excel` | 数据处理 | 智能Excel读取、字段提取、数据清洗 |
| `smart_pdf` | 数据处理 | PDF文本+表格提取 |
| `fill_document` | 文档填充 | 数据源→模板精准填充 |
| `scan_site_navigation` | 自动化 | 全站导航扫描 |
| `generate_system_guide` | 自动化 | AI深度扫描生成系统文档 |
| `query_system_guide` | 自动化 | 根据用户意图匹配功能入口 |
| `get_flow_spec` | 自动化 | 查询FlowSpec流程规范 |
| `submit_oa_flow` | 自动化 | 极速API提交OA流程（16x提速） |
| `add_flow_spec` | 自动化 | 为新流程添加FlowSpec规范 |
| `list_oa_flow_templates` | 自动化 | 列出可用OA流程模板 |
| `auto_build_template` | 自动化 | AI自动分析页面生成自动化模板 |
| `execute_automation` | 自动化 | 执行自动化操作 |
| `save_site_credentials` | 自动化 | 保存站点登录凭据 |

---

## 📁 项目结构

```
distilflow/
├── backend/
│   ├── agent.py              # Agent引擎（ReAct循环）
│   ├── api.py                # FastAPI接口层
│   ├── auth.py               # 钉钉OAuth2认证
│   ├── config.py             # 配置管理
│   ├── llm_client.py         # LLM客户端（httpx）
│   ├── automation/
│   │   ├── tools.py          # 自动化工具注册
│   │   ├── recorder.py       # Playwright RPA录制回放
│   │   ├── models.py         # 数据模型（凭据/文档/FlowSpec）
│   │   └── ai_assistant.py   # AI辅助分析
│   ├── distillation/
│   │   └── engine.py         # 知识蒸馏引擎
│   ├── tools/
│   │   ├── registry.py       # 工具注册中心
│   │   ├── doc_filler.py     # 文档智能填充
│   │   ├── excel_reader.py   # Excel处理
│   │   ├── pdf_reader.py     # PDF处理
│   │   └── validator.py      # 数据校验
│   └── workflows/
│       ├── engine.py         # DAG工作流引擎
│       └── skill_templates.py # 技能模板系统
├── frontend/
│   └── index.html            # 前端（Midnight主题）
├── data/                     # 运行时数据（gitignore）
│   ├── automation/           # 自动化数据
│   │   └── oa_flow_specs.json # FlowSpec协议文件
│   ├── skill_templates/      # 技能模板
│   └── workflows/            # 工作流定义
├── tests/                    # 测试
├── app.py                    # Web应用入口
├── desktop.py                # 桌面应用入口
├── start.py                  # 一键启动脚本
└── pyproject.toml            # 项目配置
```

---

## 🔧 配置说明

### 支持的LLM提供商

| 提供商 | 环境变量 | 默认Base URL |
|--------|---------|-------------|
| OpenAI | `OPENAI_API_KEY` | https://api.openai.com/v1 |
| DeepSeek | `OPENAI_API_KEY` + `LLM_PROVIDER=deepseek` | https://api.deepseek.com/v1 |
| OpenRouter | `OPENROUTER_API_KEY` | https://openrouter.ai/api/v1 |
| Qwen | `LLM_PROVIDER=qwen` | https://dashscope.aliyuncs.com |
| Gemini | `LLM_PROVIDER=gemini` | https://generativelanguage.googleapis.com |

### 钉钉登录（可选）

```env
DINGTALK_APP_KEY=your_app_key
DINGTALK_APP_SECRET=your_app_secret
DINGTALK_CORP_ID=your_corp_id
```

---

## 🤝 参与贡献

我们欢迎所有形式的贡献！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 发起 Pull Request

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

<div align="center">

**DistilFlow — 蒸馏员工智慧，流淌自动化之河**

</div>
