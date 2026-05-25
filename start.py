"""
DistilFlow 一键启动脚本 — 支持三种模式：
1. web    — 浏览器模式（FastAPI + 前端）
2. desktop — 桌面模式（pywebview 桌面窗口）
3. api    — 纯 API 模式（供 IDE 插件接入）
"""
import subprocess
import sys
import os
from pathlib import Path


def run(cmd: str, check: bool = True):
    print(f"\n> {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=Path(__file__).parent)
    if check and result.returncode != 0:
        print(f"❌ 命令执行失败 (退出码: {result.returncode})")
        sys.exit(1)
    return result


def check_env():
    project_dir = Path(__file__).parent
    env_file = project_dir / ".env"
    if not env_file.exists():
        example = project_dir / ".env.example"
        if example.exists():
            import shutil
            shutil.copy2(example, env_file)
            print("⚠️  已从 .env.example 创建 .env 文件")
            print("⚠️  请编辑 .env 填入你的 API Key，然后重新运行此脚本")
            sys.exit(0)
        else:
            print("❌ 缺少 .env 配置文件")
            sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(env_file)
    has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY"))
    if not has_key:
        print("⚠️  .env 中未配置 API Key (OPENAI_API_KEY 或 OPENROUTER_API_KEY)")
        print("⚠️  请编辑 .env 填入你的 API Key，然后重新运行此脚本")
        sys.exit(0)
    print("✅ 配置文件就绪")


def install_deps():
    print("\n📦 安装依赖...")
    run(f'"{sys.executable}" -m pip install -e . --quiet', check=False)
    print("✅ 依赖安装完成")


def mode_web():
    """浏览器模式 — FastAPI + 前端"""
    print("\n" + "=" * 60)
    print("  🌐 浏览器模式启动中...")
    print("  📱 浏览器打开: http://localhost:8000")
    print("  🛑 按 Ctrl+C 停止服务")
    print("=" * 60 + "\n")
    run(f'"{sys.executable}" -m uvicorn backend.api:app --host 0.0.0.0 --port 8000', check=False)


def mode_desktop():
    """桌面模式 — pywebview 桌面窗口"""
    print("\n" + "=" * 60)
    print("  🖥️  桌面模式启动中...")
    print("  📱 桌面窗口将自动打开")
    print("  🛑 关闭窗口即可停止服务")
    print("=" * 60 + "\n")
    run(f'"{sys.executable}" desktop.py', check=False)


def mode_api():
    """纯 API 模式 — 供 IDE 插件接入"""
    print("\n" + "=" * 60)
    print("  🔌 API 模式启动中...")
    print("  📍 端点: http://127.0.0.1:8001/v1/chat/completions")
    print("  🛑 按 Ctrl+C 停止服务")
    print("=" * 60 + "\n")
    run(f'"{sys.executable}" -m uvicorn backend.api:app --host 127.0.0.1 --port 8001', check=False)


def main():
    project_dir = Path(__file__).parent
    os.chdir(project_dir)

    print("=" * 60)
    print("  DistilFlow — 一键启动")
    print("=" * 60)

    print("\n📌 检查 Python 版本...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 11):
        print(f"❌ 需要 Python 3.11+，当前版本: {version.major}.{version.minor}")
        print("请安装 Python 3.11+: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")

    print("\n📌 检查配置文件...")
    check_env()

    install_deps()

    mode = "web"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()

    if mode == "web":
        mode_web()
    elif mode == "desktop":
        mode_desktop()
    elif mode == "api":
        mode_api()
    else:
        print(f"\n❌ 未知模式: {mode}")
        print("用法: python start.py [web|desktop|api]")
        print("  web     — 浏览器模式 (FastAPI + 前端) [默认]")
        print("  desktop — 桌面模式 (pywebview 桌面窗口)")
        print("  api     — 纯 API 模式 (供 IDE 插件接入)")
        sys.exit(1)


if __name__ == "__main__":
    main()
