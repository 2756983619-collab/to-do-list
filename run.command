#!/bin/bash
cd "$(dirname "$0")" || exit 1
PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo '请先在项目目录运行：'
    echo 'python3 -m venv .venv'
    echo '.venv/bin/python -m pip install -r requirements.txt'
    read -r -p '按回车退出…'
    exit 1
fi
if ! "$PYTHON" -c 'import tkinter, AppKit, ApplicationServices' ; then
    echo '请确认 Python 带有 Tk，并安装 Mac 依赖：'
    echo '.venv/bin/python -m pip install -r requirements.txt'
    echo 'Homebrew Python 还需安装对应版本的 python-tk。'
    read -r -p '按回车退出…'
    exit 1
fi
"$PYTHON" app.py
result=$?
if [ "$result" -ne 0 ]; then read -r -p '启动失败，按回车退出…'; fi
exit "$result"
