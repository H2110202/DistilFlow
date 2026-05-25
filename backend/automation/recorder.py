import json
import asyncio
from datetime import datetime
from typing import Optional, AsyncGenerator
from backend.automation.models import ActionTemplate, AutomationStore, CredentialStore


class Player:
    def __init__(self):
        self.store = AutomationStore()
        self.cred_store = CredentialStore()

    def _resolve_field_value(self, field_label: str, selector: str, variables: dict, data: dict, field_mapping: dict) -> str:
        if field_mapping:
            for source_key, target_info in field_mapping.items():
                if isinstance(target_info, str) and target_info == selector:
                    return str(data.get(source_key, ""))
                if isinstance(target_info, dict) and target_info.get("selector") == selector:
                    return str(data.get(source_key, data.get(target_info.get("label", ""), "")))
        for var_key, var_info in variables.items():
            if var_info.get("selector") == selector or var_info.get("label") == field_label:
                if var_key in data:
                    return str(data[var_key])
                if var_info.get("label", "") in data:
                    return str(data[var_info["label"]])
        return ""

    async def _do_login(self, page, login_steps: list, credentials: Optional[dict] = None):
        for step in login_steps:
            action = step.get("action", "")
            selector = step.get("selector", "")
            value = step.get("value", "")
            label = step.get("label", "")

            if action == "fill" and credentials:
                if any(kw in label.lower() for kw in ["用户名", "账号", "username", "account", "工号"]):
                    value = credentials.get("username", value)
                elif any(kw in label.lower() for kw in ["密码", "password", "口令"]):
                    value = credentials.get("password", value)

            if action == "fill" and selector:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=10000)
                await el.fill("")
                await el.fill(str(value))
            elif action == "click" and selector:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=10000)
                await el.click()
            elif action == "wait":
                await page.wait_for_timeout(int(value) if str(value).isdigit() else 1000)
            elif action == "press":
                await page.keyboard.press(value or "Enter")

        await page.wait_for_timeout(2000)

    async def _js_set_value(self, page, selector: str, value: str) -> dict:
        return await page.evaluate("""([selector, value]) => {
            const el = document.querySelector(selector);
            if (!el) return {ok: false, error: '元素不存在'};
            const tag = el.tagName.toLowerCase();
            const type = (el.type || '').toLowerCase();

            if (tag === 'select') {
                el.value = value;
                for (const opt of el.options) {
                    if (opt.value === value || opt.text.trim() === value) {
                        el.value = opt.value;
                        opt.selected = true;
                        break;
                    }
                }
                el.dispatchEvent(new Event('change', {bubbles: true}));
                const selected = el.options[el.selectedIndex];
                return {ok: true, actual: selected ? selected.text.trim() : el.value};
            }

            if (type === 'checkbox') {
                const shouldCheck = value === 'true' || value === '1' || value === '是';
                if (el.checked !== shouldCheck) el.click();
                return {ok: true, actual: String(el.checked)};
            }

            if (type === 'radio') {
                const group = document.querySelectorAll(`[name="${el.name}"]`);
                for (const r of group) {
                    if (r.value === value || r.parentElement?.textContent?.trim() === value) {
                        if (!r.checked) r.click();
                        return {ok: true, actual: r.value};
                    }
                }
                return {ok: false, error: '未找到匹配选项: ' + value};
            }

            const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
            const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

            if (nativeSetter) {
                nativeSetter.call(el, value);
            } else {
                el.value = value;
            }

            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));

            if (el.__vue__ || el._vModifiers !== undefined) {
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }

            const actualValue = el.value;
            return {ok: actualValue === value, actual: actualValue};
        }""", [selector, str(value)])

    async def _fill_one_record(self, page, steps: list, variables: dict, data: dict, field_mapping: dict) -> list[dict]:
        results = []
        for i, step in enumerate(steps):
            action = step.get("action", "")
            selector = step.get("selector", "")
            value = step.get("value", "")
            label = step.get("label", "")
            is_data_field = step.get("is_data_field", False)

            try:
                if action in ("fill", "click_fill", "type") and is_data_field:
                    fill_value = self._resolve_field_value(label, selector, variables, data, field_mapping) or value
                    if selector:
                        el = page.locator(selector).first
                        await el.wait_for(state="visible", timeout=5000)
                        await el.scroll_into_view_if_needed()
                        await page.wait_for_timeout(50)

                        js_result = await self._js_set_value(page, selector, str(fill_value))

                        if js_result.get("ok"):
                            results.append({"step": step.get("id"), "label": label, "value": fill_value, "actual": js_result.get("actual", ""), "status": "ok"})
                        else:
                            await el.click()
                            await page.wait_for_timeout(50)
                            await el.fill("")
                            await el.fill(str(fill_value))
                            await page.wait_for_timeout(100)
                            actual = await el.input_value() if await el.evaluate("el => el.tagName.toLowerCase()") != "select" else ""
                            status = "ok" if actual.strip() == str(fill_value).strip() else "fallback"
                            results.append({"step": step.get("id"), "label": label, "value": fill_value, "actual": actual or js_result.get("actual", ""), "status": status})

                elif action == "select" and is_data_field:
                    fill_value = self._resolve_field_value(label, selector, variables, data, field_mapping) or value
                    if selector:
                        el = page.locator(selector).first
                        await el.wait_for(state="visible", timeout=5000)
                        await el.scroll_into_view_if_needed()
                        await page.wait_for_timeout(50)

                        js_result = await self._js_set_value(page, selector, str(fill_value))

                        if js_result.get("ok"):
                            results.append({"step": step.get("id"), "label": label, "value": fill_value, "actual": js_result.get("actual", ""), "status": "ok"})
                        else:
                            await el.select_option(value=str(fill_value))
                            await el.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                            actual_text = await el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
                            results.append({"step": step.get("id"), "label": label, "value": fill_value, "actual": actual_text, "status": "fallback"})

                elif action == "click" and not is_data_field:
                    if selector:
                        el = page.locator(selector).first
                        await el.wait_for(state="visible", timeout=5000)
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        results.append({"step": step.get("id"), "label": label, "status": "clicked"})

                elif action == "wait":
                    await page.wait_for_timeout(int(value) if str(value).isdigit() else 1000)

                elif action == "press":
                    await page.keyboard.press(value or "Enter")

            except Exception as e:
                results.append({"step": step.get("id"), "label": label, "status": "error", "error": str(e)})

        return results

    async def play(
        self,
        template_id: str,
        data: Optional[dict] = None,
        batch_data: Optional[list[dict]] = None,
        headless: bool = False,
        slow_mo: int = 300,
        preview_only: bool = False,
        verify_after_fill: bool = True,
    ) -> AsyncGenerator[dict, None]:
        template_data = self.store.load(template_id)
        if not template_data:
            yield {"type": "error", "content": f"操作模板 {template_id} 不存在"}
            return

        template = ActionTemplate(template_data)
        variables = template.variables
        field_mapping = template.field_mapping
        login_steps = template.login_steps

        records = batch_data if batch_data else ([data] if data else [{}])

        if preview_only:
            preview = []
            for var_key, var_info in variables.items():
                label = var_info.get("label", var_key)
                selector = var_info.get("selector", "")
                sample_val = ""
                if records and records[0]:
                    sample_val = self._resolve_field_value(label, selector, variables, records[0], field_mapping)
                mapped_from = ""
                if field_mapping:
                    for src, tgt in field_mapping.items():
                        if (isinstance(tgt, str) and tgt == selector) or (isinstance(tgt, dict) and tgt.get("selector") == selector):
                            mapped_from = src
                preview.append({
                    "field_label": label,
                    "selector": selector,
                    "sample_value": sample_val,
                    "mapped_from": mapped_from or "（未映射）",
                })
            yield {"type": "preview", "fields": preview, "total_records": len(records)}
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            yield {"type": "error", "content": "需要安装 playwright"}
            return

        yield {"type": "play_start", "template_name": template.name, "url": template.url, "total_records": len(records)}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = await context.new_page()

            try:
                target_url = template.url
                if login_steps:
                    login_url = template.login_url or target_url
                    yield {"type": "navigating", "url": login_url, "purpose": "login"}
                    await page.goto(login_url, wait_until="networkidle", timeout=30000)
                    await page.wait_for_timeout(1000)

                    creds = self.cred_store.get_credentials(login_url)
                    if not creds:
                        yield {"type": "credentials_needed", "url": login_url}
                        return

                    yield {"type": "logging_in"}
                    await self._do_login(page, login_steps, creds)
                    yield {"type": "login_done"}

                    if target_url != login_url:
                        await page.goto(target_url, wait_until="networkidle", timeout=30000)
                        await page.wait_for_timeout(1000)
                else:
                    yield {"type": "navigating", "url": target_url}
                    await page.goto(target_url, wait_until="networkidle", timeout=30000)
                    await page.wait_for_timeout(1000)

                fill_steps = [s for s in template.steps if s.get("is_data_field") or s.get("action") == "click"]
                submit_step = None
                nav_steps = []
                for s in template.steps:
                    if s.get("action") == "click" and any(kw in s.get("label", "").lower() for kw in ["提交", "保存", "确定", "确认", "submit", "save"]):
                        submit_step = s
                    elif s.get("action") in ("navigate", "wait", "wait_for_selector", "press") and not s.get("is_data_field"):
                        nav_steps.append(s)

                for rec_idx, record in enumerate(records):
                    yield {"type": "record_start", "record_index": rec_idx + 1, "total_records": len(records)}

                    if rec_idx > 0:
                        await page.goto(target_url, wait_until="networkidle", timeout=30000)
                        await page.wait_for_timeout(1000)

                    for ns in nav_steps:
                        if ns.get("action") == "wait":
                            await page.wait_for_timeout(int(ns.get("value", "1000")))
                        elif ns.get("action") == "press":
                            await page.keyboard.press(ns.get("value", "Enter"))

                    fill_results = await self._fill_one_record(page, fill_steps, variables, record, field_mapping)

                    if verify_after_fill:
                        verify_results = await self._verify_filled(page, fill_steps, variables, record, field_mapping)
                        mismatches = [v for v in verify_results if v.get("status") == "mismatch"]
                        if mismatches:
                            yield {"type": "verify_mismatch", "record_index": rec_idx + 1, "mismatches": mismatches}

                    if submit_step and template.submit_after_fill:
                        await page.wait_for_timeout(500)
                        try:
                            sel = submit_step.get("selector", "")
                            if sel:
                                el = page.locator(sel).first
                                await el.wait_for(state="visible", timeout=5000)
                                await el.click()
                                await page.wait_for_timeout(2000)
                        except Exception as e:
                            fill_results.append({"step": "submit", "status": "error", "error": str(e)})

                    yield {"type": "record_done", "record_index": rec_idx + 1, "results": fill_results}

                yield {"type": "play_done", "template_name": template.name, "total_records": len(records)}
                self.store.increment_usage(template_id)

            except Exception as e:
                yield {"type": "play_failed", "error": str(e)}
            finally:
                await context.close()
                await browser.close()

    async def _verify_filled(self, page, steps, variables, data, field_mapping) -> list[dict]:
        results = []
        for step in steps:
            if not step.get("is_data_field"):
                continue
            selector = step.get("selector", "")
            label = step.get("label", "")
            expected = self._resolve_field_value(label, selector, variables, data, field_mapping)
            if not expected or not selector:
                continue
            try:
                el = page.locator(selector).first
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    actual = await el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
                else:
                    actual = await el.input_value()
                if actual.strip() != expected.strip():
                    results.append({"label": label, "expected": expected, "actual": actual, "status": "mismatch"})
                else:
                    results.append({"label": label, "expected": expected, "actual": actual, "status": "match"})
            except Exception:
                pass
        return results
