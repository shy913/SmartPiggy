"""路径与上限集中在这里。

所有跟环境相关的路径都只在本模块解析一次，测试就能靠一个 BILL_DATA_DIR
把整个应用指到临时目录去。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "MAX_BYTES",
    "MAX_ROWS",
    "PREVIEW_KEEP",
    "PREVIEW_ROWS",
    "PREVIEW_TTL_SECONDS",
    "builtin_profiles_dir",
    "data_dir",
    "db_path",
    "user_profiles_dir",
]

DEFAULT_DATA_DIR = "~/.local/share/bill"

# 一年的微信账单也就几千行；十万是留足余量又拦住恶意文件的上限。
# 注意这是「真正读取」的上限，跟下面预览显示多少行是两回事——
# 导入绝不能复用预览那份被截断的数据，否则后面的记录会静默丢掉。
MAX_ROWS = 100_000
MAX_BYTES = 20 * 1024 * 1024

# 预览页最多列这么多行，跟 MAX_ROWS 是两条独立的路径。
PREVIEW_ROWS = 200

PREVIEW_TTL_SECONDS = 30 * 60
PREVIEW_KEEP = 5


def data_dir() -> Path:
    """本地数据的根目录。"""
    return Path(os.environ.get("BILL_DATA_DIR", DEFAULT_DATA_DIR)).expanduser()


def db_path() -> Path:
    """账本数据库的位置。BILL_DB 可以单独覆盖它（测试用）。"""
    override = os.environ.get("BILL_DB")
    if override:
        return Path(override).expanduser()
    return data_dir() / "bill.db"


def user_profiles_dir() -> Path:
    """用户自己写的列映射放这里。没有就返回路径，不创建。"""
    return data_dir() / "profiles"


def builtin_profiles_dir() -> Path:
    """随代码走的列映射（支付宝 / 微信 / 银行模板）。"""
    return Path(__file__).resolve().parent / "data" / "profiles"
