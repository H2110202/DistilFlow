import json
import asyncio
from typing import Optional
from backend.automation.models import ActionTemplate, AutomationStore
from backend.llm_client import chat_with_tools


class AIAssistant:
    def __init__(self):
        self.store = AutomationStore()

    async def scan_page(self, url: str) -> Optional[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"error": "需要安装 playwright"}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                page_info = await page.evaluate("""() => {
                    const result = {
                        title: document.title,
                        url: window.location.href,
                        has_login_form: false,
                        form_fields: [],
                        buttons: []
                    };

                    const inputs = document.querySelectorAll('input, select, textarea');
                    inputs.forEach((el, i) => {
                        const rect = el.getBoundingClientRect();
                        const label_text =
                            document.querySelector(`label[for="${el.id}"]`)?.textContent?.trim() ||
                            el.closest('.el-form-item, .ant-form-item, .form-group, .field')?.querySelector('label, .label, .field-label')?.textContent?.trim() ||
                            el.previousElementSibling?.textContent?.trim() ||
                            el.getAttribute('placeholder') ||
                            el.getAttribute('aria-label') ||
                            el.getAttribute('name') || '';

                        const selector = el.id ? '#' + el.id :
                            el.name ? `[name="${el.name}"]` :
                            el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
                            el.className && typeof el.className === 'string' ?
                                el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') :
                            el.tagName.toLowerCase() + ':eq(' + i + ')';

                        const field = {
                            index: i,
                            tag: el.tagName.toLowerCase(),
                            type: el.type || el.tagName.toLowerCase(),
                            selector: selector,
                            label: label_text.trim(),
                            name: el.name || '',
                            id: el.id || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            required: el.required || el.getAttribute('aria-required') === 'true' || false,
                            readonly: el.readOnly || el.disabled || false,
                            visible: rect.width > 0 && rect.height > 0,
                            position: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                            options: []
                        };

                        if (el.tagName === 'SELECT') {
                            field.options = Array.from(el.options).map(o => ({
                                value: o.value,
                                text: o.textContent.trim(),
                                selected: o.selected
                            }));
                        }

                        if (el.type === 'radio' || el.type === 'checkbox') {
                            const group = document.querySelectorAll(`[name="${el.name}"]`);
                            if (group.length > 1) {
                                field.options = Array.from(group).map(g => ({
                                    value: g.value,
                                    text: g.parentElement?.textContent?.trim() || g.value,
                                    checked: g.checked
                                }));
                            }
                        }

                        if (el.type === 'password' || label_text.includes('密码') || label_text.includes('password')) {
                            result.has_login_form = true;
                        }

                        result.form_fields.push(field);
                    });

                    const btns = document.querySelectorAll('button, input[type="submit"], input[type="button"], a.btn, .btn');
                    btns.forEach((el, i) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            result.buttons.push({
                                index: i,
                                text: (el.textContent || el.value || '').trim(),
                                selector: el.id ? '#' + el.id :
                                    el.className && typeof el.className === 'string' ?
                                        el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') :
                                    el.tagName.toLowerCase(),
                                type: el.type || el.tagName.toLowerCase(),
                                position: { x: Math.round(rect.x), y: Math.round(rect.y) }
                            });
                        }
                    });

                    return result;
                }""")

                ss_path = f"data/automation/screenshots/scan_{hash(url) % 100000}.png"
                from pathlib import Path
                Path(ss_path).parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=ss_path, full_page=True)
                page_info["screenshot"] = ss_path

                await context.close()
                await browser.close()
                return page_info

            except Exception as e:
                await context.close()
                await browser.close()
                return {"error": str(e)}

    async def auto_build_template(
        self,
        name: str,
        url: str,
        data_sample: Optional[dict] = None,
    ) -> Optional[dict]:
        page_info = await self.scan_page(url)
        if not page_info or "error" in page_info:
            return page_info

        form_fields = page_info.get("form_fields", [])
        visible_fields = [f for f in form_fields if f.get("visible") and not f.get("readonly")]
        if not visible_fields:
            return {"error": "未在页面中发现可填写的表单字段"}

        elements_desc = []
        for f in visible_fields:
            desc = f"- 字段{i+1}: 标签=\"{f['label']}\" 类型={f['type']} 选择器=\"{f['selector']}\""
            if f.get("required"):
                desc += " [必填]"
            if f.get("placeholder"):
                desc += f" placeholder=\"{f['placeholder']}\""
            if f.get("options"):
                opts = [f"{o['value']}={o['text']}" for o in f['options'][:8]]
                desc += f" 选项=[{', '.join(opts)}]"
            elements_desc.append(desc)

        buttons_desc = []
        for b in page_info.get("buttons", []):
            buttons_desc.append(f"- 按钮: \"{b['text']}\" 选择器=\"{b['selector']}\"")

        data_desc = ""
        if data_sample:
            data_desc = f"\n\n数据样例：\n{json.dumps(data_sample, ensure_ascii=False, indent=2)}"

        prompt = f"""分析以下网页表单，生成操作模板。

页面标题：{page_info.get('title', '')}
表单字段：
{chr(10).join(elements_desc)}

按钮：
{chr(10).join(buttons_desc)}
{data_desc}

请输出JSON（不要markdown代码块）：
{{
  "login_steps": [],
  "steps": [
    {{"action": "click_fill", "selector": "CSS选择器", "label": "字段中文名", "is_data_field": true, "value": "默认值"}},
    {{"action": "select", "selector": "CSS选择器", "label": "下拉字段", "is_data_field": true, "value": "默认选项值"}},
    {{"action": "click", "selector": "提交按钮选择器", "label": "提交", "is_data_field": false}}
  ],
  "variables": {{
    "变量名": {{"label": "中文名", "selector": "CSS选择器", "default_value": ""}}
  }},
  "field_mapping": {{
    "数据源列名": {{"selector": "CSS选择器", "label": "系统中文名"}}
  }}
}}

规则：
1. action类型：文本输入用click_fill（点击→清空→逐字输入），下拉用select，按钮用click
2. is_data_field: 需要填入数据的为true，按钮为false
3. selector优先用id，其次name，其次data-testid
4. field_mapping: 数据源列名到系统字段的映射，列名用中文（和Excel表头一致）
5. login_steps: 如果页面是登录页，生成登录步骤；否则留空数组
6. 最后一步是提交按钮"""

        result = chat_with_tools(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        content = result.get("content", "")
        if not content:
            return {"error": "AI 未能生成操作模板"}

        try:
            json_match = content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0]
            parsed = json.loads(json_match.strip())
        except json.JSONDecodeError:
            return {"error": "AI 输出格式错误", "raw": content[:500]}

        steps = parsed.get("steps", [])
        variables = parsed.get("variables", {})
        field_mapping = parsed.get("field_mapping", {})
        login_steps = parsed.get("login_steps", [])

        for i, step in enumerate(steps):
            if "id" not in step:
                step["id"] = f"s_{i + 1}"

        template = ActionTemplate({
            "name": name,
            "url": url,
            "login_url": url if login_steps else "",
            "steps": steps,
            "login_steps": login_steps,
            "variables": variables,
            "field_mapping": field_mapping,
        })
        saved = self.store.save(template)
        return saved
