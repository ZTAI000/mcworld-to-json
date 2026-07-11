import os
import sys
import subprocess
import importlib
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

THIRD_PARTY_DEPENDENCIES = []


def _pip_install(pip_name):
    print(f"[依赖安装] 正在尝试自动安装缺失的依赖包: {pip_name} ...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
        return True
    except Exception:
        print(f"[依赖安装] 自动安装失败，请手动执行: pip install {pip_name}")
        return False


def ensure_dependencies():
    for pip_name, import_name in THIRD_PARTY_DEPENDENCIES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            if not _pip_install(pip_name):
                return False
            try:
                importlib.import_module(import_name)
            except ImportError:
                return False
    return True


def _check_tkinter():
    try:
        import tkinter
        return True
    except ImportError:
        return False


def main():
    ok = ensure_dependencies()
    if not ok:
        input("\n按回车键退出...")
        sys.exit(1)

    if not _check_tkinter():
        print("=" * 60)
        print("  系统缺少 tkinter 图形库，无法启动图形界面。")
        print("  请安装 python3-tk 后重试：")
        print("  - Ubuntu/Debian:     sudo apt install python3-tk")
        print("  - Fedora:           sudo dnf install python3-tkinter")
        print("  - macOS (Homebrew): brew install python-tk")
        print("=" * 60)
        print()
        print("提示：如果你不方便安装图形库，也可以直接使用命令行：")
        print(f"    {sys.executable} cb_extract.py  存档.mcworld  (提取)")
        print(f"    {sys.executable} cb_repack.py   存档.mcworld 修改后.json  新存档.mcworld  (回写)")
        return

    try:
        import gui_app
    except Exception:
        print("图形界面模块加载失败，详细错误信息：")
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)

    gui_app.run()


if __name__ == "__main__":
    main()
