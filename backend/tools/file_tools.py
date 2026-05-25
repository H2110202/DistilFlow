"""
文件保存与下载工具 — Agent 处理完文件后保存到工作区，
用户可以通过 API 或界面下载。
"""
import json
import shutil
from pathlib import Path
from datetime import datetime
from backend.tools.registry import registry
from backend.config import get_settings


@registry.register(name="save_file", category="file")
def save_file(content: str, filename: str, subdir: str = "output") -> str:
    """
    将处理结果保存为文件。
    参数 content: 文件内容（文本格式）
    参数 filename: 文件名（如 "销售汇总.xlsx" 或 "报告.txt"）
    参数 subdir: 子目录名（默认 "output"）
    """
    settings = get_settings()
    output_dir = Path(settings.data_dir) / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / filename
    file_path.write_text(content, encoding="utf-8")

    return json.dumps({
        "status": "saved",
        "path": str(file_path),
        "filename": filename,
        "size_bytes": len(content.encode("utf-8")),
    }, ensure_ascii=False)


@registry.register(name="save_binary_file", category="file")
def save_binary_file(source_path: str, filename: str, subdir: str = "output") -> str:
    """
    将已有的二进制文件（如 Excel、图片）复制到输出目录。
    参数 source_path: 源文件路径
    参数 filename: 保存的文件名
    参数 subdir: 子目录名
    """
    settings = get_settings()
    output_dir = Path(settings.data_dir) / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    src = Path(source_path)
    if not src.exists():
        return json.dumps({"error": f"源文件不存在: {source_path}"}, ensure_ascii=False)

    dest = output_dir / filename
    shutil.copy2(src, dest)

    return json.dumps({
        "status": "saved",
        "path": str(dest),
        "filename": filename,
        "size_bytes": dest.stat().st_size,
    }, ensure_ascii=False)


@registry.register(name="list_files", category="file")
def list_files(subdir: str = "output", pattern: str = "*") -> str:
    """
    列出工作区中的文件。
    参数 subdir: 子目录名
    参数 pattern: 文件匹配模式（如 "*.xlsx"）
    """
    settings = get_settings()
    target_dir = Path(settings.data_dir) / subdir

    if not target_dir.exists():
        return json.dumps({"files": [], "directory": str(target_dir)}, ensure_ascii=False)

    files = []
    for f in target_dir.glob(pattern):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "path": str(f),
            })

    return json.dumps({
        "files": sorted(files, key=lambda x: x["modified"], reverse=True),
        "directory": str(target_dir),
        "count": len(files),
    }, ensure_ascii=False, default=str)
