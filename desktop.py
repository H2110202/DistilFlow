import subprocess
import sys
import time
import webview
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()


def main():
    api_port = 8001

    print("启动后端服务（热重载模式）...")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.api:app",
            "--host", "127.0.0.1",
            "--port", str(api_port),
            "--reload",
            "--reload-dir", str(PROJECT_ROOT / "backend"),
        ],
        cwd=str(PROJECT_ROOT),
    )

    time.sleep(3)

    print(f"打开桌面窗口 (API: {api_port})...")
    window = webview.create_window(
        title="赛特员工分身",
        url=f"http://127.0.0.1:{api_port}",
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    webview.start(debug=False)

    proc.terminate()
    proc.wait()


if __name__ == "__main__":
    main()
