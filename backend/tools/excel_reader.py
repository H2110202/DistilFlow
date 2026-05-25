"""
Smart Excel Reader — LLM 友好的 Excel 预处理层。
核心策略：先清洗再交给 LLM，不让 LLM 直接面对脏数据。
使用 openpyxl + pandas，借鉴了 Docling 的分层解析思路。
"""
import json
from pathlib import Path
from datetime import datetime, date
from typing import Any, Optional
import pandas as pd
import openpyxl
from openpyxl.utils import get_column_letter
from backend.tools.registry import registry


def _is_empty_row(row: tuple) -> bool:
    return all(v is None or str(v).strip() == "" for v in row)


def _detect_header_row(raw_rows: list[tuple], max_scan: int = 15) -> int:
    best_row = 0
    best_score = 0
    scan_up_to = min(len(raw_rows), max_scan)

    for i in range(scan_up_to):
        row = raw_rows[i]
        non_empty = sum(1 for v in row if v is not None and str(v).strip() != "")
        total = max(len(row), 1)
        score = non_empty / total
        if score > best_score:
            best_score = score
            best_row = i

    return best_row if best_score > 0.4 else 0


def _infer_type(values: list) -> str:
    numeric = 0
    date_count = 0
    text_count = 0
    sample = [v for v in values if v is not None][:50]

    for v in sample:
        if isinstance(v, (int, float)):
            numeric += 1
        elif isinstance(v, (datetime, date)):
            date_count += 1
        elif isinstance(v, str):
            try:
                float(str(v).replace(",", "").replace(" ", ""))
                numeric += 1
            except ValueError:
                text_count += 1

    total = max(len(sample), 1)
    if numeric / total > 0.5:
        return "numeric"
    if date_count / total > 0.3:
        return "date"
    return "text"


