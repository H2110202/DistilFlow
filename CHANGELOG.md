# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-25

### Added
- **对话即建模** — 员工描述需求，AI 自动搭建可视化工作流
- **ReAct + 工作流双模式** — 首次任务自由探索，重复任务确定性执行
- **文档智能填充** — 数据源 + 模板，AI 自动识别提取并精准填入
- **企业系统 RPA** — Playwright 浏览器自动化 + JS 原生 setter 绕过框架拦截
- **极速 API 提交** — httpx 直接 POST，16x 提速（~1.2s vs 浏览器 19s）
- **FlowSpec v2.0 硬协议** — api_name 固定 + value_map 硬翻译 + required 校验，保证 100% 准确率
- **系统文档自动生成** — AI 深度扫描企业系统 → 结构化文档 → Agent 精准定位入口
- **员工知识蒸馏** — 偏好提取、纠正学习、流程记忆，日常对话中无感完成
- **技能模板系统** — 关键词匹配注入 Prompt，减少幻觉
- **DAG 工作流引擎** — 支持 condition/loop/parallel/sub_workflow/human_confirm/tool
- **工作流三层匹配** — 精确匹配 → 语义关联 → 新建
- **钉钉扫码登录** — OAuth2 + DTFrameLogin JS SDK
- **凭据加密存储** — XOR 0x5A + base64
- **消息回退机制 + 任务中断** — AbortController
- **模型配置持久化 + 热重载**
- **OpenAI 兼容 API** — /v1/chat/completions，支持 IDE 插件接入
- **Midnight 主题前端** — 暗色主题，工作流可视化
