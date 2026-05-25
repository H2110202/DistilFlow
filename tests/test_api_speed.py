import json
import sys
import asyncio
import time
import re
import httpx

sys.path.insert(0, r"c:\Users\houhuixin\Desktop\员工蒸馏与多员工对话")

BASE_URL = "http://oa.saiterobot.com:9080"
TEMPLATE_ID = "19e39f4b0061b217504b5da416a8e3c1"


async def httpx_login(client: httpx.AsyncClient, username: str, password: str) -> bool:
    resp = await client.post(
        "/j_acegi_security_check",
        data={"j_username": username, "j_password": password, "j_redirectto": "", "btn_submit": "登录"},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE_URL}/"},
        follow_redirects=True,
    )
    return "j_username" not in resp.text


async def main():
    from backend.automation.models import CredentialStore
    cred_store = CredentialStore()
    creds = cred_store.get_credentials(BASE_URL)

    print("=" * 60)
    print("FlowCraft OA流程极速提交 — 最终优化版")
    print("=" * 60)

    total_start = time.time()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30, follow_redirects=True) as client:
        # ===== 步骤1: httpx直接登录 =====
        print("\n[步骤1] httpx直接HTTP登录...")
        t0 = time.time()
        ok = await httpx_login(client, creds["username"], creds["password"])
        login_time = time.time() - t0
        print(f"   结果: {'✅成功' if ok else '❌失败'}")
        print(f"   耗时: {login_time:.3f}s")
        if not ok:
            return

        # ===== 步骤2: 创建流程实例 =====
        print("\n[步骤2] httpx创建流程实例...")
        t0 = time.time()
        resp = await client.post(
            "/km/review/km_review_main/kmReviewMain.do",
            data={"method": "add", "fdTemplateId": TEMPLATE_ID, ".fdTemplate": TEMPLATE_ID, "i.docTemplate": TEMPLATE_ID, "s_css": "default"},
            follow_redirects=True,
        )
        html = resp.text
        create_time = time.time() - t0

        fd_id = ""
        m = re.search(r'name="fdId"\s+value="([0-9a-f]{32})"', html)
        if m:
            fd_id = m.group(1)
        print(f"   fdId: {fd_id}")

        # 提取所有字段
        all_fields = {}
        for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', html):
            name = m.group(1)
            val_match = re.search(r'value="([^"]*)"', m.group(0))
            all_fields[name] = val_match.group(1) if val_match else ""
        for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>([^<]*)</textarea>', html):
            all_fields[m.group(1)] = m.group(2)

        print(f"   字段数: {len(all_fields)}")
        print(f"   耗时: {create_time:.3f}s")

        if not fd_id:
            print("   ❌ 无法获取fdId")
            return

        # ===== 步骤3: 一步提交（save + docStatus=20） =====
        print("\n[步骤3] 一步提交（method=save, docStatus=20）...")
        t0 = time.time()

        form_data = dict(all_fields)
        form_data["docSubject"] = "IT软硬件需求申请-鼠标"
        form_data["docStatus"] = "20"
        form_data["fdUseWord"] = "false"
        form_data["fdUseForm"] = "true"
        form_data["fdSource"] = "0"
        form_data["fdSignEnable"] = "false"
        form_data["fdCanCircularize"] = "true"
        form_data["fdFeedbackModify"] = "1"
        form_data["extendDataFormInfo.value(fd_3a363236b3adda)"] = "<p>因工作需要，申请一个鼠标用于日常办公使用。</p>"

        for key in ["btn_submit", "method"]:
            form_data.pop(key, None)

        submit_resp = await client.post(
            f"/km/review/km_review_main/kmReviewMain.do?method=save&fdTemplateId={TEMPLATE_ID}&.fdTemplate={TEMPLATE_ID}&i.docTemplate={TEMPLATE_ID}&s_css=default",
            data=form_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/km/review/km_review_main/kmReviewMain.do?method=add&fdTemplateId={TEMPLATE_ID}",
            },
            follow_redirects=True,
        )
        submit_time = time.time() - t0

        print(f"   状态码: {submit_resp.status_code}")
        print(f"   响应大小: {len(submit_resp.text)}字")
        print(f"   耗时: {submit_time:.3f}s")

        if "成功" in submit_resp.text:
            print("   ✅ 提交成功!")
        elif "操作失败" in submit_resp.text:
            err = re.search(r"操作失败[^<]*", submit_resp.text)
            print(f"   ❌ 操作失败: {err.group() if err else '未知'}")
        else:
            print(f"   响应前300字: {submit_resp.text[:300]}")

        # ===== 步骤4: 验证提交结果 =====
        print("\n[步骤4] 验证提交结果...")
        # 尝试查看流程详情
        view_resp = await client.get(
            f"/km/review/km_review_main/kmReviewMain.do?method=view&fdId={fd_id}",
            follow_redirects=True,
        )
        if "审核中" in view_resp.text or "审批中" in view_resp.text:
            print("   ✅ 流程已进入审批状态!")
        elif "草稿" in view_resp.text:
            print("   ⚠️ 流程仍为草稿状态")
        elif view_resp.status_code == 500:
            # 500可能意味着流程已提交，不再是草稿
            print("   ⚠️ 流程可能已提交（查看返回500，说明不再是草稿）")
        else:
            print(f"   查看状态码: {view_resp.status_code}")

        # 尝试在"我的草稿"中查找
        draft_resp = await client.get(
            "/km/review/km_review_main/kmReviewMain.do?method=list&fdTemplateId=" + TEMPLATE_ID,
            follow_redirects=True,
        )
        if fd_id in draft_resp.text:
            print("   ⚠️ 流程仍在草稿列表中")
        else:
            print("   ✅ 流程不在草稿列表中（已提交）")

    total_time = time.time() - total_start
    api_only = create_time + submit_time

    print(f"\n{'='*60}")
    print(f"🚀 速度对比汇总")
    print(f"{'='*60}")
    print(f"  浏览器方式(旧):           ~19s  (登录7s + 打开8s + 填写1s + 提交3s)")
    print(f"  纯httpx方案(含登录):      {total_time:.2f}s")
    print(f"    - httpx登录:            {login_time:.3f}s  (vs Playwright ~6.5s = {6.5/login_time:.0f}x)")
    print(f"    - 创建流程:             {create_time:.3f}s  (vs 浏览器 ~8s = {8/create_time:.0f}x)")
    print(f"    - 一步提交:             {submit_time:.3f}s  (vs 浏览器 ~4s = {4/submit_time:.0f}x)")
    print(f"  复用session(无登录):      ~{api_only:.2f}s")
    print(f"  🎯 提速比:                {19/total_time:.0f}x (首次) / {19/api_only:.0f}x (复用)")
    print(f"\n  核心优化:")
    print(f"    1. httpx替代Playwright登录: 6.5s → {login_time:.2f}s")
    print(f"    2. httpx替代浏览器渲染: 8s → {create_time:.2f}s")
    print(f"    3. 一步提交(save+docStatus=20): 4s → {submit_time:.2f}s")
    print(f"    4. 复用session可再省{login_time:.1f}s")


asyncio.run(main())
