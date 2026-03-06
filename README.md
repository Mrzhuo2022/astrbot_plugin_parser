<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_parser?name=astrbot_plugin_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_parser

_✨ 链接解析器 ✨_

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Zhalslar-blue)](https://github.com/Zhalslar)

</div>

## 📖 介绍

当前支持的平台和类型：

| 平台      | 触发的消息形态                    | 视频 | 图集 | 音频 |
| --------- | --------------------------------- | ---- | ---- | ---- |
| B 站      | av 号/BV 号/链接/短链/卡片/小程序 | ✅   | ✅   | ✅   |
| 抖音      | 链接(分享链接，兼容电脑端链接)    | ✅   | ✅   | ❌️ |
| 微博      | 链接(博文，视频，show, 文章)      | ✅   | ✅   | ❌️ |
| 小红书    | 链接(含短链)/卡片                 | ✅   | ✅   | ❌️ |
| 快手      | 链接(包含标准链接和短链)          | ✅   | ✅   | ❌️ |
| acfun     | 链接                              | ✅   | ❌️ | ❌️ |
| youtube   | 链接(含短链)                      | ✅   | ❌️ | ✅   |
| tiktok    | 链接                              | ✅   | ❌️ | ❌️ |
| instagram | 链接                              | ✅   | ✅   | ❌️ |
| twitter   | 链接                              | ✅   | ✅   | ❌️ |

本插件目标：凡是链接皆可解析！尽请期待更新（如果可以,请提交PR）

---

## 🎨 效果图

插件默认启用 PIL 实现的通用媒体卡片渲染，效果图如下

<div align="center">

<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/9_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/4_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_2_pic.png" width="160" />

</div>

---

## 💿 安装

直接在astrbot的插件市场搜索astrbot_plugin_parser，点击安装，等待完成即可

## 🚀 快速开始

1. 安装并启用插件
2. 可选登录 B 站账号（用于高画质下载、AI 总结可用性提升）：
   - 指令：`登录B站` 或 `登录b站` 或 `blogin`
3. 直接发送支持平台链接，插件会自动解析
4. 若只想要 B 站总结，不想下载视频，发送：
   - `总结 BV号 [分P]`
   - `总结 av号 [分P]`
   - `总结 B站视频链接`

## ⚙️ 配置

请在 AstrBot 插件配置面板查看并修改。推荐重点关注：

- `source_max_size`：单个媒体最大体积（MB）
- `source_max_minute`：单个媒体最大时长（分钟）
- `clean_cron`：缓存清理检查周期（Cron）
- `cache_max_size_gb`：缓存阈值（GB），超过阈值才清理
- `single_heavy_render_card`：单条重媒体是否先发预览卡片
- `bili_llm_fallback`：B站官方总结不可用时，启用 LLM 兜底
- `bili_llm_api_base` / `bili_llm_model`：LLM 接口配置（必填）
- `bili_llm_api_key`：LLM API Key（按服务商要求，部分本地/网关可留空）
- `bili_llm_timeout` / `bili_llm_max_chars`：LLM 调用超时与输入长度上限

## 🎉 命令与用法

### 管理命令（AstrBot 指令）

| 指令                       | 权限  | 说明                    |
| :------------------------- | :---- | :---------------------- |
| 开启解析                   | ADMIN | 开启当前会话的解析功能  |
| 关闭解析                   | ADMIN | 关闭当前会话的解析功能  |
| 登录B站 / 登录b站 / blogin | ADMIN | 扫码登录 B 站，保存凭证 |

### 自然语言命令（消息触发）

| 用法                    | 权限 | 说明                            |
| :---------------------- | :--- | :------------------------------ |
| `BV号` `[分P]`      | ALL  | 解析 B 站视频并发送媒体         |
| `av号` `[分P]`      | ALL  | 解析 B 站视频并发送媒体         |
| `bmBV号` `[分P]`    | ALL  | 仅提取并发送 B 站音频           |
| 总结 `BV号` `[分P]` | ALL  | 仅返回 B 站总结（官方AI/LLM兜底），不下载视频 |
| 总结 `av号` `[分P]` | ALL  | 仅返回 B 站总结（官方AI/LLM兜底），不下载视频 |
| 总结 `B站视频链接`    | ALL  | 仅返回 B 站总结（官方AI/LLM兜底），不下载视频 |
| bsummary `BV/av/链接` | ALL  | 英文别名，效果同上              |

示例：

```text
BV1xx411c7mD
av1234567
bmBV1xx411c7mD
总结 BV1xx411c7mD
总结 BV1xx411c7mD 2
总结 av1234567
总结 https://www.bilibili.com/video/BV1xx411c7mD?p=3
bsummary BV1xx411c7mD
```

---

## 🧠 插件工作流程

当插件运行后，每一条消息的处理流程如下：

1. **消息接收**监听所有消息事件，获取消息链与原始文本内容

   - 支持普通文本、链接、卡片（Json 组件）
2. **基础过滤**

   - 跳过已被禁用的会话
   - 跳过空消息
   - 若消息首段为 `@` 且目标不是本 Bot，则不解析
3. **只总结视频内容（可选）**

   - 若消息是 `总结 ...` / `bsummary ...`
   - 仅请求 B 站信息与 AI 总结文本，不进入媒体下载
4. **链接提取与匹配**

   - 若为卡片消息，先从 Json 中提取 URL
   - 使用「关键词 + 正则」双重匹配，定位对应解析器
   - 未匹配到解析规则则直接退出
5. **仲裁判定（Emoji Like Arbiter）**

   - 仅在 `aiocqhttp` 平台生效
   - 通过固定表情进行 Bot 间仲裁
   - 未胜出的 Bot 自动放弃解析
6. **防抖判定（Link Debouncer）**

   - 对同一会话内的相同链接进行时间窗口限制
   - 命中防抖规则则跳过解析，避免短时间重复处理
7. **内容解析**

   - 调用对应平台解析器获取媒体信息
   - 生成统一的 `ParseResult` 数据结构
8. **媒体下载与消息构建**

   - 下载视频 / 图片 / 音频 / 文件
   - 根据配置决定音频发送方式
   - 可按配置提示下载失败项
9. **卡片渲染（可选）**

   - 在非简洁模式或无直传媒体时生成媒体卡片
   - 使用 PIL 渲染并缓存图片
10. **消息合并与发送**

    - 当消息段数量超过阈值时自动合并为转发消息
    - 最终将结果发送到对应会话

---

## 🧹 缓存清理说明

- 当 `cache_max_size_gb > 0`：
  - 到达 `clean_cron` 时先检查缓存体积
  - 仅当超过阈值才执行清理（按旧文件优先淘汰）
- 当 `cache_max_size_gb = 0`：
  - 到达 `clean_cron` 时执行常规清理
- 当 `clean_cron` 为空：
  - 禁用自动清理

---

## ❓常见问题

### 1. B 站 Cookies 用哪个？

优先推荐用 `登录B站 / blogin` 扫码登录自动保存。
若手动填 cookies，至少需要 `SESSDATA`，建议同时带上 `bili_jct`、`DedeUserID`、`ac_time_value` 等关键字段。

### 2. Docker 下视频发送失败（ENOENT/找不到文件）怎么办？

确保 AstrBot 容器和协议端容器（如 NapCat）挂载同一宿主机目录，并映射到相同容器路径（例如都映射到 `/AstrBot/data`）。

### 3. B站提示“该视频暂不支持AI总结”怎么办？

可启用 LLM 兜底：

- 打开 `bili_llm_fallback`
- 配置 `bili_llm_api_base`、`bili_llm_model`
- 若服务商需要鉴权，再配置 `bili_llm_api_key`
- 可选调整 `bili_llm_timeout`、`bili_llm_max_chars`

兜底优先使用字幕文本，没有字幕时会使用视频简介。
若兜底失败，返回文案会附带原因（如配置缺失、鉴权失败、接口限流、超时等）。

---

## 🧩 扩展

插件支持自定义解析器，通过继承 `BaseParser` 类并实现 `platform`, `handle` 即可。

示例解析器请看 [示例解析器](https://github.com/Zhalslar/astrbot_plugin_parser/blob/main/core/parsers/example.py)

---

## 🎉 致谢

本项目核心代码来自[nonebot-plugin-parser](https://github.com/fllesser/nonebot-plugin-parser)，请前往原仓库给作者点个Star!
