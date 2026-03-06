from asyncio import gather
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from astrbot.api import logger
from astrbot.core.message.components import (
    BaseMessageComponent,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Video,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import PluginConfig
from .data import (
    AudioContent,
    DynamicContent,
    FileContent,
    GraphicsContent,
    ImageContent,
    MediaContent,
    ParseResult,
    VideoContent,
)
from .exception import (
    DownloadException,
    DownloadLimitException,
    SizeLimitException,
    ZeroSizeException,
)
from .render import Renderer


@dataclass(slots=True)
class SendPlan:
    light: list[MediaContent]
    heavy: list[MediaContent]
    render_card: bool
    preview_card: bool
    force_merge: bool


class MessageSender:
    """
    消息发送器

    职责：
    - 根据解析结果（ParseResult）规划发送策略
    - 控制是否渲染卡片、是否强制合并转发
    - 将不同类型的内容转换为 AstrBot 消息组件并发送

    重要原则：
    - 不在此处做解析
    - 不在此处决定“内容是什么”
    - 只负责“怎么发”
    """

    def __init__(self, config: PluginConfig, renderer: Renderer):
        self.cfg = config
        self.renderer = renderer

    @staticmethod
    def _download_fail_tip() -> Plain:
        return Plain("此项媒体下载失败")

    @staticmethod
    def _as_file_uri(path: Path) -> str:
        """将本地路径转换为 file:// URI，兼容需要 URL 的协议端实现。"""
        return path.resolve().as_uri()

    @staticmethod
    def _image_from_local_path(path: Path) -> Image:
        """
        构建图片消息：
        - 新版本 AstrBot 优先使用 fromFileSystem
        - 低版本回退到 file:// URI
        """
        from_fs = getattr(Image, "fromFileSystem", None)
        if callable(from_fs):
            return from_fs(path=str(path))
        return Image(MessageSender._as_file_uri(path))

    @staticmethod
    def _video_from_local_path(path: Path) -> Video:
        """
        构建视频消息：
        - 新版本 AstrBot 优先使用 fromFileSystem
        - 低版本回退到 file:// URI，避免裸绝对路径被当作非法 URL
        """
        from_fs = getattr(Video, "fromFileSystem", None)
        if callable(from_fs):
            return from_fs(path=str(path))
        return Video(MessageSender._as_file_uri(path))

    def _build_send_plan(self, result: ParseResult) -> SendPlan:
        """
        根据解析结果生成发送计划（plan）

        plan 只做“策略决策”，不做任何 IO 或发送动作。
        后续发送流程严格按 plan 执行，避免逻辑分散。
        """
        light, heavy = [], []

        # 合并主内容 + 转发内容，统一参与发送策略计算
        for cont in chain(
            result.contents, result.repost.contents if result.repost else ()
        ):
            match cont:
                case ImageContent() | GraphicsContent():
                    light.append(cont)
                case VideoContent() | AudioContent() | FileContent() | DynamicContent():
                    heavy.append(cont)
                case _:
                    light.append(cont)

        # 仅在“单一重媒体且无其他内容”时，才允许渲染卡片
        is_single_heavy = len(heavy) == 1 and not light
        render_card = is_single_heavy and self.cfg.single_heavy_render_card
        # 实际消息段数量（卡片也算一个段）
        seg_count = len(light) + len(heavy) + (1 if render_card else 0)

        # 达到阈值后，强制合并转发，避免刷屏
        force_merge = seg_count >= self.cfg.forward_threshold

        return SendPlan(
            light=light,
            heavy=heavy,
            render_card=render_card,
            # 预览卡片：仅在“渲染卡片 + 不合并”时独立发送
            preview_card=render_card and not force_merge,
            force_merge=force_merge,
        )

    async def _send_preview_card(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        plan: SendPlan,
    ):
        """
        发送预览卡片（独立消息）

        场景：
        - 只有一个重媒体
        - 未触发合并转发
        - 卡片作为“预览”，不与正文混合
        """
        if not plan.preview_card:
            return

        if image_path := await self.renderer.render_card(result):
            await event.send(event.chain_result([self._image_from_local_path(image_path)]))

    async def _build_segments(
        self,
        result: ParseResult,
        plan: SendPlan,
    ) -> list[BaseMessageComponent]:
        """
        根据发送计划构建消息段列表

        这里负责：
        - 下载媒体
        - 转换为 AstrBot 消息组件
        """
        segs: list[BaseMessageComponent] = []

        # 合并转发时，卡片以内联形式作为一个消息段参与合并
        if plan.render_card and plan.force_merge:
            if image_path := await self.renderer.render_card(result):
                segs.append(self._image_from_local_path(image_path))

        light_contents = plan.light
        heavy_contents = plan.heavy
        if not light_contents and not heavy_contents:
            return segs

        light_paths, heavy_paths = await gather(
            self._resolve_content_paths(light_contents),
            self._resolve_content_paths(heavy_contents),
        )

        # 轻媒体处理
        for cont, path_or_exc in zip(light_contents, light_paths):
            if isinstance(path_or_exc, (DownloadLimitException, ZeroSizeException)):
                continue
            if isinstance(path_or_exc, DownloadException):
                if self.cfg.show_download_fail_tip:
                    segs.append(self._download_fail_tip())
                continue
            if isinstance(path_or_exc, Exception):
                logger.warning(
                    f"轻媒体处理异常: {cont.__class__.__name__}: {path_or_exc}"
                )
                continue

            path: Path = path_or_exc
            match cont:
                case ImageContent():
                    segs.append(self._image_from_local_path(path))
                case GraphicsContent() as g:
                    segs.append(self._image_from_local_path(path))
                    # GraphicsContent 允许携带补充文本
                    if g.text:
                        segs.append(Plain(g.text))
                    if g.alt:
                        segs.append(Plain(g.alt))

        # 重媒体处理
        for cont, path_or_exc in zip(heavy_contents, heavy_paths):
            if isinstance(path_or_exc, SizeLimitException):
                segs.append(Plain("此项媒体超过大小限制"))
                continue
            if isinstance(path_or_exc, DownloadException):
                if self.cfg.show_download_fail_tip:
                    segs.append(self._download_fail_tip())
                continue
            if isinstance(path_or_exc, Exception):
                logger.warning(
                    f"重媒体处理异常: {cont.__class__.__name__}: {path_or_exc}"
                )
                continue

            path: Path = path_or_exc
            match cont:
                case VideoContent() | DynamicContent():
                    segs.append(self._video_from_local_path(path))
                case AudioContent():
                    segs.append(
                        File(name=path.name, file=str(path))
                        if self.cfg.audio_to_file
                        else Record(str(path))
                    )
                case FileContent():
                    segs.append(File(name=path.name, file=str(path)))

        return segs

    @staticmethod
    async def _resolve_content_paths(
        contents: list[MediaContent],
    ) -> list[Path | Exception]:
        """
        并发解析 media path，返回值与输入顺序一致。
        """
        return await gather(*(cont.get_path() for cont in contents), return_exceptions=True)

    def _merge_segments_if_needed(
        self,
        event: AstrMessageEvent,
        segs: list[BaseMessageComponent],
        force_merge: bool,
    ) -> list[BaseMessageComponent]:
        """
        根据策略决定是否将消息段合并为转发节点

        合并后的消息结构：
        - 每个原始消息段成为一个 Node
        - 统一使用机器人自身身份
        """
        if not force_merge or not segs:
            return segs

        nodes = Nodes([])
        self_id = event.get_self_id()

        for seg in segs:
            nodes.nodes.append(Node(uin=self_id, name="解析器", content=[seg]))

        return [nodes]


    async def send_parse_result(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
    ):
        """
        发送解析结果的统一入口

        执行顺序固定：
        1. 构建发送计划
        2. 发送预览卡片（如有）
        3. 构建消息段
        4. 必要时合并转发
        5. 最终发送
        """
        plan = self._build_send_plan(result)

        await self._send_preview_card(event, result, plan)

        segs = await self._build_segments(result, plan)
        segs = self._merge_segments_if_needed(event, segs, plan.force_merge)

        if segs:
            await event.send(event.chain_result(segs))
