"""
输出验证层 — 跑通了≠对了。
三层验证：结构验证 → 数据验证 → 语义验证。
验证不通过触发重试或降级。
"""
import json
from pathlib import Path
from typing import Optional
from backend.llm_client import chat


class ValidationResult:
    def __init__(self, passed: bool, reason: str = "", retry_hint: str = ""):
        self.passed = passed
        self.reason = reason
        self.retry_hint = retry_hint


class OutputValidator:
    """
    三层验证：
    1. 结构验证（硬规则）— 文件可打开？行列数合理？
    2. 数据验证（规则引擎）— 汇总值在合理范围？行数对比？
    3. 语义验证（LLM辅助）— 结果从业务角度合理吗？
    """

    def validate(
        self,
        task_desc: str,
        output_path: Optional[str] = None,
        output_text: Optional[str] = None,
        input_row_count: int = 0,
    ) -> ValidationResult:
        if output_path:
            path = Path(output_path)
            if not path.exists():
                return ValidationResult(False, f"输出文件不存在: {output_path}", "重新生成输出文件")

            suffix = path.suffix.lower()

            if suffix in (".xlsx", ".xls"):
                result = self._validate_excel(path, input_row_count)
                if not result.passed:
                    return result

            elif suffix == ".pdf":
                result = self._validate_pdf(path)
                if not result.passed:
                    return result

        if output_text and len(output_text) > 100:
            result = self._semantic_validate(task_desc, output_text)
            if not result.passed:
                return result

        return ValidationResult(True, "验证通过")

    def _validate_excel(self, path: Path, input_row_count: int = 0) -> ValidationResult:
        """结构验证：Excel 文件"""
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            total_rows = sum(ws.max_row for ws in wb.worksheets if ws.max_row)

            if total_rows < 2:
                return ValidationResult(
                    False,
                    f"输出 Excel 数据过少 ({total_rows} 行)",
                    "检查数据源和筛选条件是否过于严格",
                )

            if input_row_count > 0 and total_rows > input_row_count * 2:
                return ValidationResult(
                    False,
                    f"输出行数 ({total_rows}) 远超输入行数 ({input_row_count})，可能存在数据膨胀",
                    "检查是否有重复数据或错误的合并操作",
                )

            wb.close()
            return ValidationResult(True, f"Excel 验证通过: {total_rows} 行")

        except Exception as e:
            return ValidationResult(False, f"Excel 文件损坏: {e}", "用更基础的方式重新生成")

    def _validate_pdf(self, path: Path) -> ValidationResult:
        """结构验证：PDF 文件"""
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) == 0:
                    return ValidationResult(False, "PDF 无页面", "重新生成")
            return ValidationResult(True, "PDF 验证通过")
        except Exception as e:
            return ValidationResult(False, f"PDF 文件损坏: {e}", "重新生成")

    def _semantic_validate(self, task_desc: str, output_text: str) -> ValidationResult:
        """语义验证：用 LLM 快速检查结果是否合理"""
        prompt = f"""快速判断以下任务结果是否合理。只需回答"合理"或"不合理+原因"。

任务: {task_desc[:200]}
结果摘要: {output_text[:500]}

判断:"""

        result = chat([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=100)
        if result and "不合理" in result:
            reason = result.replace("不合理", "").strip(":： ")
            return ValidationResult(False, f"语义验证不通过: {reason}", "检查任务理解和数据处理逻辑")

        return ValidationResult(True, "语义验证通过")


class GracefulDegradation:
    """
    优雅降级策略：
    L0 正常执行 → L1 简化重试 → L2 半自动 → L3 友好拒绝
    """

    def handle_failure(self, task_desc: str, attempt_history: list[dict]) -> dict:
        attempts = len(attempt_history)

        if attempts <= 1:
            return {
                "level": "L1_simplified_retry",
                "prompt_hint": (
                    f"上一次尝试失败了。请用最简单的方式重新实现，"
                    f"不要使用复杂操作，用最基本的 pandas 处理即可。"
                ),
            }
        elif attempts <= 2:
            return {
                "level": "L2_human_in_loop",
                "message_to_user": (
                    f"我尝试了 {attempts} 次自动处理，都遇到了问题。\n"
                    f"失败原因：{attempt_history[-1].get('error', '未知')}\n"
                    f"我已生成处理方案草稿，你可以确认后我再执行。"
                ),
            }
        else:
            return {
                "level": "L3_friendly_reject",
                "message_to_user": (
                    f"抱歉，我尝试了 {attempts} 次都未能完成。\n"
                    f"建议：\n"
                    f"1. 把需求拆成更小的步骤\n"
                    f"2. 先做一个目标格式的样例给我参考\n"
                    f"3. 如果紧急，建议手工操作一次，我会记住流程"
                ),
            }


validator = OutputValidator()
degradation = GracefulDegradation()
