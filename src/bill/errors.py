"""异常类型。这些异常的 message 是写给使用者的，可以直接显示在页面上。"""

from __future__ import annotations

__all__ = ["BillError", "UploadError", "ProfileError", "RowError"]


class BillError(ValueError):
    """基类：str(exc) 可以直接讲给用户听。"""


class UploadError(BillError):
    """上传的文件读不成一张表（类型不对、编码解不开、超过大小上限）。"""


class ProfileError(BillError):
    """列映射配置缺失或自相矛盾——比如没有任何办法判断收支方向。"""


class RowError(BillError):
    """某一行变不成一笔交易（日期或金额解析不了）。"""
