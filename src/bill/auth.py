"""登录认证：一个密码，一个人。

这是给自己用的本地账本，没有多用户、没有注册、没有找回密码——要的只是
「先过一道密码再看账目」，所以这里就一个密码。密码存数据目录里的 ``auth.toml``，
scrypt 加盐哈希，**不存明文**；同一个文件里还放着一把随机会话密钥，重启服务不会
把已经登录的浏览器踢下线。

**文件不存在 = 不设防。** 这是刻意的：新装的、以及从上一版升上来的，照旧打开
就能用，不需要先跑一条命令才能看自己的账。这种状态会在每个页面顶上挂一条提示
（见 ``web.py`` 的 ``auth_missing``），不会悄没声地过去；而一旦设过密码，
文件读坏了宁可谁都进不去，也不能悄悄变回不设防。

哈希和校验（``hash_password`` / ``verify``）不碰文件系统，读写是 ``load`` /
``write`` 的事，这样一条一条好测。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import time
import tomllib

from dataclasses import dataclass

from . import config
from .errors import BillError

__all__ = [
    "MIN_LENGTH",
    "Attempts",
    "Auth",
    "hash_password",
    "load",
    "new_session_key",
    "verify",
    "write",
]

MIN_LENGTH = 8
# 太长的密码没有意义，还要拿它去跑一遍 scrypt；给个上限省得有人拿几兆的输入
# 来占内存。
MAX_LENGTH = 1024

SESSION_DAYS = 30

# scrypt 的开销参数：n 是「算几轮」，r 是每轮的内存块。128 * n * r ≈ 16 MiB，
# 比默认的 32 MiB 上限低一档，所以填不填 maxmem 都跑得动——这里还是显式写上，
# 免得哪天换了默认值就崩。
_SCRYPT = {"n": 16384, "r": 8, "p": 1, "dklen": 32}
_SALT_BYTES = 16
_MAX_MEM = 64 * 1024 * 1024

# 会话密钥长什么样：一路 hex。写回去的时候要拼进 TOML，形状不定死的话，
# 一个带引号的「密钥」能拼出一份读不回来的文件，下次请求谁都进不来。
_SESSION_KEY = re.compile(r"^[0-9a-fA-F]{32,128}$")


@dataclass(frozen=True)
class Auth:
    """一份能用的认证配置。"""

    password_hash: str
    session_key: str

    def check(self, password: str) -> bool:
        return verify(password, self.password_hash)


def hash_password(password: str) -> str:
    """把密码做成 ``scrypt$参数$盐$哈希`` 一行。盐每次现取。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _derive(password, salt, _SCRYPT)
    return "scrypt${n}${r}${p}${salt}${key}".format(
        salt=_b64(salt), key=_b64(derived), **_SCRYPT
    )


