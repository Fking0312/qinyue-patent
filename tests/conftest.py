"""Pytest 全局配置：关闭 CSRF，并隔离测试数据与上传文件。"""

import os
import shutil

os.environ.setdefault("WTF_CSRF_ENABLED", "0")
os.environ.setdefault("FLASK_ENV", "development")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_INSTANCE_PATH = os.path.join(PROJECT_ROOT, "instance", "pytest-instance")

# 测试从不使用 instance/patent.db 或其 uploads，避免伪造案件进入本地业务页面。
shutil.rmtree(TEST_INSTANCE_PATH, ignore_errors=True)
os.makedirs(TEST_INSTANCE_PATH, exist_ok=True)
os.environ["QY_INSTANCE_PATH"] = TEST_INSTANCE_PATH
os.environ["QY_DATABASE_URI"] = "sqlite:///" + os.path.join(TEST_INSTANCE_PATH, "pytest.db")
