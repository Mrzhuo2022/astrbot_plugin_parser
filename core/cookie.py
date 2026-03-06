from __future__ import annotations

import time
from dataclasses import dataclass
from http import cookiejar
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from astrbot.api import logger

from .config import ParserItem, PluginConfig


def parse_cookie_string(cookies_str: str) -> dict[str, str]:
    """
    解析形如 ``k1=v1; k2=v2`` 的 Cookie 字符串。
    忽略空片段和非法片段，避免因单个坏值导致整次解析失败。
    """
    parsed: dict[str, str] = {}
    cleaned = cookies_str.replace("\n", "").replace("\r", "").strip()
    if not cleaned:
        return parsed

    for item in cleaned.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            continue
        parsed[name] = value.strip()
    return parsed


@dataclass(slots=True)
class Cookie:
    domain: str
    path: str
    name: str
    value: str
    secure: bool
    expires: int

    def is_expired(self) -> bool:
        return self.expires != 0 and self.expires < int(time.time())

    def match(self, domain: str, path: str, secure: bool) -> bool:
        if self.is_expired():
            return False

        if self.secure and not secure:
            return False

        if self.domain.startswith("."):
            if not domain.endswith(self.domain[1:]):
                return False
        elif domain != self.domain:
            return False

        return path.startswith(self.path)