def verify(password: str, encoded: str) -> bool:
    """按哈希里记的参数重算一遍再比。

    参数是**从文件里读来的**，所以认不出来就返回 False：一份被手改坏的哈希该是
    「谁都进不去」，不是让登录页 500，更不是放行。
    """
    try:
        scheme, n, r, p, salt, key = str(encoded).split("$")
        if scheme != "scrypt":
            return False
        raw_salt = _unb64(salt)
        expected = _unb64(key)
        derived = _derive(
            password,
            raw_salt,
            {"n": int(n), "r": int(r), "p": int(p), "dklen": len(expected)},
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    # 逐字节比完再出结果：提前 return 会把「猜到第几位」漏出去。
    return hmac.compare_digest(derived, expected)


def new_session_key() -> str:
    return secrets.token_hex(32)


def _derive(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=params["n"],
        r=params["r"],
        p=params["p"],
        dklen=params["dklen"],
        maxmem=_MAX_MEM,
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


# --------------------------------------------------------------------------
# 文件
# --------------------------------------------------------------------------

_TOML = '''\
# 由 `bill --set-password` 写出。删掉这个文件 = 关掉登录，恢复成谁都能看。
password = "{password}"

# 会话密钥：浏览器那边的登录凭据是拿它签的。换掉它等于把所有已登录的浏览器
# 一起踢下线。改密码**不会**动它——不然改一次密码就要在每台设备上重登一次。
session_key = "{session_key}"
'''


def load() -> Auth | None:
    """读认证配置。**没有这个文件就返回 None**（= 不设防）。

    坏文件跟缺文件是两回事：缺是「还没设密码」，坏是「设过了但现在读不出来」。
    后者抛出去，让谁都进不来——把读不了的配置当成没设密码，等于一次磁盘故障
    就把账本敞开。
    """
    path = config.user_auth_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BillError(f"{path.name} 读不了：{error}") from None

    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise BillError(f"{path.name} 读不了：{error}") from None

    password_hash = parsed.get("password")
    session_key = parsed.get("session_key")
    # 少任何一项、或者类型不对，都当坏文件，不回落成「不设防」：手改这个文件
    # 把 password 那行删掉，本意绝不是「那就别要密码了」。
    # 形状也一起查——这两个值会被原样写回 TOML，放进来的怪东西（数字、数组、
    # 带引号的串）会让下一次 `--set-password` 写出一份读不回来的配置。
    if not isinstance(password_hash, str) or not password_hash:
        raise BillError(f"{path} 里没有 password。要关掉登录就直接删掉这个文件。")
    if not isinstance(session_key, str) or not _SESSION_KEY.match(session_key):
        raise BillError(
            f"{path} 里的 session_key 不像个会话密钥，跑一次 `bill --set-password`。"
        )

    return Auth(password_hash=password_hash, session_key=session_key)


def write(password: str) -> Auth:
    """写出一份新的 auth.toml（先写临时文件、再原子替换）。

    已经有一份就沿用里面的会话密钥：改密码不该把别的设备一起踢下线。
    文件权限收到 0600——哈希本身不该被别人顺手读走，会话密钥更是。
    """
    if len(password) < MIN_LENGTH:
        raise BillError(f"密码至少 {MIN_LENGTH} 个字符。")
    if len(password) > MAX_LENGTH:
        raise BillError(f"密码太长了，最多 {MAX_LENGTH} 个字符。")

    target = config.user_auth_path()
    try:
        existing = load()
    except BillError:
        # 现有那份读不出来（手改坏了）。这时候照旧换一把新的会话密钥就行：
        # 正在设密码的人本来就进不去，谈不上把他踢下线。
        existing = None

    conf = Auth(
        password_hash=hash_password(password),
        session_key=existing.session_key if existing else new_session_key(),
    )
    text = _TOML.format(
        password=conf.password_hash, session_key=conf.session_key
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        # 先按 0600 建出来再写：权限从第一刻就定死，不经过「先松后紧」那一段。
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, target)
    except OSError as error:
        raise BillError(f"写 {target} 失败：{error}") from None

    # 拿写出去的那份哈希当场验一遍：写出去的东西必须认得回来，否则下一次请求
    # 谁都进不去（跟 settings.save 写完读一遍是同一个理由）。
    if not verify(password, conf.password_hash):
        raise BillError(f"写出去的 {target.name} 验不过，密码没生效。")
    return conf


# --------------------------------------------------------------------------
# 失败次数
# --------------------------------------------------------------------------


class Attempts:
    """登录失败计数：一个来源在 ``lockout`` 秒里错够 ``limit`` 次就歇一会儿。

    表在内存里，进程重启就清空；这个应用本来就是一个人的账本，拦的是「有人
    在网线那头一遍遍猜密码」，猜错几次停一分钟就够把在线爆破拖到没有意义了。

    ``clock`` 可以换掉，测试不用真的等一分钟。
    """

    def __init__(
        self,
        limit: int = 5,
        lockout: float = 60.0,
        clock=time.monotonic,
    ) -> None:
        self.limit = limit
        self.lockout = lockout
        self.clock = clock
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = self.clock()
        recent = [when for when in self._fails.get(key, []) if now - when < self.lockout]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def waiting(self, key: str) -> float:
        """还要等多少秒才能再试。0 = 现在就能试。"""
        recent = self._recent(key)
        if len(recent) < self.limit:
            return 0.0
        # 记满之后每次错都往后顺延，等的是「最后一次」往后那一分钟。
        return max(0.0, self.lockout - (self.clock() - recent[-1]))

    def failed(self, key: str) -> None:
        self._fails.setdefault(key, []).append(self.clock())
        self._recent(key)

    def clear(self, key: str) -> None:
        self._fails.pop(key, None)