def _serialize_value(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and (v != v):
        return None
    return v


def _fill_merged_cells(ws) -> None:
    merged_ranges = list(ws.merged_cells.ranges)
    for merged_range in merged_ranges:
        min_col = merged_range.min_col
        min_row = merged_range.min_row
        max_col = merged_range.max_col
        max_row = merged_range.max_row

        top_left_value = ws.cell(row=min_row, column=min_col).value
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                if row != min_row or col != min_col:
                    ws.cell(row=row, column=col).value = top_left_value
        ws.unmerge_cells(str(merged_range))


def _self_check(all_results: dict) -> list[str]:
    warnings = []
    for sname, data in all_results.items():
        if isinstance(data, dict) and "error" in data:
            continue
        headers = data.get("headers", [])
        row_count = data.get("row_count", 0)
        type_map = data.get("type_map", {})

        generic_headers = [h for h in headers if h.startswith("列")]
        if len(generic_headers) > len(headers) * 0.5:
            warnings.append(f"工作表「{sname}」：超过一半的列名为自动生成，表头检测可能不准确")

        for h, t in type_map.items():
            if t == "numeric":
                data_rows = data.get("data", [])
                non_null = [r for r in data_rows if r.get(h) is not None]
                if len(non_null) > 0:
                    vals = []
                    for r in non_null[:30]:
                        try:
                            vals.append(float(str(r[h]).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    if vals:
                        mean_val = sum(vals) / len(vals)
                        if mean_val != 0 and any(abs(v - mean_val) > abs(mean_val) * 100 for v in vals):
                            warnings.append(f"工作表「{sname}」列「{h}」：存在极端异常值，可能包含格式错误")

        if row_count > 0 and len(data.get("data", [])) == 0:
            warnings.append(f"工作表「{sname}」：有 {row_count} 行数据但未返回任何行，读取可能不完整")

    return warnings


@registry.register(name="read_excel", category="file")
def read_excel(file_path: str, sheet_name: Optional[str] = None, max_rows: int = 500) -> str:
    """
    智能读取 Excel 文件（支持 .xlsx 和 .xls）。
    自动检测表头行、推断列类型、跳过空行、处理合并单元格。
    返回 JSON：包含清洗后的数据和元信息。
    参数 file_path: Excel 文件路径
    参数 sheet_name: 指定工作表名，不传则读取所有工作表
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    is_xls = path.suffix.lower() == ".xls"

    try:
        if is_xls:
            try:
                import xlrd
            except ImportError:
                return json.dumps({"error": "读取 .xls 文件需要 xlrd 库，请联系管理员安装"}, ensure_ascii=False)
            wb_xlrd = xlrd.open_workbook(str(path))
            sheets_to_read = [sheet_name] if sheet_name else wb_xlrd.sheet_names()
            all_results = {}
            for sname in sheets_to_read:
                if sname not in wb_xlrd.sheet_names():
                    continue
                ws = wb_xlrd.sheet_by_name(sname)
                raw = []
                for row_idx in range(ws.nrows):
                    raw.append(tuple(ws.cell_value(row_idx, col_idx) for col_idx in range(ws.ncols)))
                if not raw:
                    all_results[sname] = {"headers": [], "data": [], "row_count": 0, "type_map": {}, "warnings": ["空工作表"]}
                    continue
                header_idx = _detect_header_row(raw)
                warnings = []
                if header_idx > 0:
                    warnings.append(f"跳过了前 {header_idx} 行（非数据行）")
                if len(raw) > max_rows:
                    warnings.append(f"数据行数超过上限，仅展示前 {max_rows} 行")
                clean = raw[header_idx:]
                clean = [row for row in clean if not _is_empty_row(row)]
                if not clean:
                    all_results[sname] = {"headers": [], "data": [], "row_count": 0, "type_map": {}, "warnings": warnings + ["清洗后无数据"]}
                    continue
                headers = [str(h) if h else f"列{i+1}" for i, h in enumerate(clean[0])]
                data_rows = clean[1:][:max_rows]
                serialized = []
                for row in data_rows:
                    padded = list(row) + [None] * (len(headers) - len(row))
                    serialized.append({headers[i]: _serialize_value(padded[i]) for i in range(len(headers))})
                type_map = {}
                for i, h in enumerate(headers):
                    col_values = [row[i] if i < len(row) else None for row in data_rows]
                    type_map[h] = _infer_type(col_values)
                all_results[sname] = {
                    "headers": headers,
                    "data": serialized[:50],
                    "row_count": len(data_rows),
                    "column_count": len(headers),
                    "type_map": type_map,
                    "warnings": warnings,
                }
            self_check_warnings = _self_check(all_results)
            if self_check_warnings:
                all_results["_self_check"] = {"status": "warnings", "warnings": self_check_warnings}
            return json.dumps(all_results, ensure_ascii=False, default=str)

        wb = openpyxl.load_workbook(path, data_only=True)
        sheets_to_read = [sheet_name] if sheet_name else wb.sheetnames
        all_results = {}

        for sname in sheets_to_read:
            if sname not in wb.sheetnames:
                continue
            ws = wb[sname]

            _fill_merged_cells(ws)

            raw = list(ws.iter_rows(values_only=True))

            if not raw:
                all_results[sname] = {"headers": [], "data": [], "row_count": 0, "type_map": {}, "warnings": ["空工作表"]}
                continue

            header_idx = _detect_header_row(raw)
            warnings = []
            if header_idx > 0:
                warnings.append(f"跳过了前 {header_idx} 行（非数据行）")
            if len(raw) > max_rows:
                warnings.append(f"数据行数超过上限，仅展示前 {max_rows} 行")

            clean = raw[header_idx:]
            clean = [row for row in clean if not _is_empty_row(row)]

            if not clean:
                all_results[sname] = {"headers": [], "data": [], "row_count": 0, "type_map": {}, "warnings": warnings + ["清洗后无数据"]}
                continue

            headers = [str(h) if h else f"列{i+1}" for i, h in enumerate(clean[0])]
            data_rows = clean[1:][:max_rows]

            serialized = []
            for row in data_rows:
                padded = list(row) + [None] * (len(headers) - len(row))
                serialized.append({headers[i]: _serialize_value(padded[i]) for i in range(len(headers))})

            type_map = {}
            for i, h in enumerate(headers):
                col_values = [row[i] if i < len(row) else None for row in data_rows]
                type_map[h] = _infer_type(col_values)

            all_results[sname] = {
                "headers": headers,
                "data": serialized[:50],
                "row_count": len(data_rows),
                "column_count": len(headers),
                "type_map": type_map,
                "warnings": warnings,
            }

        wb.close()

        self_check_warnings = _self_check(all_results)
        if self_check_warnings:
            all_results["_self_check"] = {
                "status": "warnings",
                "warnings": self_check_warnings,
            }

        return json.dumps(all_results, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"读取失败: {str(e)}"}, ensure_ascii=False)


@registry.register(name="excel_stats", category="file")
def excel_stats(file_path: str, sheet_name: Optional[str] = None) -> str:
    """
    快速获取 Excel 文件的统计信息（支持 .xlsx 和 .xls）。
    返回行数、列数、各列的基本统计（数值列：均值/最大/最小，文本列：唯一值数量）。
    参数 file_path: Excel 文件路径
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    try:
        engine = None
        if path.suffix.lower() == ".xls":
            engine = "xlrd"
        if sheet_name:
            df = pd.read_excel(path, sheet_name=sheet_name, engine=engine)
            sheets = {sheet_name: df}
        else:
            sheets = pd.read_excel(path, sheet_name=None, engine=engine)

        result = {}
        for sname, df in sheets.items():
            stats = {"row_count": len(df), "column_count": len(df.columns), "columns": {}}
            for col in df.columns:
                col_data = df[col].dropna()
                col_stat = {"dtype": str(df[col].dtype), "non_null": len(col_data)}

                if pd.api.types.is_numeric_dtype(df[col]):
                    col_stat.update({
                        "mean": round(float(col_data.mean()), 2) if len(col_data) > 0 else 0,
                        "min": float(col_data.min()) if len(col_data) > 0 else 0,
                        "max": float(col_data.max()) if len(col_data) > 0 else 0,
                    })
                else:
                    col_stat["unique_count"] = col_data.nunique()

                stats["columns"][str(col)] = col_stat
            result[sname] = stats

        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
