import json
import asyncio
import time
import re
import concurrent.futures
from backend.tools.registry import registry
from backend.automation.models import AutomationStore, CredentialStore, SystemGuideStore
from backend.automation.recorder import Player
from backend.automation.ai_assistant import AIAssistant


_session_cache: dict = {}


def _run_async(fn, *args, **kwargs):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fn(*args, **kwargs))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(fn(*args, **kwargs)))
        return future.result()


@registry.register(name="scan_site_navigation", description="扫描网站的全站导航结构。自动登录（如有凭据），提取所有菜单、导航项、功能模块，生成站点地图。用于了解企业系统有哪些功能", category="automation")
def scan_site_navigation(url: str, max_depth: int = 1):
    result = _run_async(_do_scan_site, url, max_depth)
    return json.dumps(result, ensure_ascii=False)


async def _do_scan_site(url: str, max_depth: int) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "需要安装 playwright"}

    cred_store = CredentialStore()
    creds = cred_store.get_credentials(url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            current_url = page.url
            is_login_page = any(kw in current_url.lower() for kw in ["login", "signin", "登录"])

            if not is_login_page:
                is_login_page = await page.evaluate("""() => {
                    const pw = document.querySelector('input[type="password"]');
                    const hasLoginForm = pw && pw.offsetWidth > 0;
                    const title = (document.title || '').toLowerCase();
                    const bodyText = (document.body?.innerText || '').slice(0, 500).toLowerCase();
                    return hasLoginForm || title.includes('登录') || title.includes('login') || (bodyText.includes('登录') && bodyText.includes('密码'));
                }""")

            if is_login_page:
                if not creds:
                    login_page_info = await page.evaluate("""() => {
                        const result = {
                            title: document.title,
                            url: window.location.href,
                            frameworks: [],
                            login_fields: [],
                            buttons: [],
                        };
                        if (window.Vue || document.querySelector('[data-v-]')) result.frameworks.push('Vue');
                        if (window.React || document.querySelector('[data-reactroot]')) result.frameworks.push('React');
                        if (window.jQuery || window.$) result.frameworks.push('jQuery');
                        if (document.querySelector('.el-')) result.frameworks.push('Element UI');
                        if (document.querySelector('.ant-')) result.frameworks.push('Ant Design');
                        if (document.querySelector('.layui-')) result.frameworks.push('Layui');
                        if (document.querySelector('.km-')) result.frameworks.push('蓝凌KM');
                        document.querySelectorAll('input').forEach(el => {
                            result.login_fields.push({
                                tag: el.tagName.toLowerCase(),
                                type: el.type || '',
                                name: el.name || '',
                                id: el.id || '',
                                placeholder: el.getAttribute('placeholder') || '',
                                visible: el.offsetWidth > 0,
                            });
                        });
                        document.querySelectorAll('button, input[type="submit"]').forEach(el => {
                            result.buttons.push({
                                text: (el.textContent || el.value || '').trim(),
                                type: el.type || '',
                            });
                        });
                        return result;
                    }""")

                    ss_path = "data/automation/screenshots/site_nav_scan.png"
                    from pathlib import Path
                    Path(ss_path).parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=ss_path, full_page=True)

                    await context.close()
                    await browser.close()

                    return {
                        "status": "login_required",
                        "title": login_page_info.get("title", ""),
                        "url": login_page_info.get("url", current_url),
                        "frameworks": login_page_info.get("frameworks", []),
                        "logged_in": False,
                        "login_fields": login_page_info.get("login_fields", []),
                        "buttons": login_page_info.get("buttons", []),
                        "screenshot": ss_path,
                        "message": "该系统需要登录。请使用 save_site_credentials 工具保存登录凭据后重试。",
                    }

                login_fields = await page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input');
                    const result = {username: '', password: '', submit: '', submitSelector: ''};
                    for (const el of inputs) {
                        const t = (el.type || '').toLowerCase();
                        const n = (el.name || '').toLowerCase();
                        const c = (el.className || '').toLowerCase();
                        if (t === 'text' || n.includes('user') || n.includes('account') || c.includes('username')) {
                            result.username = el.name || el.id || '';
                        }
                        if (t === 'password' || c.includes('password')) {
                            result.password = el.name || el.id || '';
                        }
                        if (t === 'submit') {
                            result.submit = el.name || el.id || '';
                            result.submitSelector = el.name ? `[name="${el.name}"]` : el.id ? '#' + el.id : 'input[type="submit"]';
                        }
                    }
                    const btns = document.querySelectorAll('button, input[type="submit"], input[type="button"], a');
                    for (const b of btns) {
                        const text = (b.textContent || b.value || '').trim();
                        const href = b.getAttribute('href') || '';
                        if (text.includes('登录') || text.includes('登 录') || text.includes('Login') || text.includes('确定') || text.includes('Sign')) {
                            if (href.includes('javascript:') && href.includes('click')) {
                                result.submitSelector = href;
                            } else if (!result.submitSelector) {
                                result.submitSelector = b.id ? '#' + b.id : b.name ? `[name="${b.name}"]` : 'button';
                            }
                        }
                    }
                    return result;
                }""")

                if login_fields.get("username") and creds.get("username"):
                    u_sel = f'[name="{login_fields["username"]}"]' if login_fields["username"] else 'input[type="text"]'
                    p_sel = f'[name="{login_fields["password"]}"]' if login_fields["password"] else 'input[type="password"]'

                    try:
                        await page.fill(u_sel, creds["username"])
                    except Exception:
                        await page.evaluate(f"""() => {{
                            const el = document.querySelector('{u_sel}');
                            if (el) {{ el.value = '{creds["username"]}'; el.dispatchEvent(new Event('input', {{bubbles: true}})); }}
                        }}""")

                    try:
                        await page.fill(p_sel, creds["password"])
                    except Exception:
                        await page.evaluate(f"""() => {{
                            const el = document.querySelector('{p_sel}');
                            if (el) {{ el.value = '{creds["password"]}'; el.dispatchEvent(new Event('input', {{bubbles: true}})); }}
                        }}""")

                    await page.wait_for_timeout(500)

                    submit_sel = login_fields.get("submitSelector", "")

                    if submit_sel.startswith("javascript:"):
                        await page.evaluate(submit_sel)
                    elif submit_sel:
                        try:
                            await page.click(submit_sel, timeout=3000)
                        except Exception:
                            await page.evaluate(f"""() => {{
                                const el = document.querySelector('{submit_sel}');
                                if (el) el.click();
                            }}""")
                    else:
                        await page.keyboard.press("Enter")

                    await page.wait_for_timeout(5000)

                    if "/login" in page.url.lower() and "login" in (await page.title()).lower():
                        ss_path = "data/automation/screenshots/site_nav_scan.png"
                        from pathlib import Path
                        Path(ss_path).parent.mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=ss_path, full_page=True)
                        await context.close()
                        await browser.close()
                        return {"error": "登录失败，请检查账号密码是否正确", "login_url": page.url, "screenshot": ss_path}

            nav_data = await page.evaluate("""() => {
                const result = {
                    title: document.title,
                    url: window.location.href,
                    frameworks: [],
                    menus: [],
                    portalTabs: [],
                    widgets: [],
                    actionItems: [],
                    quickLinks: [],
                    formFields: [],
                    iframes: [],
                };

                if (window.Vue || document.querySelector('[data-v-]')) result.frameworks.push('Vue');
                if (window.React || document.querySelector('[data-reactroot]')) result.frameworks.push('React');
                if (window.jQuery || window.$) result.frameworks.push('jQuery');
                if (document.querySelector('.el-')) result.frameworks.push('Element UI');
                if (document.querySelector('.ant-')) result.frameworks.push('Ant Design');
                if (document.querySelector('.layui-')) result.frameworks.push('Layui');
                if (document.querySelector('.km-') || document.querySelector('.lui_')) result.frameworks.push('蓝凌KM');

                const menuSelectors = [
                    '.nav a', '.menu a', '.sidebar a', '.el-menu a',
                    '.ant-menu a', '.layui-nav a', '.km-nav a', '.km-menu a',
                    'nav a', '[role="menuitem"]', '[role="tab"]',
                    '.tab a', '.tabs a', 'a[href*="#"]',
                    '.panel-heading a', '.accordion a', '.tree a',
                    'li > a', 'dd > a',
                ];

                const seen = new Set();
                for (const sel of menuSelectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const text = el.textContent?.trim();
                        const href = el.getAttribute('href') || '';
                        if (text && text.length > 0 && text.length < 40 && !seen.has(text)) {
                            seen.add(text);
                            result.menus.push({ text, href: href.slice(0, 120) });
                        }
                    });
                }

                document.querySelectorAll('.lui_single_menu_header_menu_item_div, .lui_single_menu_header_menu_item_current').forEach(el => {
                    const text = el.textContent?.trim();
                    if (text && text.length < 30 && !seen.has('portal_' + text)) {
                        seen.add('portal_' + text);
                        result.portalTabs.push({ text, type: 'portal_tab' });
                    }
                });

                document.querySelectorAll('.lui_panel_navs_l, .lui_tabpanel_navs_l, .portlet-title, .widget-title').forEach(el => {
                    const text = el.textContent?.trim();
                    if (text && text.length < 40 && !seen.has('widget_' + text)) {
                        seen.add('widget_' + text);
                        result.widgets.push({ text, type: 'widget' });
                    }
                });

                document.querySelectorAll('[onclick], [data-url], [data-href]').forEach(el => {
                    const text = el.textContent?.trim()?.slice(0, 40);
                    const onclick = el.getAttribute('onclick') || '';
                    const dataUrl = el.getAttribute('data-url') || el.getAttribute('data-href') || '';
                    if (text && (onclick || dataUrl) && !seen.has('action_' + text)) {
                        seen.add('action_' + text);
                        const url = dataUrl || (onclick.match(/window\\.open\\(['"]([^'"]+)/)?.[1] || '');
                        if (url && !url.startsWith('javascript')) {
                            result.actionItems.push({ text, url: url.slice(0, 120), type: 'action' });
                        }
                    }
                });

                document.querySelectorAll('a').forEach(el => {
                    const text = el.textContent?.trim();
                    const href = el.getAttribute('href') || '';
                    if (text && text.length < 30 && href && !href.startsWith('javascript') && !seen.has('link_' + text)) {
                        seen.add('link_' + text);
                        result.quickLinks.push({ text, href: href.slice(0, 120) });
                    }
                });

                document.querySelectorAll('input, select, textarea').forEach((el, i) => {
                    if (i < 30) {
                        result.formFields.push({
                            tag: el.tagName.toLowerCase(),
                            type: el.type || '',
                            name: el.name || '',
                            id: el.id || '',
                            label: el.getAttribute('placeholder') || '',
                            visible: el.offsetWidth > 0,
                        });
                    }
                });

                document.querySelectorAll('iframe').forEach(el => {
                    result.iframes.push({ src: (el.src || '').slice(0, 100), id: el.id || '' });
                });

                return result;
            }""")

            ss_path = "data/automation/screenshots/site_nav_scan.png"
            from pathlib import Path
            Path(ss_path).parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=ss_path, full_page=True)

            await context.close()
            await browser.close()

            return {
                "status": "scanned",
                "title": nav_data.get("title", ""),
                "url": nav_data.get("url", url),
                "frameworks": nav_data.get("frameworks", []),
                "logged_in": "/login" not in nav_data.get("url", "").lower(),
                "menu_count": len(nav_data.get("menus", [])),
                "menus": nav_data.get("menus", [])[:50],
                "portal_tabs": nav_data.get("portalTabs", [])[:20],
                "widgets": nav_data.get("widgets", [])[:20],
                "action_items": nav_data.get("actionItems", [])[:30],
                "quick_links_count": len(nav_data.get("quickLinks", [])),
                "quick_links": nav_data.get("quickLinks", [])[:30],
                "form_fields_count": len(nav_data.get("formFields", [])),
                "form_fields": nav_data.get("formFields", []),
                "iframes": nav_data.get("iframes", []),
                "screenshot": ss_path,
            }

        except Exception as e:
            await context.close()
            await browser.close()
            return {"error": str(e)}


@registry.register(name="scan_form_page", description="扫描网页表单页面，返回所有可填写字段的详细信息（标签、类型、选项等）。用于填写前先了解页面结构", category="automation")
def scan_form_page(url: str):
    assistant = AIAssistant()
    result = _run_async(assistant.scan_page, url)
    if not result or "error" in result:
        return json.dumps(result or {"error": "扫描失败"}, ensure_ascii=False)

    fields = []
    for f in result.get("form_fields", []):
        if not f.get("visible") or f.get("readonly"):
            continue
        field_info = {
            "label": f.get("label", ""),
            "type": f.get("type", ""),
            "selector": f.get("selector", ""),
            "required": f.get("required", False),
            "placeholder": f.get("placeholder", ""),
        }
        if f.get("options"):
            field_info["options"] = [o.get("text", o.get("value", "")) for o in f["options"][:10]]
        fields.append(field_info)

    buttons = [b.get("text", "") for b in result.get("buttons", [])]

    return json.dumps({
        "status": "scanned",
        "page_title": result.get("title", ""),
        "url": result.get("url", url),
        "has_login_form": result.get("has_login_form", False),
        "fields_count": len(fields),
        "fields": fields,
        "buttons": buttons,
        "message": f"页面「{result.get('title', '')}」共有 {len(fields)} 个可填写字段。请提供数据来源和映射规则。"
    }, ensure_ascii=False)


@registry.register(name="list_automation_templates", description="列出所有已保存的自动化操作模板", category="automation")
def list_automation_templates():
    store = AutomationStore()
    templates = store.list_all()
    if not templates:
        return json.dumps({"message": "暂无自动化模板", "templates": []}, ensure_ascii=False)
    result = []
    for t in templates:
        steps = t.get("steps", [])
        data_fields = [s for s in steps if s.get("is_data_field")]
        has_login = bool(t.get("login_steps"))
        result.append({
            "id": t["id"],
            "name": t["name"],
            "url": t.get("url", ""),
            "has_login": has_login,
            "steps_count": len(steps),
            "data_fields_count": len(data_fields),
            "field_mapping_count": len(t.get("field_mapping", {})),
            "usage_count": t.get("usage_count", 0),
        })
    return json.dumps({"templates": result}, ensure_ascii=False)


@registry.register(name="auto_build_template", description="AI自动分析网页表单，生成自动化操作模板。提供URL和模板名称即可", category="automation")
def auto_build_template(url: str, name: str, data_sample: str = ""):
    assistant = AIAssistant()
    sample = None
    if data_sample:
        try:
            sample = json.loads(data_sample)
        except json.JSONDecodeError:
            sample = None

    result = _run_async(assistant.auto_build_template, name=name, url=url, data_sample=sample)
    if result and "error" not in result:
        steps = result.get("steps", [])
        variables = result.get("variables", {})
        field_names = [v.get("label", k) for k, v in variables.items()]
        return json.dumps({
            "status": "created",
            "template_id": result["id"],
            "template_name": result["name"],
            "steps_count": len(steps),
            "data_fields": field_names,
            "message": f"已生成操作模板「{result['name']}」，包含 {len(steps)} 个步骤，{len(field_names)} 个数据字段。"
        }, ensure_ascii=False)
    return json.dumps(result or {"error": "生成失败"}, ensure_ascii=False)


@registry.register(name="save_site_credentials", description="保存企业系统的登录凭据（账号密码），用于自动登录。只需保存一次", category="automation")
def save_site_credentials(site_url: str, username: str, password: str):
    cred_store = CredentialStore()
    cred_store.save_credentials(site_url, username, password)
    return json.dumps({
        "status": "saved",
        "site": site_url,
        "message": f"已保存 {site_url} 的登录凭据，后续自动填写时会自动登录"
    }, ensure_ascii=False)


@registry.register(name="set_field_mapping", description="设置数据源字段到目标系统字段的映射关系。当数据源列名和系统表单字段名不一致时使用", category="automation")
def set_field_mapping(template_id: str, mapping: str):
    store = AutomationStore()
    template_data = store.load(template_id)
    if not template_data:
        return json.dumps({"error": f"模板 {template_id} 不存在"}, ensure_ascii=False)

    try:
        parsed_mapping = json.loads(mapping) if isinstance(mapping, str) else mapping
    except json.JSONDecodeError:
        return json.dumps({"error": "mapping 必须是有效的 JSON，格式：{\"数据源列名\": \"目标字段选择器\"}"}, ensure_ascii=False)

    from backend.automation.models import ActionTemplate
    template = ActionTemplate(template_data)
    template.field_mapping = parsed_mapping
    saved = store.save(template)

    return json.dumps({
        "status": "saved",
        "template_id": template_id,
        "mapping_count": len(parsed_mapping),
        "mapping": parsed_mapping,
        "message": f"已设置 {len(parsed_mapping)} 个字段映射"
    }, ensure_ascii=False)


@registry.register(name="preview_automation", description="预览自动化填写：展示每个字段将填入什么值，不实际执行。用于确认映射是否正确", category="automation")
def preview_automation(template_id: str, data: str = "{}"):
    store = AutomationStore()
    template_data = store.load(template_id)
    if not template_data:
        return json.dumps({"error": f"模板 {template_id} 不存在"}, ensure_ascii=False)

    try:
        parsed_data = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "data 参数必须是有效的 JSON"}, ensure_ascii=False)

    player = Player()
    results = []

    async def _do_preview():
        async for event in player.play(template_id=template_id, data=parsed_data, preview_only=True):
            results.append(event)

    _run_async(_do_preview)

    preview_events = [r for r in results if r.get("type") == "preview"]
    if preview_events:
        return json.dumps({
            "status": "preview",
            "fields": preview_events[0]["fields"],
            "total_records": preview_events[0]["total_records"],
        }, ensure_ascii=False)
    return json.dumps({"error": "预览失败"}, ensure_ascii=False)


@registry.register(name="execute_automation", description="执行自动化操作，自动填写网页表单。支持单条和批量。提供模板ID和要填写的数据", category="automation")
def execute_automation(template_id: str, data: str = "{}", batch_data: str = "", visible: bool = True, slow_mo: int = 300, auto_submit: bool = True):
    store = AutomationStore()
    template_data = store.load(template_id)
    if not template_data:
        return json.dumps({"error": f"模板 {template_id} 不存在"}, ensure_ascii=False)

    try:
        parsed_data = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "data 参数必须是有效的 JSON"}, ensure_ascii=False)

    parsed_batch = None
    if batch_data:
        try:
            parsed_batch = json.loads(batch_data) if isinstance(batch_data, str) else batch_data
        except json.JSONDecodeError:
            return json.dumps({"error": "batch_data 参数必须是有效的 JSON 数组"}, ensure_ascii=False)

    from backend.automation.models import ActionTemplate
    template = ActionTemplate(template_data)
    template.submit_after_fill = auto_submit
    store.save(template)

    player = Player()
    results = []

    async def _do_execute():
        async for event in player.play(
            template_id=template_id,
            data=parsed_data,
            batch_data=parsed_batch,
            headless=not visible,
            slow_mo=slow_mo,
            verify_after_fill=True,
        ):
            results.append(event)

    _run_async(_do_execute)

    errors = [r for r in results if r.get("type") == "step_error"]
    done = [r for r in results if r.get("type") == "record_done"]
    mismatches = [r for r in results if r.get("type") == "verify_mismatch"]
    final = [r for r in results if r.get("type") in ("play_done", "play_failed")]
    cred_needed = [r for r in results if r.get("type") == "credentials_needed"]
    final_event = final[0] if final else {}

    if cred_needed:
        return json.dumps({
            "status": "credentials_needed",
            "url": cred_needed[0].get("url", ""),
            "message": "该系统需要登录，请先用 save_site_credentials 保存登录凭据"
        }, ensure_ascii=False)

    if final_event.get("type") == "play_done":
        return json.dumps({
            "status": "success",
            "template_name": template_data["name"],
            "records_completed": len(done),
            "mismatches": [{"record": m.get("record_index"), "fields": m.get("mismatches")} for m in mismatches],
            "errors": [{"step": e.get("step_id"), "error": e.get("error")} for e in errors],
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "status": "failed",
            "error": final_event.get("error", "未知错误"),
            "records_completed": len(done),
        }, ensure_ascii=False)


@registry.register(name="delete_automation_template", description="删除自动化操作模板", category="automation")
def delete_automation_template(template_id: str):
    store = AutomationStore()
    if store.delete(template_id):
        return json.dumps({"status": "deleted", "template_id": template_id}, ensure_ascii=False)
    return json.dumps({"error": f"模板 {template_id} 不存在"}, ensure_ascii=False)


@registry.register(name="generate_system_guide", description="深度扫描企业系统，自动生成系统使用文档。包含所有功能模块、入口URL、操作说明和关键词。生成后Agent可根据用户一句话精准定位入口", category="automation")
def generate_system_guide(site_url: str, site_name: str = ""):
    result = _run_async(_do_generate_guide, site_url, site_name)
    return json.dumps(result, ensure_ascii=False)


async def _do_generate_guide(site_url: str, site_name: str) -> dict:
    scan_result = await _do_scan_site(site_url, max_depth=1)

    if scan_result.get("status") == "login_required":
        return scan_result

    if "error" in scan_result and scan_result.get("status") != "scanned":
        return scan_result

    all_entries = []

    for tab in scan_result.get("portal_tabs", []):
        all_entries.append({
            "name": tab["text"],
            "type": "portal_tab",
            "url": "",
            "description": "",
            "keywords": [tab["text"]],
        })

    for widget in scan_result.get("widgets", []):
        all_entries.append({
            "name": widget["text"],
            "type": "widget",
            "url": "",
            "description": "",
            "keywords": [widget["text"]],
        })

    for item in scan_result.get("action_items", []):
        raw_url = item.get("url", "")
        full_url = raw_url if raw_url.startswith("http") else site_url.rstrip("/") + "/" + raw_url.lstrip("/")
        all_entries.append({
            "name": item["text"].split("\n")[0].strip()[:30],
            "type": "action",
            "url": full_url,
            "description": "",
            "keywords": [item["text"].split("\n")[0].strip()[:20]],
        })

    for link in scan_result.get("quick_links", []):
        raw_href = link.get("href", "")
        full_url = raw_href if raw_href.startswith("http") else site_url.rstrip("/") + "/" + raw_href.lstrip("/")
        all_entries.append({
            "name": link["text"][:30],
            "type": "link",
            "url": full_url,
            "description": "",
            "keywords": [link["text"][:20]],
        })

    for menu in scan_result.get("menus", []):
        all_entries.append({
            "name": menu["text"][:30],
            "type": "menu",
            "url": menu.get("href", ""),
            "description": "",
            "keywords": [menu["text"][:20]],
        })

    unique_entries = []
    seen_names = set()
    for entry in all_entries:
        norm_name = entry["name"].strip()
        if norm_name and norm_name not in seen_names and len(norm_name) > 1:
            seen_names.add(norm_name)
            unique_entries.append(entry)

    try:
        from backend.llm_client import chat_with_tools
        entries_desc = "\n".join(
            f"- {i+1}. 名称: {e['name']} | 类型: {e['type']} | URL: {e['url']}"
            for i, e in enumerate(unique_entries[:60])
        )

        prompt = f"""你是企业系统分析专家。根据以下扫描到的系统功能列表，为每个功能生成：
1. description: 一句话功能说明（10-30字）
2. keywords: 用户可能用来描述此功能的关键词列表（3-8个，包括同义词、简称、口语表达）

系统名称: {site_name or scan_result.get('title', '')}
技术框架: {', '.join(scan_result.get('frameworks', []))}

功能列表:
{entries_desc}

请输出JSON数组（不要markdown代码块）:
[
  {{
    "index": 1,
    "description": "功能说明",
    "keywords": ["关键词1", "关键词2", "简称", "口语表达"]
  }}
]

注意：
- keywords要覆盖员工日常口语，如"请假"对应"考勤/假期/休假/请假申请"
- description要具体，不要写"用于xxx功能"这种废话
- 只输出JSON，不要其他文字"""

        llm_result = chat_with_tools(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        content = llm_result.get("content", "")
        if content:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            try:
                ai_entries = json.loads(content.strip())
                for ai_entry in ai_entries:
                    idx = ai_entry.get("index", 0) - 1
                    if 0 <= idx < len(unique_entries):
                        unique_entries[idx]["description"] = ai_entry.get("description", "")
                        unique_entries[idx]["keywords"].extend(ai_entry.get("keywords", []))
                        unique_entries[idx]["keywords"] = list(set(unique_entries[idx]["keywords"]))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    guide_data = {
        "site_name": site_name or scan_result.get("title", "未命名系统"),
        "site_url": scan_result.get("url", site_url),
        "frameworks": scan_result.get("frameworks", []),
        "logged_in": scan_result.get("logged_in", False),
        "total_modules": len(unique_entries),
        "modules": unique_entries,
        "scan_summary": {
            "portal_tabs": len(scan_result.get("portal_tabs", [])),
            "widgets": len(scan_result.get("widgets", [])),
            "action_items": len(scan_result.get("action_items", [])),
            "quick_links": scan_result.get("quick_links_count", 0),
            "menus": scan_result.get("menu_count", 0),
        },
    }

    store = SystemGuideStore()
    existing = store.find_by_site(site_url)
    if existing:
        guide_data["id"] = existing["id"]
    saved = store.save(guide_data)
    guide_data["id"] = saved["id"]

    return {
        "status": "generated",
        "guide_id": guide_data["id"],
        "site_name": guide_data["site_name"],
        "site_url": guide_data["site_url"],
        "total_modules": len(unique_entries),
        "modules": [
            {
                "name": m["name"],
                "type": m["type"],
                "url": m["url"],
                "description": m["description"],
                "keywords": m["keywords"][:8],
            }
            for m in unique_entries
        ],
        "message": f"已生成「{guide_data['site_name']}」系统使用文档，共 {len(unique_entries)} 个功能模块。Agent现在可以根据用户指令精准定位入口。",
    }


@registry.register(name="query_system_guide", description="查询系统使用文档，根据用户意图匹配最合适的功能入口。返回入口名称、URL和操作说明", category="automation")
def query_system_guide(site_url: str, user_intent: str):
    store = SystemGuideStore()
    guide = store.find_by_site(site_url)

    if not guide:
        all_guides = store.list_all()
        if all_guides:
            guide = all_guides[0]
        else:
            return json.dumps({
                "status": "no_guide",
                "message": f"未找到 {site_url} 的系统文档，请先用 generate_system_guide 生成",
            }, ensure_ascii=False)

    user_intent_lower = user_intent.lower()
    intent_chars = set(user_intent_lower)

    scored = []
    for mod in guide.get("modules", []):
        score = 0
        name = mod.get("name", "").lower()
        desc = mod.get("description", "").lower()
        keywords = [k.lower() for k in mod.get("keywords", [])]

        if user_intent_lower == name:
            score += 100
        if user_intent_lower in name or name in user_intent_lower:
            score += 50
        if user_intent_lower in desc:
            score += 30
        for kw in keywords:
            if user_intent_lower == kw:
                score += 80
            if user_intent_lower in kw or kw in user_intent_lower:
                score += 40
            kw_chars = set(kw)
            overlap = len(intent_chars & kw_chars)
            if overlap > 0 and len(kw) > 1:
                score += overlap * 5

        if score > 0:
            scored.append((score, mod))

    scored.sort(key=lambda x: -x[0])

    top = [
        {
            "name": mod["name"],
            "type": mod["type"],
            "url": mod.get("url", ""),
            "description": mod.get("description", ""),
            "keywords": mod.get("keywords", [])[:5],
            "match_score": score,
        }
        for score, mod in scored[:5]
    ]

    if not top:
        return json.dumps({
            "status": "no_match",
            "site_name": guide.get("site_name", ""),
            "available_modules": [m["name"] for m in guide.get("modules", [])[:20]],
            "message": f"未找到与「{user_intent}」匹配的功能，以下是可用功能列表",
        }, ensure_ascii=False)

    return json.dumps({
        "status": "matched",
        "site_name": guide.get("site_name", ""),
        "site_url": guide.get("site_url", ""),
        "user_intent": user_intent,
        "best_match": top[0],
        "other_candidates": top[1:],
        "message": f"匹配到「{top[0]['name']}」— {top[0].get('description', '')}",
    }, ensure_ascii=False)


@registry.register(name="list_system_guides", description="列出所有已生成的系统使用文档", category="automation")
def list_system_guides():
    store = SystemGuideStore()
    guides = store.list_all()
    if not guides:
        return json.dumps({"message": "暂无系统文档", "guides": []}, ensure_ascii=False)
    result = []
    for g in guides:
        result.append({
            "id": g["id"],
            "site_name": g.get("site_name", ""),
            "site_url": g.get("site_url", ""),
            "total_modules": g.get("total_modules", 0),
            "frameworks": g.get("frameworks", []),
            "updated_at": g.get("updated_at", ""),
        })
    return json.dumps({"guides": result}, ensure_ascii=False)


async def _httpx_login(base_url: str, username: str, password: str) -> tuple:
    import httpx
    client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True)
    resp = await client.post(
        "/j_acegi_security_check",
        data={"j_username": username, "j_password": password, "j_redirectto": "", "btn_submit": "登录"},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{base_url}/"},
        follow_redirects=True,
    )
    logged_in = "j_username" not in resp.text
    if not logged_in:
        await client.aclose()
        return None, False
    cookies = dict(client.cookies)
    return (client, cookies), True


async def _get_or_create_session(base_url: str) -> tuple:
    import httpx
    global _session_cache
    cache_key = base_url.rstrip("/")
    cached = _session_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < 600:
        client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True, cookies=cached["cookies"])
        return client, True

    cred_store = CredentialStore()
    creds = cred_store.get_credentials(base_url)
    if not creds:
        return None, False

    result, ok = await _httpx_login(base_url, creds["username"], creds["password"])
    if not ok:
        return None, False

    _, cookies = result
    _session_cache[cache_key] = {"cookies": cookies, "ts": time.time()}
    client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True, cookies=cookies)
    return client, True


async def _do_submit_oa_flow(base_url: str, template_id: str, form_data: dict, submit: bool = True) -> dict:
    import httpx
    t_start = time.time()

    client, reused = await _get_or_create_session(base_url)
    if client is None:
        return {"error": "无法登录，请先使用 save_site_credentials 保存登录凭据"}

    try:
        t0 = time.time()
        resp = await client.post(
            "/km/review/km_review_main/kmReviewMain.do",
            data={"method": "add", "fdTemplateId": template_id, ".fdTemplate": template_id, "i.docTemplate": template_id, "s_css": "default"},
            follow_redirects=True,
        )
        html = resp.text
        create_time = time.time() - t0

        fd_id = ""
        m = re.search(r'name="fdId"\s+value="([0-9a-f]{32})"', html)
        if m:
            fd_id = m.group(1)

        if not fd_id:
            return {"error": "创建流程实例失败，无法获取fdId", "create_time": create_time}

        all_fields = {}
        for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', html):
            name = m.group(1)
            val_match = re.search(r'value="([^"]*)"', m.group(0))
            all_fields[name] = val_match.group(1) if val_match else ""
        for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>([^<]*)</textarea>', html):
            all_fields[m.group(1)] = m.group(2)

        submit_form = dict(all_fields)

        for key, val in form_data.items():
            if key.startswith("extendDataFormInfo.value("):
                submit_form[key] = val
            elif key == "docSubject":
                submit_form[key] = val
            elif key in submit_form:
                submit_form[key] = val
            else:
                submit_form[key] = val

        if submit:
            submit_form["docStatus"] = "20"
        else:
            submit_form["docStatus"] = "10"

        submit_form["fdUseWord"] = "false"
        submit_form["fdUseForm"] = "true"
        submit_form["fdSource"] = "0"
        submit_form["fdSignEnable"] = "false"
        submit_form["fdCanCircularize"] = "true"
        submit_form["fdFeedbackModify"] = "1"

        for key in ["btn_submit", "method"]:
            submit_form.pop(key, None)

        t0 = time.time()
        save_resp = await client.post(
            f"/km/review/km_review_main/kmReviewMain.do?method=save&fdTemplateId={template_id}&.fdTemplate={template_id}&i.docTemplate={template_id}&s_css=default",
            data=submit_form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": base_url,
                "Referer": f"{base_url}/km/review/km_review_main/kmReviewMain.do?method=add&fdTemplateId={template_id}",
            },
            follow_redirects=True,
        )
        submit_time = time.time() - t0

        success = "成功" in save_resp.text
        total_time = time.time() - t_start

        result = {
            "status": "submitted" if success else "failed",
            "fd_id": fd_id,
            "template_id": template_id,
            "doc_subject": submit_form.get("docSubject", ""),
            "doc_status": "已提交(审批中)" if submit and success else "草稿" if success else "失败",
            "timing": {
                "create_time": round(create_time, 3),
                "submit_time": round(submit_time, 3),
                "total_time": round(total_time, 3),
                "session_reused": reused,
            },
            "fields_count": len(submit_form),
        }

        if not success:
            err = re.search(r"操作失败[^<]*", save_resp.text)
            result["error"] = err.group() if err else "提交失败，未知原因"

        return result

    finally:
        await client.aclose()


@registry.register(name="submit_oa_flow", description="极速提交OA流程（绕过浏览器，直接API提交）。提供模板ID和表单数据即可，速度比浏览器方式快10-16倍。支持OA系统。会自动校验FlowSpec中的必填字段，缺少时返回追问提示", category="automation")
def submit_oa_flow(site_url: str, template_id: str, form_data: str, submit: bool = True):
    try:
        parsed_data = json.loads(form_data) if isinstance(form_data, str) else form_data
    except json.JSONDecodeError:
        return json.dumps({"error": "form_data 必须是有效的 JSON，格式：{\"字段名\": \"值\"}"}, ensure_ascii=False)

    specs_data = _load_flow_specs()
    flow_spec = None
    if specs_data:
        for name, spec in specs_data.get("flows", {}).items():
            if spec.get("template_id") == template_id:
                flow_spec = spec
                flow_spec["_name"] = name
                break

    if flow_spec:
        missing = []
        for field_key, field_def in flow_spec.get("fields", {}).items():
            if not field_def.get("required"):
                continue
            if field_def.get("auto_fill"):
                continue
            if not field_def.get("ask_user"):
                continue
            api_name = field_def.get("api_name", field_key)
            if api_name not in parsed_data or not parsed_data[api_name]:
                prompt = field_def.get("ask_prompt", f"请提供{field_def.get('label', field_key)}")
                missing.append({
                    "field_key": field_key,
                    "label": field_def.get("label", ""),
                    "api_name": api_name,
                    "type": field_def.get("type", ""),
                    "ask_prompt": prompt,
                })

        if missing:
            prompts = [m["ask_prompt"] for m in missing]
            return json.dumps({
                "status": "missing_required",
                "flow_name": flow_spec.get("_name", ""),
                "template_id": template_id,
                "missing_fields": missing,
                "ask_prompts": prompts,
                "message": f"流程「{flow_spec.get('_name', '')}」缺少 {len(missing)} 个必填字段，请追问用户：{'；'.join(prompts)}",
            }, ensure_ascii=False)

        for field_key, field_def in flow_spec.get("fields", {}).items():
            api_name = field_def.get("api_name", field_key)
            value_map = field_def.get("value_map", {})
            if value_map and api_name in parsed_data:
                raw_val = str(parsed_data[api_name])
                if raw_val in value_map:
                    parsed_data[api_name] = value_map[raw_val]
                else:
                    for alias, mapped in value_map.items():
                        if alias in raw_val or raw_val in alias:
                            parsed_data[api_name] = mapped
                            break

            value_template = field_def.get("value_template", "")
            if value_template and api_name in parsed_data:
                raw_val = parsed_data[api_name]
                if "{用户输入}" in value_template and "<p>" not in str(raw_val):
                    parsed_data[api_name] = value_template.replace("{用户输入}", str(raw_val))

            auto_rule = field_def.get("auto_rule", "")
            if field_def.get("auto_fill") and auto_rule and "docSubject" not in parsed_data:
                import re as _re
                rule_text = auto_rule
                for fk, fd in flow_spec.get("fields", {}).items():
                    fk_api = fd.get("api_name", fk)
                    placeholder = "{" + fd.get("label", fk) + "}"
                    if placeholder in rule_text and fk_api in parsed_data:
                        val = parsed_data[fk_api]
                        val = _re.sub(r'<[^>]+>', '', str(val)).strip()[:30]
                        rule_text = rule_text.replace(placeholder, val)
                remaining = _re.findall(r'\{[^}]+\}', rule_text)
                if not remaining:
                    parsed_data["docSubject"] = rule_text

    result = _run_async(_do_submit_oa_flow, site_url, template_id, parsed_data, submit)
    return json.dumps(result, ensure_ascii=False)


@registry.register(name="list_oa_flow_templates", description="列出OA系统中所有可用的流程模板。需要先有OA流程扫描数据（oa_flow_guide.json）", category="automation")
def list_oa_flow_templates(site_url: str = "", category: str = ""):
    from pathlib import Path
    settings = get_settings()
    guide_path = Path(settings.data_dir) / "automation" / "oa_flow_guide.json"
    if not guide_path.exists():
        return json.dumps({"error": "未找到OA流程扫描数据，请先扫描OA系统的发起流程页面"}, ensure_ascii=False)

    try:
        guide_data = json.loads(guide_path.read_text(encoding="utf-8"))
    except Exception:
        return json.dumps({"error": "OA流程数据读取失败"}, ensure_ascii=False)

    templates = []
    if isinstance(guide_data, list):
        templates = guide_data
    elif isinstance(guide_data, dict):
        first_val = next(iter(guide_data.values()), None)
        if isinstance(first_val, dict) and "template_id" in first_val:
            templates = list(guide_data.values())
        elif "templates" in guide_data:
            templates = guide_data["templates"]
        elif "flows" in guide_data:
            templates = guide_data["flows"]
        else:
            templates = [{"name": k, **v} if isinstance(v, dict) else {"name": k} for k, v in guide_data.items()]

    if not templates:
        return json.dumps({"error": "OA流程数据为空"}, ensure_ascii=False)

    if category:
        templates = [t for t in templates if category in t.get("category", "")]

    result = []
    for t in templates:
        if t.get("status") not in ("success", None):
            continue
        name = t.get("name", "")
        cat = t.get("category", "")
        if category and category not in cat and category not in name:
            continue
        result.append({
            "name": name,
            "category": cat,
            "template_id": t.get("template_id", ""),
            "create_url": t.get("create_url", ""),
            "required_fields": [f.get("label", f.get("name", "")).strip() for f in t.get("fields", []) if f.get("required")],
            "fields_count": len(t.get("fields", [])),
        })

    return json.dumps({
        "status": "ok",
        "total": len(result),
        "templates": result,
        "message": f"共 {len(result)} 个可用流程模板" + (f"，已筛选分类「{category}」" if category else ""),
    }, ensure_ascii=False)


def _load_flow_specs() -> dict:
    from pathlib import Path
    settings = get_settings()
    spec_path = Path(settings.data_dir) / "automation" / "oa_flow_specs.json"
    if not spec_path.exists():
        return {}
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_flow_spec(flow_name: str, specs_data: dict) -> dict:
    flows = specs_data.get("flows", {})
    if flow_name in flows:
        return flows[flow_name]
    best_match = None
    best_score = 0
    for name, spec in flows.items():
        score = 0
        if flow_name in name:
            score += 50
        if name in flow_name:
            score += 50
        keywords = spec.get("keywords", [])
        for kw in keywords:
            if flow_name == kw:
                score += 80
            if flow_name in kw or kw in flow_name:
                score += 40
        name_chars = set(name)
        query_chars = set(flow_name)
        overlap = len(name_chars & query_chars)
        if overlap > 0:
            score += overlap * 3
        if score > best_score:
            best_score = score
            best_match = spec
    if best_match and best_score >= 20:
        return best_match
    return {}


@registry.register(name="get_flow_spec", description="查询OA流程的填写规范。返回该流程需要用户填写哪些字段、哪些自动填充、每个字段的追问提示。Agent据此知道该问用户什么、不该问什么", category="automation")
def get_flow_spec(flow_name: str):
    specs_data = _load_flow_specs()
    if not specs_data:
        return json.dumps({"error": "未找到OA流程规范数据（oa_flow_specs.json）"}, ensure_ascii=False)

    spec = _find_flow_spec(flow_name, specs_data)
    if not spec:
        available = list(specs_data.get("flows", {}).keys())
        return json.dumps({
            "status": "not_found",
            "flow_name": flow_name,
            "available_flows": available,
            "message": f"未找到流程「{flow_name}」的规范，可用流程：{', '.join(available[:10])}",
        }, ensure_ascii=False)

    user_fields = []
    auto_fields = []
    for field_key, field_def in spec.get("fields", {}).items():
        field_info = {
            "key": field_key,
            "label": field_def.get("label", ""),
            "type": field_def.get("type", ""),
            "required": field_def.get("required", False),
            "description": field_def.get("description", ""),
        }
        if field_def.get("options"):
            field_info["options"] = field_def["options"]
        if field_def.get("ask_prompt"):
            field_info["ask_prompt"] = field_def["ask_prompt"]
        if field_def.get("validation"):
            field_info["validation"] = field_def["validation"]

        if field_def.get("ask_user"):
            user_fields.append(field_info)
        elif field_def.get("auto_fill"):
            auto_fields.append(field_info)

    missing_prompts = []
    for f in user_fields:
        if f.get("required"):
            prompt = f.get("ask_prompt", f"请提供{f['label']}")
            missing_prompts.append(prompt)

    return json.dumps({
        "status": "found",
        "flow_name": flow_name,
        "template_id": spec.get("template_id", ""),
        "category": spec.get("category", ""),
        "description": spec.get("description", ""),
        "keywords": spec.get("keywords", []),
        "user_prompt": spec.get("user_prompt", ""),
        "fields_user_must_provide": user_fields,
        "fields_auto_filled": auto_fields,
        "required_missing_prompts": missing_prompts,
        "submit_mode": spec.get("submit_mode", "save_docStatus_20"),
        "timing_estimate": spec.get("timing_estimate", "~1.2s"),
        "message": f"流程「{flow_name}」需要用户提供 {len(user_fields)} 个字段，{len(auto_fields)} 个字段自动填充"
                   + (f"。请追问用户：{'；'.join(missing_prompts)}" if missing_prompts else "，所有必填字段可自动填充"),
    }, ensure_ascii=False)


@registry.register(name="add_flow_spec", description="为OA流程添加填写规范。定义哪些字段需要用户填写、追问提示、校验规则等。确保Agent知道该问什么、不该问什么", category="automation")
def add_flow_spec(flow_name: str, spec_json: str):
    try:
        spec_data = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
    except json.JSONDecodeError:
        return json.dumps({"error": "spec_json 必须是有效的 JSON"}, ensure_ascii=False)

    specs = _load_flow_specs()
    if "flows" not in specs:
        specs["flows"] = {}

    specs["flows"][flow_name] = spec_data

    from pathlib import Path
    settings = get_settings()
    spec_path = Path(settings.data_dir) / "automation" / "oa_flow_specs.json"
    spec_path.write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")

    user_fields = [k for k, v in spec_data.get("fields", {}).items() if v.get("ask_user")]
    auto_fields = [k for k, v in spec_data.get("fields", {}).items() if v.get("auto_fill")]

    return json.dumps({
        "status": "saved",
        "flow_name": flow_name,
        "user_fields_count": len(user_fields),
        "auto_fields_count": len(auto_fields),
        "message": f"已保存流程「{flow_name}」的规范：{len(user_fields)} 个用户填写字段，{len(auto_fields)} 个自动填充字段",
    }, ensure_ascii=False)


def get_settings():
    from backend.config import get_settings as _gs
    return _gs()
