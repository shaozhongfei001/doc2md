"""测试用 MySQL 连接参数（环境变量可配，默认回退历史约定）。

历史上各测试文件把 ``personal_kb`` 库/用户与 ``mahound`` 密码写死，
导致在库名不同的机器上所有依赖 DB 的用例被静默 ``pytest.skip``
（本机 123 个用例即因此从未真正执行）。这里统一收敛为：

    PKB_MYSQL_HOST      默认 127.0.0.1
    PKB_MYSQL_PORT      默认 3306
    PKB_MYSQL_DATABASE  默认 personal_kb
    PKB_MYSQL_USER      默认 personal_kb
    PKB_MYSQL_PASSWORD  默认 mahound

未设置任何环境变量时行为与改造前完全一致。
"""

from __future__ import annotations

import os

MYSQL_HOST = os.environ.get("PKB_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = os.environ.get("PKB_MYSQL_PORT", "3306")
MYSQL_DATABASE = os.environ.get("PKB_MYSQL_DATABASE", "personal_kb")
MYSQL_USER = os.environ.get("PKB_MYSQL_USER", "personal_kb")
MYSQL_PASSWORD = os.environ.get("PKB_MYSQL_PASSWORD", "mahound")


def mysql_url(password: str | None = None) -> str:
    """返回 SQLAlchemy 连接串；``password`` 省略时取环境变量值。"""
    secret = MYSQL_PASSWORD if password is None else password
    return (
        f"mysql+pymysql://{MYSQL_USER}:{secret}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )


def mysql_config_block(password: str | None = None, *, indent: str = "  ") -> str:
    """返回测试 ``app.yaml`` 中的 ``mysql:`` 配置块（含缩进，不含 ``mysql:`` 行）。

    供 f-string 三引号 YAML 直接插值使用，保持与被替换的硬编码块字节布局一致。
    """
    secret = MYSQL_PASSWORD if password is None else password
    lines = (
        f"host: {MYSQL_HOST}",
        f"port: {MYSQL_PORT}",
        f"database: {MYSQL_DATABASE}",
        f"username: {MYSQL_USER}",
        f"password: {secret}",
        "charset: utf8mb4",
    )
    return "\n".join(f"{indent}{line}" for line in lines)
