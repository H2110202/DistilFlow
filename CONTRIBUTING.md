# Contributing to DistilFlow

感谢你对 DistilFlow 的关注！我们欢迎所有形式的贡献。

## 🚀 快速开始

1. Fork 本仓库
2. 克隆到本地：`git clone https://github.com/YOUR_USERNAME/DistilFlow.git`
3. 创建虚拟环境：`python -m venv .venv && source .venv/bin/activate`
4. 安装开发依赖：`pip install -e ".[all]"`
5. 创建特性分支：`git checkout -b feature/amazing-feature`

## 🛠️ 开发指南

### 代码风格

- Python 3.11+，使用 type hints
- 遵循 PEP 8，行宽 120
- 函数/类添加 docstring
- 不添加多余注释，代码即文档

### 项目结构

```
backend/
├── agent.py              # Agent 引擎（ReAct 循环）
├── api.py                # FastAPI 接口层
├── config.py             # 配置管理
├── llm_client.py         # LLM 客户端
├── automation/           # RPA + API 自动化
├── distillation/         # 知识蒸馏引擎
├── tools/                # 工具注册与实现
└── workflows/            # DAG 工作流引擎
frontend/
└── index.html            # 前端（Midnight 主题）
```

### 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat: 添加新功能
fix: 修复 Bug
docs: 文档更新
refactor: 代码重构
perf: 性能优化
test: 测试相关
chore: 构建/工具变更
```

### 添加新工具

1. 在 `backend/tools/` 下创建工具文件
2. 使用 `@registry.register()` 装饰器注册
3. 在 `backend/agent.py` 系统提示词中添加使用说明

### 添加新 FlowSpec

1. 编辑 `data/automation/oa_flow_specs.json`
2. 遵循 FlowSpec v2.0 协议格式
3. 必须包含 `api_name`、`value_map`、`required` 字段

## 🧪 测试

```bash
# 运行测试
python -m pytest tests/

# FlowSpec 端到端测试
python tests/test_flow_spec.py

# API 速度基准测试
python tests/test_api_speed.py
```

## 📋 提交 PR

1. 确保代码通过 lint 检查
2. 确保相关测试通过
3. PR 描述清楚变更内容和原因
4. 关联相关 Issue（如有）

## 🐛 报告 Bug

请使用 [GitHub Issues](https://github.com/H2110202/DistilFlow/issues)，包含：

- 复现步骤
- 期望行为
- 实际行为
- 环境信息（Python 版本、操作系统）

## 💡 功能建议

同样使用 [GitHub Issues](https://github.com/H2110202/DistilFlow/issues)，描述：

- 使用场景
- 期望的行为
- 可能的实现思路（可选）

## 📜 许可证

提交代码即表示你同意在 MIT License 下授权。
