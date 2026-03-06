import asyncio
from re import Match
from typing import TYPE_CHECKING, Any, ClassVar

from aiohttp import ClientTimeout
from bilibili_api import request_settings, select_client
from bilibili_api.opus import Opus
from bilibili_api.video import Video, VideoCodecs, VideoQuality
from msgspec import convert

from astrbot.api import logger

from ...config import PluginConfig
from ...data import ImageContent, MediaContent, Platform
from ...exception import DownloadException, DurationLimitException
from ...utils import LimitedSizeDict
from ..base import (
    BaseParser,
    Downloader,
    ParseException,
    handle,
)
from .login import BilibiliLogin

if TYPE_CHECKING:
    from .video import PageInfo, VideoInfo

# 选择客户端
select_client("curl_cffi")
# 模拟浏览器，第二参数数值参考 curl_cffi 文档
# https://curl-cffi.readthedocs.io/en/latest/impersonate.html
request_settings.set("impersonate", "chrome131")


class BilibiliParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="bilibili", display_name="B站")
    _OFFICIAL_SUMMARY_UNSUPPORTED = "该视频暂不支持AI总结"
    _LLM_FALLBACK_HINT = "可开启 bili_llm_fallback 并配置 LLM 进行兜底"

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.bilibili
        self.headers.update(
            {
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
            }
        )

        self.video_quality = getattr(
            VideoQuality, str(self.mycfg.video_quality).upper(), VideoQuality._720P
        )
        self.video_codecs = getattr(
            VideoCodecs, str(self.mycfg.video_codecs).upper(), VideoCodecs.AVC
        )

        self.login = BilibiliLogin(config)
        self._cid_cache: LimitedSizeDict[str, int | None] = LimitedSizeDict(
            max_size=512
        )
        self._subtitle_cache: LimitedSizeDict[str, str | None] = LimitedSizeDict(
            max_size=256
        )
        self._summary_cache: LimitedSizeDict[str, str] = LimitedSizeDict(max_size=256)

    @handle("b23.tv", r"b23\.tv/[A-Za-z\d\._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("BV", r"^(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/BV",
        r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_bv(self, searched: Match[str]):
        """解析视频信息"""
        bvid = str(searched.group("bvid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(bvid=bvid, page_num=page_num)

    @handle("bm", r"^bm(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s(?P<page_num>\d{1,3}))?$")
    async def _parse_bv_bm(self, searched: Match[str]):
        bvid = searched.group("bvid")
        page = int(searched.group("page_num") or 1)
        _, a_url = await self.extract_download_urls(bvid=bvid, page_index=page - 1)
        if not a_url:
            raise ParseException("未找到音频链接")
        audio = self.create_audio_content(a_url)
        return self.result(
            title=f"BiliBili_audio_{bvid}",
            contents=[audio],
            url=a_url,
        )

    @handle("av", r"^av(?P<avid>\d{6,})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/av",
        r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_av(self, searched: Match[str]):
        """解析视频信息"""
        avid = int(searched.group("avid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(avid=avid, page_num=page_num)

    @handle("/dynamic/", r"bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: Match[str]):
        """解析动态信息"""
        dynamic_id = int(searched.group("dynamic_id"))
        return await self.parse_dynamic(dynamic_id)

    @handle("live.bili", r"live\.bilibili\.com/(?P<room_id>\d+)")
    async def _parse_live(self, searched: Match[str]):
        """解析直播信息"""
        room_id = int(searched.group("room_id"))
        return await self.parse_live(room_id)

    @handle("/favlist", r"favlist\?fid=(?P<fav_id>\d+)")
    async def _parse_favlist(self, searched: Match[str]):
        """解析收藏夹信息"""
        fav_id = int(searched.group("fav_id"))
        return await self.parse_favlist(fav_id)

    @handle("/read/", r"bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: Match[str]):
        """解析专栏信息"""
        read_id = int(searched.group("read_id"))
        return await self.parse_read_with_opus(read_id)

    @handle("/opus/", r"bilibili\.com/opus/(?P<opus_id>\d+)")
    async def _parse_opus(self, searched: Match[str]):
        """解析图文动态信息"""
        opus_id = int(searched.group("opus_id"))
        return await self.parse_opus(opus_id)

    @staticmethod
    def _build_video_url(bvid: str, page_index: int) -> str:
        url = f"https://www.bilibili.com/video/{bvid}"
        return url + f"?p={page_index + 1}" if page_index > 0 else url

    def _llm_fallback_missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.cfg.bili_llm_api_base:
            missing.append("bili_llm_api_base")
        if not self.cfg.bili_llm_model:
            missing.append("bili_llm_model")
        return missing

    @staticmethod
    def _normalize_summary_text(text: str) -> str:
        """
        统一清洗 LLM 文本，避免回包出现 Markdown 代码块或多余空行。
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        lines = [line.rstrip() for line in cleaned.splitlines()]
        return "\n".join(line for line in lines if line.strip()).strip()

    @staticmethod
    def _humanize_llm_error(err: str | None) -> str:
        if not err:
            return "未知错误"
        low = err.lower()
        if "http 401" in low or "http 403" in low:
            return "接口鉴权失败，请检查 bili_llm_api_key"
        if "http 404" in low:
            return "接口地址无效，请检查 bili_llm_api_base"
        if "http 429" in low:
            return "接口限流，请稍后重试"
        if "timeout" in low:
            return "接口超时，可调大 bili_llm_timeout"
        if "http 5" in low:
            return "接口服务异常（5xx）"
        if "empty completion" in low:
            return "模型返回为空"
        return err[:120]

    def _summary_cache_key(
        self,
        *,
        bvid: str,
        page_index: int,
    ) -> str:
        """
        总结缓存 key。包含兜底配置，避免不同模型/配置相互污染。
        """
        return "|".join(
            [
                bvid,
                str(page_index),
                str(int(self.cfg.bili_llm_fallback)),
                self.cfg.bili_llm_api_base,
                self.cfg.bili_llm_model,
                str(self.cfg.bili_llm_max_chars),
            ]
        )

    async def _get_page_cid(
        self,
        *,
        video: Video,
        bvid: str,
        page_index: int,
    ) -> int | None:
        cache_key = f"{bvid}:{page_index}"
        if cache_key in self._cid_cache:
            return self._cid_cache[cache_key]
        try:
            cid = await video.get_cid(page_index)
        except Exception as e:
            logger.debug(f"获取 cid 失败: {e}")
            cid = None
        self._cid_cache[cache_key] = cid
        return cid

    @staticmethod
    def _extract_llm_text(data: dict[str, Any]) -> str:
        """
        兼容多种 OpenAI 风格返回：
        - choices[0].message.content (str / list)
        - choices[0].text
        - output_text
        """

        def _flatten_content(content: Any) -> str:
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item.strip())
                        continue
                    if not isinstance(item, dict):
                        continue
                    for key in ("text", "content", "output_text"):
                        val = item.get(key)
                        if isinstance(val, str) and val.strip():
                            parts.append(val.strip())
                            break
                return "\n".join(p for p in parts if p).strip()
            return ""

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    text = _flatten_content(message.get("content"))
                    if text:
                        return text
                text_field = first.get("text")
                if isinstance(text_field, str) and text_field.strip():
                    return text_field.strip()

        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        if isinstance(output_text, list):
            merged = "\n".join(
                x.strip() for x in output_text if isinstance(x, str) and x.strip()
            ).strip()
            if merged:
                return merged

        return ""

    async def _get_official_ai_summary(
        self,
        *,
        video: Video,
        cid: int | None,
    ) -> tuple[str, bool]:
        """
        返回值:
        - summary 文本
        - 是否成功拿到官方 AI 总结
        """
        from .video import AIConclusion

        if not self.login._credential:
            return "哔哩哔哩 cookie 未配置或失效, 无法使用 AI 总结", False
        if cid is None:
            return "官方AI总结获取失败", False

        try:
            ai_conclusion = await video.get_ai_conclusion(cid)
            ai_conclusion = convert(ai_conclusion, AIConclusion)
            summary = ai_conclusion.summary
            return summary, summary != self._OFFICIAL_SUMMARY_UNSUPPORTED
        except Exception as e:
            logger.warning(f"获取 B 站官方 AI 总结失败: {e}")
            return "官方AI总结获取失败", False

    async def _fetch_subtitle_text(
        self,
        *,
        bvid: str,
        cid: int,
    ) -> str | None:
        """
        拉取 B 站字幕文本（优先第一个字幕轨道）。
        """
        cache_key = f"{bvid}:{cid}"
        if cache_key in self._subtitle_cache:
            return self._subtitle_cache[cache_key]

        api = "https://api.bilibili.com/x/player/v2"
        try:
            async with self.session.get(
                api,
                params={"bvid": bvid, "cid": cid},
                headers=self.headers,
                proxy=self.proxy,
            ) as resp:
                if resp.status >= 400:
                    self._subtitle_cache[cache_key] = None
                    return None
                player_data = await resp.json(content_type=None)
        except Exception as e:
            logger.debug(f"获取字幕元信息失败: {e}")
            self._subtitle_cache[cache_key] = None
            return None

        subtitles = (
            player_data.get("data", {})
            .get("subtitle", {})
            .get("subtitles", [])
        )
        if not subtitles:
            self._subtitle_cache[cache_key] = None
            return None

        subtitle_url = subtitles[0].get("subtitle_url")
        if not subtitle_url:
            self._subtitle_cache[cache_key] = None
            return None
        if subtitle_url.startswith("//"):
            subtitle_url = f"https:{subtitle_url}"

        try:
            async with self.session.get(
                subtitle_url,
                headers=self.headers,
                proxy=self.proxy,
            ) as resp:
                if resp.status >= 400:
                    self._subtitle_cache[cache_key] = None
                    return None
                subtitle_data = await resp.json(content_type=None)
        except Exception as e:
            logger.debug(f"获取字幕内容失败: {e}")
            self._subtitle_cache[cache_key] = None
            return None

        body = subtitle_data.get("body", [])
        if not isinstance(body, list):
            self._subtitle_cache[cache_key] = None
            return None
        text = "\n".join(
            item.get("content", "").strip()
            for item in body
            if isinstance(item, dict) and item.get("content")
        ).strip()
        result = text or None
        self._subtitle_cache[cache_key] = result
        return result

    async def _llm_summarize(
        self, *, title: str, url: str, source_text: str
    ) -> tuple[str | None, str | None]:
        """
        使用 OpenAI 兼容接口总结文本。
        """
        endpoint = f"{self.cfg.bili_llm_api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.cfg.bili_llm_model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是中文视频内容总结助手。"
                        "请严格按以下格式输出：\n"
                        "【一句话概述】...\n"
                        "【核心要点】\n"
                        "1. ...\n"
                        "2. ...\n"
                        "3. ...\n"
                        "【结论】...\n"
                        "要求：中文、信息准确、总长度控制在120~260字。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"请对下面视频内容做中文总结（120~220字）。\n"
                        f"- 标题: {title}\n"
                        f"- 链接: {url}\n"
                        f"- 重点: 主题、核心观点、结论/建议。\n\n"
                        f"内容如下：\n{source_text}"
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.cfg.bili_llm_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.bili_llm_api_key}"

        try:
            async with self.session.post(
                endpoint,
                json=payload,
                headers=headers,
                proxy=self.proxy,
                timeout=ClientTimeout(total=self.cfg.bili_llm_timeout),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning(f"LLM 总结请求失败: HTTP {resp.status}, {body[:200]}")
                    return None, f"HTTP {resp.status}: {body[:120]}"
                data = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"LLM 总结请求异常: {e}")
            return None, str(e)[:120]

        content = self._extract_llm_text(data)
        if not content:
            return None, "empty completion"
        return self._normalize_summary_text(content), None

    async def _get_summary_text(
        self,
        *,
        video: Video,
        video_info: "VideoInfo",
        page_info: "PageInfo",
    ) -> str:
        """
        优先官方 AI 总结，失败时可回退到 LLM 总结。
        """
        summary_cache_key = self._summary_cache_key(
            bvid=video_info.bvid,
            page_index=page_info.index,
        )
        cached_summary = self._summary_cache.get(summary_cache_key)
        if cached_summary:
            return cached_summary

        cid = await self._get_page_cid(
            video=video,
            bvid=video_info.bvid,
            page_index=page_info.index,
        )

        official_summary, official_ok = await self._get_official_ai_summary(
            video=video,
            cid=cid,
        )
        if official_ok:
            self._summary_cache[summary_cache_key] = official_summary
            return official_summary
        if not self.cfg.bili_llm_fallback:
            if official_summary == self._OFFICIAL_SUMMARY_UNSUPPORTED:
                return f"{official_summary}（{self._LLM_FALLBACK_HINT}）"
            return official_summary

        missing = self._llm_fallback_missing_fields()
        if missing:
            if official_summary == self._OFFICIAL_SUMMARY_UNSUPPORTED:
                return f"{official_summary}（LLM配置缺失: {', '.join(missing)}）"
            return official_summary

        source_text = ""
        source_type = "字幕"
        if cid is not None:
            subtitle_text = await self._fetch_subtitle_text(
                bvid=video_info.bvid,
                cid=cid,
            )
            if subtitle_text:
                source_text = subtitle_text

        if not source_text:
            source_type = "简介"
            source_text = (video_info.desc or "").strip()
        if not source_text:
            if official_summary == self._OFFICIAL_SUMMARY_UNSUPPORTED:
                return f"{official_summary}（LLM兜底失败: 无可用字幕或简介）"
            return official_summary

        max_chars = self.cfg.bili_llm_max_chars
        source_text = source_text[:max_chars]
        url = self._build_video_url(video_info.bvid, page_info.index)

        llm_summary, llm_err = await self._llm_summarize(
            title=page_info.title,
            url=url,
            source_text=source_text,
        )
        if not llm_summary:
            if official_summary == self._OFFICIAL_SUMMARY_UNSUPPORTED:
                detail = self._humanize_llm_error(llm_err)
                return f"{official_summary}（LLM兜底失败: {detail}）"
            return official_summary

        result = f"LLM总结（基于{source_type}）:\n{llm_summary}"
        self._summary_cache[summary_cache_key] = result
        return result

    async def parse_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ):
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_num (int): 页码
        """

        from .video import VideoInfo

        video = await self._get_video(bvid=bvid, avid=avid)
        # 转换为 msgspec struct
        video_info = convert(await video.get_info(), VideoInfo)
        # 获取简介
        text = f"简介: {video_info.desc}" if video_info.desc else None
        # up
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        ai_summary = await self._get_summary_text(
            video=video,
            video_info=video_info,
            page_info=page_info,
        )

        url = self._build_video_url(video_info.bvid, page_info.index)

        # 视频下载 task
        real_page_num = page_info.index + 1

        async def download_video():
            output_path = self.cfg.cache_dir / f"{video_info.bvid}-{real_page_num}.mp4"
            if output_path.exists():
                return output_path
            v_url, a_url = await self.extract_download_urls(
                video=video, page_index=page_info.index
            )
            if page_info.duration > self.cfg.max_duration:
                raise DurationLimitException
            if a_url is not None:
                return await self.downloader.download_av_and_merge(
                    v_url,
                    a_url,
                    output_path=output_path,
                    headers=self.headers,
                    proxy=self.proxy,
                )
            else:
                return await self.downloader.streamd(
                    v_url,
                    file_name=output_path.name,
                    headers=self.headers,
                    proxy=self.proxy,
                )

        task_name = f"bili_video_{video_info.bvid}_p{real_page_num}"
        video_task = asyncio.create_task(download_video(), name=task_name)
        video_content = self.create_video_content(
            video_task,
            page_info.cover,
            page_info.duration,
        )

        return self.result(
            url=url,
            title=page_info.title,
            timestamp=page_info.timestamp,
            text=text,
            author=author,
            contents=[video_content],
            extra={"info": ai_summary},
        )

    async def summarize_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ) -> str:
        """
        只返回视频摘要文本，不下载媒体文件。
        """
        from .video import VideoInfo

        video = await self._get_video(bvid=bvid, avid=avid)
        video_info = convert(await video.get_info(), VideoInfo)
        page_info = video_info.extract_info_with_page(page_num)
        ai_summary = await self._get_summary_text(
            video=video,
            video_info=video_info,
            page_info=page_info,
        )
        url = self._build_video_url(video_info.bvid, page_info.index)

        lines = [
            "B站视频总结",
            f"标题: {page_info.title}",
            f"UP: {video_info.owner.name}",
            f"链接: {url}",
            ai_summary,
        ]
        return "\n".join(lines)

    async def parse_dynamic(self, dynamic_id: int):
        """解析动态信息

        Args:
            url (str): 动态链接
        """
        from bilibili_api.dynamic import Dynamic

        from .dynamic import DynamicData

        dynamic_ = Dynamic(dynamic_id, await self.login.credential)

        dynamic_info = convert(await dynamic_.get_info(), DynamicData).item
        author = self.create_author(dynamic_info.name, dynamic_info.avatar)

        # 下载图片
        contents: list[MediaContent] = []
        for image_url in dynamic_info.image_urls:
            img_task = self.downloader.download_img(
                image_url, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(img_task))

        return self.result(
            title=dynamic_info.title,
            text=dynamic_info.text,
            timestamp=dynamic_info.timestamp,
            author=author,
            contents=contents,
        )

    async def parse_opus(self, opus_id: int):
        """解析图文动态信息

        Args:
            opus_id (int): 图文动态 id
        """
        opus = Opus(opus_id, await self.login.credential)
        return await self._parse_opus_obj(opus)

    async def parse_read_with_opus(self, read_id: int):
        """解析专栏信息, 使用 Opus 接口
        Args:
            read_id (int): 专栏 id
        """
        from bilibili_api.article import Article

        article = Article(read_id)
        return await self._parse_opus_obj(await article.turn_to_opus())

    async def _parse_opus_obj(self, bili_opus: Opus):
        """解析图文动态信息
        Args:
            opus_id (int): 图文动态 id
        Returns:
            ParseResult: 解析结果
        """
        from .opus import ImageNode, OpusItem, TextNode

        opus_info = await bili_opus.get_info()
        if not isinstance(opus_info, dict):
            raise ParseException("获取图文动态信息失败")
        # 转换为结构体
        opus_data = convert(opus_info, OpusItem)
        logger.debug(f"opus_data: {opus_data}")
        author = self.create_author(*opus_data.name_avatar)
        # 按顺序处理图文内容（参考 parse_read 的逻辑）
        contents: list[MediaContent] = []
        current_text = ""
        for node in opus_data.gen_text_img():
            if isinstance(node, ImageNode):
                contents.append(
                    self.create_graphics_content(
                        node.url, current_text.strip(), node.alt
                    )
                )
                current_text = ""
            elif isinstance(node, TextNode):
                current_text += node.text
        return self.result(
            title=opus_data.title,
            author=author,
            timestamp=opus_data.timestamp,
            contents=contents,
            text=current_text.strip(),
        )

    async def parse_live(self, room_id: int):
        """解析直播信息

        Args:
            room_id (int): 直播 id

        Returns:
            ParseResult: 解析结果
        """
        from bilibili_api.live import LiveRoom

        from .live import RoomData

        room = LiveRoom(room_display_id=room_id, credential=await self.login.credential)
        info_dict = await room.get_room_info()

        room_data = convert(info_dict, RoomData)
        contents: list[MediaContent] = []
        # 下载封面
        if cover := room_data.cover:
            cover_task = self.downloader.download_img(
                cover, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(cover_task))

        # 下载关键帧
        if keyframe := room_data.keyframe:
            keyframe_task = self.downloader.download_img(
                keyframe, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(keyframe_task))

        author = self.create_author(room_data.name, room_data.avatar)

        url = f"https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
        return self.result(
            url=url,
            title=room_data.title,
            text=room_data.detail,
            contents=contents,
            author=author,
        )

    async def parse_favlist(self, fav_id: int):
        """解析收藏夹信息

        Args:
            fav_id (int): 收藏夹 id

        Returns:
            list[GraphicsContent]: 图文内容列表
        """
        from bilibili_api.favorite_list import get_video_favorite_list_content

        from .favlist import FavData

        # 只会取一页，20 个
        fav_dict = await get_video_favorite_list_content(fav_id)

        if fav_dict["medias"] is None:
            raise ParseException("收藏夹内容为空, 或被风控")

        favdata = convert(fav_dict, FavData)

        return self.result(
            title=favdata.title,
            timestamp=favdata.timestamp,
            author=self.create_author(favdata.info.upper.name, favdata.info.upper.face),
            contents=[
                self.create_graphics_content(fav.cover, fav.desc)
                for fav in favdata.medias
            ],
        )

    async def _get_video(
        self, *, bvid: str | None = None, avid: int | None = None
    ) -> Video:
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
        """
        if avid:
            return Video(aid=avid, credential=await self.login.credential)
        elif bvid:
            return Video(bvid=bvid, credential=await self.login.credential)
        else:
            raise ParseException("avid 和 bvid 至少指定一项")

    async def extract_download_urls(
        self,
        video: Video | None = None,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_index: int = 0,
    ) -> tuple[str, str | None]:
        """解析视频下载链接

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_index (int): 页索引 = 页码 - 1
        """

        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoDownloadURLDataDetecter,
            VideoStreamDownloadURL,
        )

        if video is None:
            video = await self._get_video(bvid=bvid, avid=avid)

        # 获取下载数据
        download_url_data = await video.get_download_url(page_index=page_index)
        detecter = VideoDownloadURLDataDetecter(download_url_data)
        streams = detecter.detect_best_streams(
            video_max_quality=self.video_quality,
            codecs=[self.video_codecs],
            no_dolby_video=True,
            no_hdr=True,
        )
        video_stream = streams[0]
        if not isinstance(video_stream, VideoStreamDownloadURL):
            raise DownloadException("未找到可下载的视频流")
        logger.debug(
            f"视频流质量: {video_stream.video_quality.name}, 编码: {video_stream.video_codecs}"
        )

        audio_stream = streams[1]
        if not isinstance(audio_stream, AudioStreamDownloadURL):
            return video_stream.url, None
        logger.debug(f"音频流质量: {audio_stream.audio_quality.name}")
        return video_stream.url, audio_stream.url