class CookieJar:
    def __init__(
        self, config: PluginConfig, parser_cfg: ParserItem, domain: str
    ) -> None:
        self.domain = domain

        self.cookie_file = config.cookie_dir / f"{parser_cfg.name}_cookies.txt"
        self.cookies: list[Cookie] = []

        self.raw_cookies = parser_cfg.cookies
        self.cookies_str = ""

        if self.raw_cookies:
            self.cookies_str = self.clean_cookies_str(self.raw_cookies)
            self._load_from_cookies_str(self.cookies_str)
            self.save_to_file()

        if self.cookie_file.exists():
            self.load_from_file()

    # ---------------- public ----------------

    def file_exists(self) -> bool:
        return self.cookie_file.exists()

    def get(self, path: str = "/", secure: bool = True) -> dict[str, str]:
        return {
            c.name: c.value for c in self.cookies if c.match(self.domain, path, secure)
        }

    def get_cookie_header(self, path: str = "/", secure: bool = True) -> str:
        cookies = self.get(path, secure)
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def get_cookie_header_for_url(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.hostname:
            return ""
        return self.get_cookie_header(
            path=parsed.path or "/",
            secure=parsed.scheme == "https",
        )

    def purge_expired(self) -> None:
        self.cookies = [c for c in self.cookies if not c.is_expired()]
        self._sync_cookies_str()

    def to_dict(self) -> dict[str, str]:
        """将 cookies 字符串转换为字典"""
        return parse_cookie_string(self.cookies_str)

    # ---------------- persistence ----------------

    @staticmethod
    def clean_cookies_str(cookies_str: str) -> str:
        return cookies_str.replace("\n", "").replace("\r", "").strip()

    @staticmethod
    def _mask_secret(value: str, *, keep: int = 3) -> str:
        if not value:
            return ""
        if len(value) <= keep * 2:
            return "*" * len(value)
        return f"{value[:keep]}***{value[-keep:]}"

    @classmethod
    def _mask_cookie_header(cls, header: str) -> str:
        parts = []
        for name, value in parse_cookie_string(header).items():
            parts.append(f"{name}={cls._mask_secret(value)}")
        return "; ".join(parts)

    def _sync_cookies_str(self) -> None:
        self.cookies_str = "; ".join(f"{c.name}={c.value}" for c in self.cookies)

    def _load_from_cookies_str(self, cookies_str: str) -> None:
        parsed = parse_cookie_string(cookies_str)
        if not parsed:
            return

        for name, value in parsed.items():
            self.cookies.append(
                Cookie(
                    domain=f".{self.domain}",
                    path="/",
                    name=name,
                    value=value,
                    secure=True,
                    expires=0,
                )
            )
        self._sync_cookies_str()

    def save_to_file(self) -> None:
        cj = cookiejar.MozillaCookieJar(self.cookie_file)

        for c in self.cookies:
            cj.set_cookie(
                cookiejar.Cookie(
                    version=0,
                    name=c.name,
                    value=c.value,
                    port=None,
                    port_specified=False,
                    domain=c.domain,
                    domain_specified=True,
                    domain_initial_dot=c.domain.startswith("."),
                    path=c.path,
                    path_specified=True,
                    secure=c.secure,
                    expires=c.expires,
                    discard=c.expires == 0,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": ""},
                    rfc2109=False,
                )
            )

        cj.save(ignore_discard=True, ignore_expires=True)
        logger.debug(f"已保存 {len(cj)} 个 Cookie 到 {self.cookie_file}")

    def load_from_file(self) -> None:
        cj = cookiejar.MozillaCookieJar(self.cookie_file)
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            logger.warning(f"加载 cookie 文件失败：{self.cookie_file}")
            return

        self.cookies = []
        for c in cj:
            self.cookies.append(
                Cookie(
                    domain=c.domain,
                    path=c.path,
                    name=c.name,
                    value=c.value or "",
                    secure=c.secure,
                    expires=c.expires or 0,
                )
            )

        self._sync_cookies_str()
        logger.debug(f"从文件加载 {len(self.cookies)} 个 Cookie")

    # ---------------- update from response ----------------

    def update_from_response(self, set_cookie_headers: list[str]) -> None:
        if not set_cookie_headers:
            return

        logger.debug(
            f"开始更新 cookies，收到 {len(set_cookie_headers)} 个 Set-Cookie 头"
        )

        updated = False
        updated_items = []
        added_items = []
        ignored_items = []

        for header in set_cookie_headers:
            logger.debug(f"解析 Set-Cookie: {self._mask_cookie_header(header)}")

            sc = SimpleCookie()
            sc.load(header)

            if not sc:
                logger.debug("解析结果为空，跳过该 header")
                continue

            for name, morsel in sc.items():
                value = morsel.value
                path = morsel["path"] or "/"
                domain = morsel["domain"] or f".{self.domain}"
                secure = bool(morsel["secure"])

                expires = 0
                if morsel["expires"]:
                    try:
                        expires = int(
                            time.mktime(
                                time.strptime(
                                    morsel["expires"], "%a, %d-%b-%Y %H:%M:%S %Z"
                                )
                            )
                        )
                    except Exception as e:
                        logger.debug(
                            f"解析 expires 失败: {morsel['expires']}，错误: {e}"
                        )
                        expires = 0

                existing = next(
                    (
                        c
                        for c in self.cookies
                        if c.name == name and c.domain == domain and c.path == path
                    ),
                    None,
                )

                if existing:
                    # 如果值完全一样，仍然记录但标记为“未变更”
                    if (
                        existing.value == value
                        and existing.secure == secure
                        and existing.expires == expires
                    ):
                        ignored_items.append((name, domain, path))
                        logger.debug(
                            f"Cookie 未变更，忽略: {name} (domain={domain}, path={path})"
                        )
                        continue

                    old_value = existing.value
                    existing.value = value
                    existing.secure = secure
                    existing.expires = expires

                    updated_items.append(
                        (name, domain, path, secure, expires)
                    )
                    logger.debug(
                        f"Cookie 更新: {name} (domain={domain}, path={path}) "
                        f"old_value={self._mask_secret(old_value)} "
                        f"new_value={self._mask_secret(value)} "
                        f"secure={secure} expires={expires}"
                    )
                else:
                    self.cookies.append(
                        Cookie(
                            domain=domain,
                            path=path,
                            name=name,
                            value=value,
                            secure=secure,
                            expires=expires,
                        )
                    )
                    added_items.append((name, domain, path, secure, expires))
                    logger.debug(
                        f"Cookie 新增: {name} (domain={domain}, path={path}) "
                        f"value={self._mask_secret(value)} "
                        f"secure={secure} expires={expires}"
                    )

                updated = True

        if updated:
            self.purge_expired()
            self.save_to_file()
            logger.debug(
                "Cookies 已更新并保存 "
                f"(新增 {len(added_items)}，更新 {len(updated_items)}，忽略 {len(ignored_items)})"
            )
            logger.debug(f"当前 Cookie 总数: {len(self.cookies)}")
            logger.debug(f"当前 cookies_str: {self._mask_cookie_header(self.cookies_str)}")
