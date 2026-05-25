"""
Smart PDF Reader — 基于 pdfplumber + pymupdf 的 LLM 友好 PDF 预处理。
pdfplumber = 表格提取最强；pymupdf = 文本提取/布局分析快。
借鉴 Docling 的分层解析思路，输出结构化 JSON。
"""
import json
from pathlib import Path
from typing import Optional
from backend.tools.registry import registry


def _is_scanned_page(page, text: str) -> bool:
    if not text or len(text.strip()) < 20:
        images = page.images
        if len(images) > 0:
            page_area = page.width * page.height
            img_area = 0
            for img in images:
                x0, top, x1, bottom = img.get("x0", 0), img.get("top", 0), img.get("x1", 0), img.get("bottom", 0)
                img_area += abs(x1 - x0) * abs(bottom - top)
            if img_area > page_area * 0.3:
                return True
    return False


@registry.register(name="read_pdf", category="file")
def read_pdf(file_path: str, page_start: int = 0, page_end: int = 10, extract_tables: bool = True) -> str:
    """
    智能读取 PDF 文件。
    提取文本内容和表格，按页组织。支持中文。自动检测扫描件。
    参数 file_path: PDF 文件路径
    参数 page_start: 起始页码(从0开始)
    参数 page_end: 结束页码
    参数 extract_tables: 是否提取表格
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    try:
        import pdfplumber

        pages_data = []
        scanned_pages = []
        warnings = []

        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            end = min(page_end, total_pages)

            for i in range(page_start, end):
                page = pdf.pages[i]
                page_info = {
                    "page": i + 1,
                    "width": page.width,
                    "height": page.height,
                }

                text = page.extract_text()

                if _is_scanned_page(page, text or ""):
                    scanned_pages.append(i + 1)
                    page_info["scanned"] = True
                    page_info["text"] = "[此页为扫描图片，需要OCR识别]"
                    continue

                if text:
                    page_info["text"] = text[:3000]

                if extract_tables:
                    tables = page.extract_tables()
                    if tables:
                        page_info["tables"] = []
                        for tbl in tables[:5]:
                            if tbl and len(tbl) > 0:
                                headers = tbl[0] if tbl else []
                                valid_headers = [str(h) if h else f"列{j+1}" for j, h in enumerate(headers)]
                                page_info["tables"].append({
                                    "headers": valid_headers,
                                    "row_count": len(tbl) - 1 if tbl else 0,
                                    "preview": tbl[:5],
                                })

                pages_data.append(page_info)

            result = {
                "file": path.name,
                "total_pages": total_pages,
                "read_pages": f"{page_start + 1}-{end}",
                "pages": pages_data,
            }

            if scanned_pages:
                result["scanned_pages"] = scanned_pages
                warnings.append(f"检测到 {len(scanned_pages)} 个扫描页（第 {', '.join(map(str, scanned_pages))} 页），需要OCR才能提取文字")

            if warnings:
                result["warnings"] = warnings

            return json.dumps(result, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"读取失败: {str(e)}"}, ensure_ascii=False)


@registry.register(name="pdf_to_text", category="file")
def pdf_to_text(file_path: str, max_chars: int = 5000) -> str:
    """
    将 PDF 全文提取为纯文本。
    用于需要全文分析或搜索的场景。
    参数 file_path: PDF 文件路径
    参数 max_chars: 最大返回字符数
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    try:
        import fitz

        doc = fitz.open(path)
        full_text = []
        total_chars = 0

        for page in doc:
            text = page.get_text()
            full_text.append(text)
            total_chars += len(text)
            if total_chars > max_chars:
                full_text.append(f"\n\n[文本超出限制，已截断。全文共 {len(doc)} 页]")
                break

        doc.close()
        return "\n".join(full_text)

    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                texts = []
                total = 0
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    texts.append(t)
                    total += len(t)
                    if total > max_chars:
                        break
                return "\n".join(texts)
        except Exception as e2:
            return f"PDF 文本提取失败: {e2}"
    except Exception as e:
        return f"PDF 文本提取失败: {e}"


@registry.register(name="read_image", category="file")
def read_image(file_path: str) -> str:
    """
    读取图片并 OCR 识别其中的文字。
    使用 PaddleOCR，中文识别准确率最高。
    自动过滤低置信度结果并标记。
    参数 file_path: 图片文件路径（支持 jpg/png/bmp）
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    try:
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(lang="ch")
        result = ocr.ocr(str(path))

        if not result or not result[0]:
            return json.dumps({"warning": "未识别到文字", "file": path.name}, ensure_ascii=False)

        texts = []
        low_confidence = []
        for line in result[0]:
            if line and len(line) >= 2:
                text = line[1][0]
                confidence = line[1][1]
                entry = {"text": text, "confidence": round(confidence, 3)}
                if confidence < 0.7:
                    entry["low_confidence"] = True
                    low_confidence.append(text)
                texts.append(entry)

        response = {
            "file": path.name,
            "ocr_result": texts,
            "full_text": "".join(t["text"] for t in texts),
        }

        if low_confidence:
            response["warnings"] = [f"以下内容识别置信度较低，可能不准确：{', '.join(low_confidence[:10])}"]
            response["low_confidence_count"] = len(low_confidence)
            response["total_count"] = len(texts)

        return json.dumps(response, ensure_ascii=False)

    except ImportError:
        return json.dumps({"error": "PaddleOCR 未安装。请运行: pip install paddleocr paddlepaddle"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"OCR失败: {str(e)}"}, ensure_ascii=False)
