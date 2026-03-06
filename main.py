# main.py

import asyncio
import re

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Image, Json, Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.arbiter import ArbiterContext, EmojiLikeArbiter
from .core.clean import CacheCleaner
from .core.config import PluginConfig
from .core.debounce import Debouncer
from .core.download import Downloader
from .core.exception import ParseException
from .core.parsers import BaseParser, BilibiliParser
from .core.render import Renderer
from .core.sender import MessageSender
from .core.utils import extract_json_url

try:
    from aiocqhttp.exceptions import ActionFailed as AioActionFailed
except Exception:
    AioActionFailed = None

SUMMARY_CMD_RE = re.compile(r"^\s*(?:总结B站|总结b站|总结|bsummary)\s+", re.IGNORECASE)
BILI_BV_URL_RE = re.compile(
    r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})(?:\?p=(?P<page_num>\d{1,3}))?",
    re.IGNORECASE,
)
BILI_AV_URL_RE = re.compile(
    r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})(?:\?p=(?P<page_num>\d{1,3}))?",
    re.IGNORECASE,
)
BILI_BV_RE = re.compile(
    r"(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s+(?:p)?(?P<page_num>\d{1,3}))?$",
    re.IGNORECASE,
)
BILI_AV_RE = re.compile(
    r"av(?P<avid>\d{6,})(?:\s+(?:p)?(?P<page_num>\d{1,3}))?$",
    re.IGNORECASE,
)


class ParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context=context)
        # 渲染器
        self.renderer = Renderer(self.cfg)
        # 下载器
        self.downloader = Downloader(self.cfg)
        # 防抖器
        self.debouncer = Debouncer(self.cfg)
        # 仲裁器
        self.arbiter = EmojiLikeArbiter()
        # 消息发送器
        self.sender = MessageSender(self.cfg, self.renderer)
        # 缓存清理器
        self.cleaner = CacheCleaner(self.cfg)
        # 关键词 -> Parser 映射
        self.parser_map: dict[str, BaseParser] = {}
        # 关键词 -> 正则 列表
        self.key_pattern_list: list[tuple[str, re.Pattern[str]]] = []


    async def initialize(self):
        """加载、重载插件时触发"""
        # 加载渲染器资源
        await asyncio.to_thread(Renderer.load_resources)
        # 注册解析器
        self._register_parser()

    async def terminate(self):
        """插件卸载时触发"""
        # 关下载器里的会话
        await self.downloader.close()
        # 关所有解析器里的会话 (去重后的实例)
        unique_parsers = set(self.parser_map.values())
        for parser in unique_parsers:
            await parser.close_session()
        # 关缓存清理器
        await self.cleaner.stop()

    def _register_parser(self):
        """注册解析器（以 parser.enable 为唯一启用来源）"""
        # 所有 Parser 子类
        all_subclass = BaseParser.get_all_subclass()
        enabled_platforms = set(self.cfg.parser.enabled_platforms())

        enabled_classes: list[type[BaseParser]] = []
        enabled_names: list[str] = []
        for cls in all_subclass:
            platform_name = cls.platform.name

            if platform_name not in enabled_platforms:
                logger.debug(f"[parser] 平台未启用或未配置: {platform_name}")
                continue

            enabled_classes.append(cls)
            enabled_names.append(platform_name)

            # 一个平台一个 parser 实例
            parser = cls(self.cfg, self.downloader)

            # 关键词 → parser
            for keyword, _ in cls._key_patterns:
                self.parser_map[keyword] = parser

        logger.debug(f"启用平台: {'、'.join(enabled_names) if enabled_names else '无'}")

        # -------- 关键词-正则表（统一生成） --------
        patterns: list[tuple[str, re.Pattern[str]]] = []

        for cls in enabled_classes:
            for kw, pat in cls._key_patterns:
                patterns.append((kw, re.compile(pat) if isinstance(pat, str) else pat))

        # 长关键词优先，避免短词抢匹配
        patterns.sort(key=lambda x: -len(x[0]))

        self.key_pattern_list = patterns

        logger.debug(f"[parser] 关键词-正则对已生成: {[kw for kw, _ in patterns]}")

    def _get_parser_by_type(self, parser_type):
        for parser in self.parser_map.values():
            if isinstance(parser, parser_type):
                return parser
        raise ValueError(f"未找到类型为 {parser_type} 的 parser 实例")

    @staticmethod
    def _normalize_page_num(page_num_text: str | None) -> int:
        if not page_num_text:
            return 1
        try:
            page = int(page_num_text)
        except ValueError:
            return 1
        return page if page > 0 else 1

    def _extract_bili_summary_target(self, text: str) -> dict[str, str | int] | None:
        """
        从“总结命令”中提取 B 站视频定位参数。
        支持:
        - 总结 BVxxxx [p]
        - 总结 av123456 [p]
        - 总结 https://www.bilibili.com/video/BV...?... 
        """
        cmd_match = SUMMARY_CMD_RE.match(text)
        if not cmd_match:
            return None
        payload = text[cmd_match.end() :].strip()
        if not payload:
            return None

        if searched := BILI_BV_URL_RE.search(payload):
            return {
                "bvid": searched.group("bvid"),
                "page_num": self._normalize_page_num(searched.group("page_num")),
            }
        if searched := BILI_AV_URL_RE.search(payload):
            return {
                "avid": int(searched.group("avid")),
                "page_num": self._normalize_page_num(searched.group("page_num")),
            }
        if searched := BILI_BV_RE.search(payload):
            return {
                "bvid": searched.group("bvid"),
                "page_num": self._normalize_page_num(searched.group("page_num")),
            }
        if searched := BILI_AV_RE.search(payload):
            return {
                "avid": int(searched.group("avid")),
                "page_num": self._normalize_page_num(searched.group("page_num")),
            }
        return None

    async def _should_skip_by_arbiter(self, event: AstrMessageEvent) -> bool:
        if not isinstance(event, AiocqhttpMessageEvent) or event.is_private_chat():
            return False

        raw = event.message_obj.raw_message
        if not isinstance(raw, dict):
            logger.warning(f"Unexpected raw_message type: {type(raw)}")
            return True

        try:
            ctx = ArbiterContext(
                message_id=int(raw["message_id"]),
                msg_time=int(raw["time"]),
                self_id=int(raw["self_id"]),
            )
        except Exception:
            logger.warning("raw_message 缺少必要字段，跳过解析")
            return True

        is_win = await self.arbiter.compete(bot=event.bot, ctx=ctx)
        if not is_win:
            logger.debug("Bot在仲裁中输了, 跳过解析")
            return True
        logger.debug("Bot在仲裁中胜出, 准备解析...")
        return False

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """消息的统一入口"""
        umo = event.unified_msg_origin

        # 白名单
        if self.cfg.whitelist and umo not in self.cfg.whitelist:
            return

        # 黑名单
        if self.cfg.blacklist and umo in self.cfg.blacklist:
            return

        # 消息链
        chain = event.get_messages()
        if not chain:
            return

        seg1 = chain[0]
        text = event.message_str

        # 卡片解析：解析Json组件，提取URL
        if isinstance(seg1, Json):
            text = extract_json_url(seg1.data)
            logger.debug(f"解析Json组件: {text}")

        if not text:
            return

        self_id = event.get_self_id()

        # 指定机制：专门@其他bot的消息不解析
        if isinstance(seg1, At) and str(seg1.qq) != self_id:
            return

        # “只总结，不下载”分支
        if SUMMARY_CMD_RE.match(text):
            target = self._extract_bili_summary_target(text)
            if target is None:
                await event.send(
                    event.chain_result(
                        [
                            Plain(
                                "用法: 总结 BV号 [分P] 或 总结 av号 [分P] 或 总结 B站视频链接"
                            )
                        ]
                    )
                )
                return

            if await self._should_skip_by_arbiter(event):
                return

            try:
                parser: BilibiliParser = self._get_parser_by_type(BilibiliParser)  # type: ignore
            except ValueError:
                await event.send(event.chain_result([Plain("B站解析器未启用")]))
                return

            try:
                summary_text = await parser.summarize_video(**target)
            except ParseException as e:
                await event.send(event.chain_result([Plain(f"总结失败: {e}")]))
                return
            except Exception:
                logger.exception("[总结异常] B站总结链路异常")
                await event.send(event.chain_result([Plain("总结失败，请稍后重试")]))
                return

            await event.send(event.chain_result([Plain(summary_text)]))
            return

        # 核心匹配逻辑 ：关键词 + 正则双重判定，汇集了所有解析器的正则对。
        keyword: str = ""
        searched: re.Match[str] | None = None
        for kw, pat in self.key_pattern_list:
            if kw not in text:
                continue
            if m := pat.search(text):
                keyword, searched = kw, m
                break
        if searched is None:
            return
        logger.debug(f"匹配结果: {keyword}, {searched}")

        # 仲裁机制
        if await self._should_skip_by_arbiter(event):
            return

        # 基于link防抖
        link = searched.group(0)
        if self.debouncer.hit_link(umo, link):
            logger.warning(f"[链接防抖] 链接 {link} 在防抖时间内，跳过解析")
            return

        # 解析
        try:
            parse_res = await self.parser_map[keyword].parse(keyword, searched)
        except ParseException as e:
            logger.warning(f"[解析失败] {keyword}: {e}")
            return
        except Exception:
            logger.exception(f"[解析异常] {keyword} 处理链路异常")
            return

        # 基于资源ID防抖
        resource_id = parse_res.get_resource_id()
        if self.debouncer.hit_resource(umo, resource_id):
            logger.warning(f"[资源防抖] 资源 {resource_id} 在防抖时间内，跳过发送")
            return

        # 发送
        try:
            await self.sender.send_parse_result(event, parse_res)
        except Exception as e:
            if AioActionFailed is not None and isinstance(e, AioActionFailed):
                logger.warning(f"[发送失败] 协议端拒绝消息: {e}")
                return
            logger.exception("[发送异常] 消息下发失败")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启解析")
    async def open_parser(self, event: AstrMessageEvent):
        """开启当前会话的解析"""
        umo = event.unified_msg_origin
        self.cfg.remove_blacklist(umo)
        yield event.plain_result("当前会话的解析已开启")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭解析")
    async def close_parser(self, event: AstrMessageEvent):
        """关闭当前会话的解析"""
        umo = event.unified_msg_origin
        self.cfg.add_blacklist(umo)
        yield event.plain_result("当前会话的解析已关闭")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("登录B站", alias={"blogin", "登录b站"})
    async def login_bilibili(self, event: AstrMessageEvent):
        """扫码登录B站"""
        parser: BilibiliParser = self._get_parser_by_type(BilibiliParser)  # type: ignore
        qrcode = await parser.login.login_with_qrcode()
        yield event.chain_result([Image.fromBytes(qrcode)])
        async for msg in parser.login.check_qr_state():
            yield event.plain_result(msg)
