import sys
import json

sys.path.insert(0, r"c:\Users\houhuixin\Desktop\员工蒸馏与多员工对话")

from backend.automation.tools import get_flow_spec, submit_oa_flow

print("=" * 60)
print("FlowSpec v2.0 硬协议测试")
print("=" * 60)

# ========== 测试1: value_map硬翻译 ==========
print("\n--- 测试1: value_map硬翻译 ---")
print("用户说'领用'，应该翻译成'1'")
print("用户说'电子章'，应该翻译成'电子公章（仅限内部使用）'")

# IT资产领用 - 用户口语"领用"→ 硬翻译为 "1"
form_data = json.dumps({
    "extendDataFormInfo.value(fd_3f0f1b5a502104)": "领用",
    "extendDataFormInfo.value(fd_3f0edd65aaba44)": "外设领用",
    "extendDataFormInfo.value(fd_3f0edd16b8ca6a)": "因工作需要，领用一个鼠标"
}, ensure_ascii=False)

result = submit_oa_flow(
    site_url="http://oa.saiterobot.com:9080",
    template_id="19d41dab456469f7935fc3b432e80cec",
    form_data=form_data,
    submit=True
)
r = json.loads(result)
if r["status"] == "submitted":
    print(f"  ✅ 提交成功! 标题: {r['doc_subject']}")
    print(f"  耗时: {r['timing']['total_time']}s")
else:
    print(f"  结果: {r}")

# ========== 测试2: value_template自动包装 ==========
print("\n--- 测试2: value_template自动包装 ---")
print("用户输入纯文本，自动包装为<p>标签")

form_data2 = json.dumps({
    "extendDataFormInfo.value(fd_3a363236b3adda)": "因工作需要，申请一个键盘用于日常办公"
}, ensure_ascii=False)

result2 = submit_oa_flow(
    site_url="http://oa.saiterobot.com:9080",
    template_id="19e39f4b0061b217504b5da416a8e3c1",
    form_data=form_data2,
    submit=True
)
r2 = json.loads(result2)
if r2["status"] == "submitted":
    print(f"  ✅ 提交成功! 标题: {r2['doc_subject']}")
else:
    print(f"  结果: {r2}")

# ========== 测试3: get_flow_spec返回value_map ==========
print("\n--- 测试3: get_flow_spec返回value_map信息 ---")
spec_result = get_flow_spec("用章审批")
spec = json.loads(spec_result)
print(f"  状态: {spec['status']}")
for f in spec.get("fields_user_must_provide", []):
    if f.get("options"):
        print(f"  字段「{f['label']}」选项:")
        for opt in f["options"]:
            print(f"    - 用户说'{opt.get('label', '')}' → 提交值'{opt['value']}'")

print("\n" + "=" * 60)
print("测试完成!")
