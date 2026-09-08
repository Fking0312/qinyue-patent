"""Gunicorn 入口：线上 systemd 单元跑的是 `wsgi:app`，删掉此文件会导致服务起不来。"""

from app import create_app

app = create_app()
