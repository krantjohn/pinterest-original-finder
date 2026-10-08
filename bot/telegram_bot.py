"""
Telegram Bot implementation for Pinterest Original Finder.
Features:
- Fast response with '正在工作中...' status feedback
- Interactive shortcut menu (Inline Keyboard & Bot Menu commands)
- Real-time progress updates with resolution enhancement details
- Automatic ZIP packaging and document delivery
"""
import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

from config import settings
from core.pipeline import BoardPipeline
from core.yande_finder import yande_finder, YandePost
from utils.logger import logger
from utils.tunnel_server import tunnel_server

PINTEREST_URL_REGEX = re.compile(
    r"(https?://(?:www\.|[a-z]{2}\.)?pinterest\.[\w.]+(?:/pin/[^/\s?#]+|/[^/\s?#]+/[^/\s?#]+/?)|https?://pin\.it/[^\s?#]+)",
    re.IGNORECASE
)

SAMPLE_URL = "https://pin.it/6IyIv54kK"

from dataclasses import dataclass
import threading
from typing import Optional, Dict

@dataclass
class ActiveJob:
    chat_id: int
    board_url: str
    pipeline: BoardPipeline
    cancel_event: threading.Event
    status_msg: any

active_jobs: Dict[int, ActiveJob] = {}

def get_shortcut_keyboard():
    """Return inline keyboard with quick actions."""
    keyboard = [
        [
            InlineKeyboardButton("📖 使用帮助", callback_data="help"),
            InlineKeyboardButton("🌸 Yande 搜图指南", callback_data="yande_help")
        ],
        [
            InlineKeyboardButton("📊 运行状态", callback_data="status"),
            InlineKeyboardButton("🧹 空间清理", callback_data="clean")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_progress_keyboard():
    """Return inline keyboard during task execution with a Stop button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 停止本次任务 (Stop)", callback_data="stop_current_job")]
    ])


async def set_bot_commands(application):
    """Register shortcut commands in Telegram Bot menu."""
    commands = [
        BotCommand("start", "启动机器人与打开功能面板"),
        BotCommand("help", "使用帮助与支持格式"),
        BotCommand("yande", "Yande 每日热图原图提取指南"),
        BotCommand("status", "查看服务器与机器人状态"),
        BotCommand("sample", "获取测试图板链接快速体验"),
        BotCommand("cookie", "查看或配置 Pinterest Cookie 凭证"),
        BotCommand("decrypt", "解密导入加密的 cookies.json 文件"),
        BotCommand("clean", "清理服务器过期文件并查看磁盘空间"),
        BotCommand("saucenao", "查看或绑定 SauceNAO 搜图 API Key"),
        BotCommand("stop", "终止当前正在执行的任务"),
        BotCommand("cancel", "终止当前正在执行的任务"),
    ]
    await application.bot.set_my_commands(commands)





async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_text = (
        "👋 **你好！我是插画与原图提取机器人 (Pinnta)**\n\n"
        "⚡ **核心功能**：\n"
        "1. 🌸 **Yande.re 每日热图秒提原图（全新支持）**：\n"
        "   • 在任何 TG 群/频道浏览 Yande 每日热图时，**选中多张喜欢的图片，一口气全部转发给我**！\n"
        "   • 无论转发图片被压缩成多小，机器人通过视觉检索精准定位 Yande 原帖。\n"
        "   • 自动下载数千像素、无损 PNG/JPG 母盘，并以 **Telegram 无损文件文档 (Document)** 发回，附带画师原作链接。\n\n"
        "2. 📌 **Pinterest 图板高清挖掘**：\n"
        "   • 发送 Pinterest 图板或单 Pin 链接，自动逆向搜图、超分比对并打包 ZIP 下载。\n\n"
        "📌 **直接把图片转发过来，或者把 Pinterest 链接发给我即可开始！**\n\n"
        "👇 你也可以点击下方快捷按钮体验："
    )
    if update.message:
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_shortcut_keyboard()
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 **使用指南 & 常见操作**\n\n"
        "1. **🌸 Yande.re 每日热图原图提取**：\n"
        "   • **如何使用**：在 TG 群或频道中长按多选喜欢的图片，点击“转发”给本机器人。\n"
        "   • **压缩图变原图**：即便转发的图片已被 TG 严重压缩，机器人通过 Yande 视觉库 + IQDB 秒级找到真实原帖。\n"
        "   • **无损交付**：以文件文档 (Document) 形式原样发回，支持 4K/8K 超高分辨率与无损原件！\n\n"
        "2. **📌 Pinterest 图板批量提取**：\n"
        "   • 发送手机 App 分享短链（`pin.it/xxx`）或图板完整链接即可自动解析并打包。\n\n"
        "3. **🛑 任务中断**：\n"
        "   • 若中途想取消，点击进度消息下方的 `[ 🛑 停止本次任务 ]` 或发送 `/stop` 即可毫秒级终止。"
    )
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_shortcut_keyboard())


async def yande_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /yande command."""
    text = (
        "🌸 <b>Yande.re 每日热图原图提取指南</b>\n\n"
        "1. <b>使用场景</b>：\n"
        "   在任意 Telegram 群组或频道浏览 Yande 每日热图、壁纸时，长按多选喜欢的图片，直接<b>一口气全部转发</b>给本机器人。\n\n"
        "2. <b>全自动智能检索</b>：\n"
        "   • <b>视觉特征比对</b>：即使转发图片被压缩成低清缩略图，通过 Yande 视觉库及 IQDB 双引擎 100% 锁定原帖。\n"
        "   • <b>标签/ID 秒达</b>：若转发消息自带标签或 Post ID，自动免搜直达。\n\n"
        "3. <b>最高清母盘交付</b>：\n"
        "   • 以 Telegram <b>无损文件文档 (Document)</b> 形式发回，绝无任何二次压缩损耗。\n"
        "   • 完整呈现原始分辨率（如 3000~6000 像素超清、无损 PNG）、画师名字与 Pixiv/推特原作直链！\n\n"
        "💡 <b>单图/直链支持</b>：\n"
        "   直接发 Yande 链接（如 <code>https://yande.re/post/show/1270146</code>）或单纯数字 ID（如 <code>1270146</code>）也能直接提取原图。"
    )
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(text, parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    has_cookie = settings.cookies_path.is_file()
    cookie_badge = "✅ 已启用会员/账号凭证 (全量解析)" if has_cookie else "⚪ 免登录双引擎 (移动端+网页端自动合并)"
    saucenao_badge = "✅ 已绑定专属 Key (Pixiv/Twitter直连)" if settings.saucenao_api_key else "⚪ 默认多源 (IQDB+Yandex免密搜图)"

    from utils.cleanup import storage_manager
    stats = storage_manager.get_storage_stats()

    status_text = (
        "🟢 **系统运行状态正常**\n\n"
        "• 状态：`在线工作中 (Online)`\n"
        "• 图板解析：`网页 BoardFeed + 移动端 Pidgets 混合双引擎`\n"
        f"• 账号凭证：`{cookie_badge}`\n"
        f"• 搜图增强：`{saucenao_badge}`\n"
        "• 检索通道：`来源穿透 (Tier 1)` + `反向搜图 (Tier 2)` + `官方原图 (Tier 3)`\n"
        f"• 磁盘存储：`可用 {stats['disk_free_gb']} GB` (已用 {stats['disk_used_pct']}%, 24h自动淘汰)\n"
        "• 打包格式：`ZIP 压缩包 + UTF-8 CSV 明细报告 (图板名命名前缀)`\n"
        "• 服务端：`Linux 守护进程运行中`"
    )

    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(status_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_shortcut_keyboard())




async def sample_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sample command."""
    msg = (
        f"⚡ **测试样例图板**：\n`{SAMPLE_URL}`\n\n"
        "你可以长按复制上方链接，直接发送给我体验全自动原图提取！"
    )
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_shortcut_keyboard())


def save_cookie_content(cookie_text: str) -> None:
    """Save cookie text (Netscape format or raw key=val string) to file and configure gallery-dl."""
    settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_text = cookie_text.strip()
    if not cookie_text.startswith("# Netscape"):
        lines = ["# Netscape HTTP Cookie File\n"]
        for item in cookie_text.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                lines.append(f".pinterest.com\tTRUE\t/\tTRUE\t2147483647\t{k.strip()}\t{v.strip()}\n")
        content = "".join(lines)
    else:
        content = cookie_text

    settings.cookies_path.write_text(content, encoding="utf-8")
    from gallery_dl import config
    config.set(("extractor", "pinterest"), "cookies", str(settings.cookies_path))


async def cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cookie command to check or set Pinterest cookies."""
    args = context.args
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return

    if not args:
        has_cookie = settings.cookies_path.is_file()
        cookie_size = f"({settings.cookies_path.stat().st_size} bytes)" if has_cookie else ""
        msg = (
            "🍪 **Pinterest 登录凭证 (Cookies) 配置状态**\n\n"
            f"• 当前状态：{'✅ 已配置 ' + cookie_size if has_cookie else '⚪ 未配置 (当前使用移动端+网页免登录双引擎)'}\n\n"
            "📌 **如何配置凭证？**\n"
            "1. **直接发送文件**：直接在聊天框发送由浏览器导出的 `cookies.txt` 文件即可自动识别保存！\n"
            "2. **直接粘贴文本**：直接把浏览器复制的 Cookie 字符串发给机器人，会自动识别！\n"
            "3. **命令方式**：发送 `/cookie <cookie字符串>`（例如 `_pinterest_sess=...`）\n"
            "4. **清除凭证**：发送 `/clearcookie` 即可恢复默认免登录模式\n\n"
            "💡 *说明：免登录状态下已集成官方双引擎抓取；若图板包含私密或年龄分级过滤的图片，配置 Cookie 后可达成 100% 完整抓取！*"
        )
        await target.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    cookie_text = " ".join(args).strip()
    save_cookie_content(cookie_text)

    await target.reply_text(
        "✅ **Pinterest 凭证已成功保存生效！**\n机器人已升级为账号会员权限，可提取图板下所有私密/受限图片。",
        parse_mode=ParseMode.MARKDOWN
    )


async def clearcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear Pinterest cookies."""
    target = update.message
    if not target:
        return
    if settings.cookies_path.is_file():
        settings.cookies_path.unlink()
    from gallery_dl import config
    await target.reply_text("🗑️ 已清除 Pinterest Cookies，恢复免登录双引擎模式。", parse_mode=ParseMode.MARKDOWN)


async def saucenao_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /saucenao command to view or configure SauceNAO API key."""
    args = context.args
    target = update.message
    if not target:
        return

    if not args:
        has_key = bool(settings.saucenao_api_key)
        masked_key = f"`{settings.saucenao_api_key[:6]}...{settings.saucenao_api_key[-4:]}`" if has_key else "未配置"
        msg = (
            "🔍 **SauceNAO 以图搜图 API 配置状态**\n\n"
            f"• 当前状态：{'✅ 已配置 ' + masked_key if has_key else '⚪ 未配置 (当前使用 IQDB + Yandex 免 Key 搜图)'}\n\n"
            "📌 **什么是 SauceNAO？**\n"
            "SauceNAO 是全球首屈一指的二次元/插画反向图像搜索引擎，能直接逆向追溯到 Pixiv、Twitter/X、ArtStation、Danbooru 等画师原稿出处。\n\n"
            "📌 **如何免费获取并绑定 API Key？**\n"
            "1. 浏览器打开 [saucenao.com](https://saucenao.com/) 免费注册一个账号；\n"
            "2. 登录后在页面右上角进入账户设置，复制你的 API Key；\n"
            "3. 向本机器人发送：`/saucenao 你的API密钥`\n"
            "4. 绑定后即可解除服务器机房 IP 限制，获得专属极速搜图额度！"
        )
        await target.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    new_key = args[0].strip()
    if len(new_key) < 10:
        await target.reply_text("⚠️ API Key 格式看似不正确，请确认后重新输入。")
        return

    test_url = "https://i.pinimg.com/originals/87/83/66/878366f776539f28515377092737b913.jpg"
    api_url = f"https://saucenao.com/search.php?db=999&output_type=2&api_key={new_key}&numres=1&url={test_url}"
    try:
        import httpx
        r = httpx.get(api_url, timeout=10)
        if r.status_code == 200:
            settings.saucenao_api_key = new_key
            env_path = settings.base_dir / ".env"
            lines = []
            found = False
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("SAUCENAO_API_KEY="):
                        lines.append(f"SAUCENAO_API_KEY={new_key}")
                        found = True
                    else:
                        lines.append(line)
            if not found:
                lines.append(f"SAUCENAO_API_KEY={new_key}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            await target.reply_text(
                "🎉 **SauceNAO API Key 验证并保存成功！**\n"
                "机器人已解锁专属 SauceNAO 插画搜图通道，后续搜图将直接直连 Pixiv / Twitter 画师原画库！",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await target.reply_text(f"⚠️ 密钥测试失败（HTTP {r.status_code}），请核对 Key 是否正确。")
    except Exception as e:
        await target.reply_text(f"❌ 验证测试发生错误：{e}")



async def decrypt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /decrypt <password> command for encrypted cookies."""
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return
    args = context.args or []
    if not args:
        await target.reply_text("🔑 请在命令后附带密码，例如：`/decrypt 你的密码`", parse_mode=ParseMode.MARKDOWN)
        return

    pwd = " ".join(args).strip()
    pending_file = settings.base_dir / "cookies_pending.json"
    if not pending_file.is_file():
        await target.reply_text("⚠️ 未找到等待解密的 Cookies 文件，请先发送 `cookies.json` 文件。")
        return

    from utils.cookie_helper import process_uploaded_cookie_file
    ok, msg = process_uploaded_cookie_file(pending_file, password=pwd)
    if ok:
        pending_file.unlink(missing_ok=True)
        await target.reply_text(
            f"🎉 **{msg}**\n\n"
            "Pinterest 账号权限已生效！现在发送图板链接将自动使用该账号提取 100% 完整原图（包括年龄限制与私密图片）。",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        if msg == "PASSWORD_INCORRECT":
            await target.reply_text("❌ 解密失败：密码不正确，请核对后重试（发送 `/decrypt 正确密码`）。")
        else:
            await target.reply_text(f"❌ 处理失败：{msg}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded cookies document (txt or json)."""
    doc = update.message.document
    if not doc:
        return
    fname = (doc.file_name or "").lower()
    if "cookie" in fname or fname.endswith((".txt", ".json")):
        file = await doc.get_file()
        temp_path = settings.base_dir / f"temp_{doc.file_name}"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        await file.download_to_drive(custom_path=str(temp_path))

        from utils.cookie_helper import process_uploaded_cookie_file
        ok, msg = process_uploaded_cookie_file(temp_path)
        if ok:
            temp_path.unlink(missing_ok=True)
            await update.message.reply_text(
                f"✅ **成功导入并加载 `{doc.file_name}`！**\n\n"
                f"• {msg}\n"
                "Pinterest 账号权限已生效，现在发送图板链接将自动提取 100% 完整原图（包括年龄限制与私密图片）。",
                parse_mode=ParseMode.MARKDOWN
            )
        elif msg == "ENCRYPTED_PASSWORD_REQUIRED":
            pending_file = settings.base_dir / "cookies_pending.json"
            temp_path.rename(pending_file)
            await update.message.reply_text(
                "🔐 **检测到已加密的 Cookies 文件！**\n\n"
                "这是由 Cookie-Editor 插件导出的带密码保护的文件。\n"
                "👉 **请直接回复您导出时设置的密码**，或发送：\n"
                "`/decrypt 您的密码`\n\n"
                "机器人将为您秒级解密并配置生效！",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            temp_path.unlink(missing_ok=True)
            await update.message.reply_text(f"⚠️ 解析文件失败：{msg}")
    else:
        await update.message.reply_text(
            "ℹ️ 接收到文件。如果是 Pinterest 凭证，请将文件命名包含 `cookie`（如 `cookies.txt` 或 `cookies.json`）后发送。"
        )



async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clean and /cleanup commands."""
    from utils.cleanup import storage_manager
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return

    res = storage_manager.run_full_cleanup()
    msg = (
        "🧹 **【磁盘存储空间与缓存清理报告】**\n\n"
        f"• 本次清理过期 ZIP：`{res['archives_deleted']} 个`\n"
        f"• 本次清理下载散图缓存：`{res['raw_downloads_deleted']} 个文件`\n"
        f"• 本次释放磁盘空间：`{res['freed_mb']} MB`\n\n"
        f"📊 **当前服务器空间状态**：\n"
        f"• 可用空间：`{res['disk_free_gb']} GB` / `{res['disk_total_gb']} GB` (已用 {res['disk_used_pct']}%)\n"
        f"• 现存 ZIP 合集包：`{res['zip_count']} 个` ({res['output_size_mb']} MB)\n\n"
        "💡 *存储管理机制：*\n"
        "1. 每次任务完成打包后，机器人会自动立即删除散图临时文件；\n"
        "2. 生成的 ZIP 归档包默认保留 24 小时供随时满速下载，超期后台定时任务自动安全回收，绝不会占满服务器空间！"
    )
    await target.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_shortcut_keyboard())


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop or /cancel command to abort running task."""
    chat_id = update.effective_chat.id
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return
    job = active_jobs.get(chat_id)
    if job:
        job.cancel_event.set()
        job.pipeline.cancel()
        await target.reply_text(
            "🛑 **已接收到停止指令！**\n正在立即终止当前后台处理任务并释放系统资源...",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            await job.status_msg.edit_text(
                "🛑 **【正在停止任务...】**\n已收到终止指令，正在立即关闭所有下载通道并清理暂存文件，请稍候...",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
    else:
        await target.reply_text("⚪ 当前没有正在运行的任务。", parse_mode=ParseMode.MARKDOWN)


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shortcut buttons clicked in chat."""
    query = update.callback_query
    data = query.data

    if data == "stop_current_job":
        chat_id = update.effective_chat.id
        job = active_jobs.get(chat_id)
        if job:
            job.cancel_event.set()
            job.pipeline.cancel()
            await query.answer("🛑 正在停止任务并清理资源...")
            try:
                await query.edit_message_text(
                    "🛑 **【正在停止任务...】**\n已收到终止指令，正在立即关闭所有下载通道并清理暂存文件，请稍候...",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        else:
            await query.answer("⚪ 任务已完成或没有正在运行的任务。")
        return

    await query.answer()

    if data == "help":
        await help_command(update, context)
    elif data == "yande_help":
        await yande_help_command(update, context)
    elif data == "status":
        await status_command(update, context)
    elif data == "sample":
        await sample_command(update, context)
    elif data == "clean":
        await clean_command(update, context)


@dataclass
class QueuedYandeItem:
    file_id: str
    caption: str
    message_id: int
    is_document: bool


class YandeBatchCoordinator:
    """Coordinates batching, debouncing and processing of forwarded Yande images."""

    def __init__(self):
        self._queues: Dict[int, List[QueuedYandeItem]] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._locks: Dict[int, asyncio.Lock] = {}

    def get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def enqueue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg:
            return

        chat_id = update.effective_chat.id
        file_id = ""
        is_doc = False
        if msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
            file_id = msg.document.file_id
            is_doc = True
        else:
            return

        item = QueuedYandeItem(
            file_id=file_id,
            caption=msg.caption or "",
            message_id=msg.message_id,
            is_document=is_doc
        )

        lock = self.get_lock(chat_id)
        async with lock:
            if chat_id not in self._queues:
                self._queues[chat_id] = []
            self._queues[chat_id].append(item)

            if chat_id not in self._tasks or self._tasks[chat_id].done():
                self._tasks[chat_id] = asyncio.create_task(
                    self._process_queue(chat_id, context)
                )

    async def _process_queue(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        # Debounce to collect rapid forwarded messages (Telegram sends multi-select forwards in ~0.5-1.2s)
        await asyncio.sleep(0.8)

        status_msg = None
        processed_count = 0
        success_count = 0
        yande_temp_dir = Path(settings.output_dir) / "yande"
        yande_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            lock = self.get_lock(chat_id)
            initial_count = len(self._queues.get(chat_id, []))
            if initial_count == 0:
                return

            if initial_count == 1:
                status_text = "🔍 正在解析图片并搜寻 Yande.re 最高清原图..."
            else:
                status_text = f"📥 已接收 <b>{initial_count}</b> 张转发图片，正在批量搜寻 Yande.re 最高清原图..."

            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=status_text,
                parse_mode=ParseMode.HTML
            )

            while True:
                item = None
                total_current = 0
                async with lock:
                    queue = self._queues.get(chat_id, [])
                    if queue:
                        item = queue.pop(0)
                        total_current = processed_count + 1 + len(queue)

                if not item:
                    # Brief wait to catch any lagging forwarded message
                    await asyncio.sleep(0.5)
                    async with lock:
                        queue = self._queues.get(chat_id, [])
                        if queue:
                            continue
                    break

                processed_count += 1
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

                if total_current > 1 and status_msg:
                    try:
                        await status_msg.edit_text(
                            f"⏳ 正在处理 [<b>{processed_count}/{total_current}</b>]：深度检索并获取原图...",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass

                post = None
                # 1. Quick match from caption
                if item.caption:
                    post_id = yande_finder.extract_id_from_text(item.caption)
                    if post_id:
                        post = await yande_finder.get_post_by_id(post_id)

                # 2. Reverse search image bytes if no match from caption
                if not post:
                    try:
                        tg_file = await context.bot.get_file(item.file_id)
                        img_bytes = bytes(await tg_file.download_as_bytearray())
                        post = await yande_finder.find_post_from_image(img_bytes)
                    except Exception as err:
                        logger.error(f"Error fetching/searching forwarded image: {err}")

                if not post:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ 第 <b>{processed_count}</b> 张图片未能成功匹配到 Yande.re 原图（可能非 Yande 收录或裁切严重）。",
                        parse_mode=ParseMode.HTML,
                        reply_to_message_id=item.message_id
                    )
                    continue

                # 3. Download and send original
                try:
                    if total_current > 1 and status_msg:
                        try:
                            await status_msg.edit_text(
                                f"⏳ 正在处理 [<b>{processed_count}/{total_current}</b>]：下载无损原图 (Yande #{post.id}, {post.width}×{post.height})...",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception:
                            pass

                    local_file = await yande_finder.download_image(post, yande_temp_dir)
                    file_size = local_file.stat().st_size
                    file_size_mb = round(file_size / (1024 * 1024), 2)

                    if file_size <= 49.5 * 1024 * 1024:
                        with open(local_file, "rb") as f:
                            await context.bot.send_document(
                                chat_id=chat_id,
                                document=f,
                                filename=local_file.name,
                                caption=post.format_caption(),
                                parse_mode=ParseMode.HTML,
                                reply_to_message_id=item.message_id
                            )
                        local_file.unlink(missing_ok=True)
                    else:
                        direct_url = tunnel_server.get_file_url(local_file)
                        huge_text = (
                            f"{post.format_caption()}\n\n"
                            f"🚀 <b>【超大文件直链（超过 TG 50MB 限制）】</b>\n"
                            f"👉 <a href=\"{direct_url}\">📥 点击极速下载完整无损原件 ({file_size_mb} MB)</a>"
                        )
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=huge_text,
                            parse_mode=ParseMode.HTML,
                            reply_to_message_id=item.message_id
                        )

                    success_count += 1
                except Exception as dl_err:
                    logger.error(f"Error downloading/sending Yande #{post.id}: {dl_err}")
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ 发送 Yande #{post.id} 原图失败：{dl_err}",
                        reply_to_message_id=item.message_id
                    )

            if status_msg:
                try:
                    finish_text = (
                        f"🎉 <b>Yande 原图处理完毕！</b>\n"
                        f"• 成功提取交付：<b>{success_count}</b> 张高清原图\n"
                        f"• 格式：无损文件文档 (Document) 发送，保持 100% 原始母盘细节！"
                    )
                    await status_msg.edit_text(finish_text, parse_mode=ParseMode.HTML)
                    await asyncio.sleep(8)
                    await status_msg.delete()
                except Exception:
                    pass

        except Exception as queue_err:
            logger.error(f"Queue processing error for chat {chat_id}: {queue_err}")
        finally:
            async with lock:
                self._queues.pop(chat_id, None)
                self._tasks.pop(chat_id, None)


yande_batch_coordinator = YandeBatchCoordinator()


async def handle_photo_or_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle forwarded or direct photos/images for Yande.re HD original extraction."""
    await yande_batch_coordinator.enqueue(update, context)


async def handle_yande_text_request(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Handle direct Yande post ID or URL sent in text."""
    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text(
        f"🔍 正在获取 Yande #{post_id} 原图信息...",
        parse_mode=ParseMode.HTML
    )
    post = await yande_finder.get_post_by_id(post_id)
    if not post:
        await status_msg.edit_text(f"⚠️ 未在 Yande.re 找到 ID 为 {post_id} 的帖子。")
        return

    await status_msg.edit_text(
        f"⏳ 正在下载最高清原图 (<code>{post.width} × {post.height}</code>, <code>{post.size_mb} MB</code>)...",
        parse_mode=ParseMode.HTML
    )
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    yande_temp_dir = Path(settings.output_dir) / "yande"
    try:
        local_file = await yande_finder.download_image(post, yande_temp_dir)
        file_size = local_file.stat().st_size
        file_size_mb = round(file_size / (1024 * 1024), 2)

        if file_size <= 49.5 * 1024 * 1024:
            with open(local_file, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=local_file.name,
                    caption=post.format_caption(),
                    parse_mode=ParseMode.HTML
                )
            local_file.unlink(missing_ok=True)
            await status_msg.delete()
        else:
            direct_url = tunnel_server.get_file_url(local_file)
            huge_text = (
                f"{post.format_caption()}\n\n"
                f"🚀 <b>【超大文件直链（超过 TG 50MB 限制）】</b>\n"
                f"👉 <a href=\"{direct_url}\">📥 点击极速下载完整无损原件 ({file_size_mb} MB)</a>"
            )
            await status_msg.edit_text(huge_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error handling Yande text request: {e}")
        await status_msg.edit_text(f"❌ 下载原图失败：{e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming user message with Pinterest links or Yande text IDs."""
    text = update.message.text or ""
    # Check if user sent a raw cookie string directly (e.g. from mobile browser)
    if ("_pinterest_sess" in text or "_auth" in text) and not PINTEREST_URL_REGEX.search(text):
        save_cookie_content(text)
        await update.message.reply_text(
            "🍪 **已自动识别并配置 Pinterest 登录凭证 (Cookies)！**\n\n"
            "✅ 凭证已即时生效，安全权限已提升为账号全量模式。\n"
            "👉 现在请直接发送您的图板链接，机器人将为您提取包含分级保护在内的全部插画！",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Check if pending encrypted cookie file is waiting for a password
    pending_file = settings.base_dir / "cookies_pending.json"
    if pending_file.is_file() and not PINTEREST_URL_REGEX.search(text) and not text.startswith("/"):
        from utils.cookie_helper import process_uploaded_cookie_file
        ok, msg = process_uploaded_cookie_file(pending_file, password=text.strip())
        if ok:
            pending_file.unlink(missing_ok=True)
            await update.message.reply_text(
                f"🎉 **{msg}**\n\n"
                "Pinterest 账号权限已生效！现在发送图板链接将自动提取 100% 完整原图（包括年龄限制与私密图片）。",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    match = PINTEREST_URL_REGEX.search(text)
    if not match:
        # Check if text contains Yande ID or URL
        yande_id = yande_finder.extract_id_from_text(text)
        if yande_id:
            await handle_yande_text_request(update, context, yande_id)
            return

        await update.message.reply_text(
            "⚠️ 未检测到有效链接或图片。\n\n"
            "👉 <b>支持以下操作</b>：\n"
            "1. <b>多选转发 Yande 每日热图</b>：在 TG 群中多选喜欢的图片直接一口气转发给我，自动获取最高清原图。\n"
            "2. <b>发送 Pinterest 链接</b>：发送图板或单 Pin 链接进行批量挖掘与打包。\n"
            "3. <b>发送 Yande 链接或 ID</b>：例如发送 <code>https://yande.re/post/show/1270146</code> 或 <code>1270146</code>。",
            parse_mode=ParseMode.HTML,
            reply_markup=get_shortcut_keyboard()
        )
        return

    url = match.group(1)
    chat_id = update.effective_chat.id

    # 1. Immediate visual response: sending chat action (typing)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    cancel_event = threading.Event()
    pipeline = BoardPipeline()

    # 2. Immediate acknowledgment showing clearly that the bot is WORKING with a Stop button
    status_msg = await update.message.reply_text(
        f"⚙️ **【正在工作中...】**\n"
        f"🚀 **已成功接收任务！**\n"
        f"🔗 链接：`{url}`\n"
        f"⏳ 正在极速解析图板并深度检索真正原图，请稍候...",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_progress_keyboard()
    )

    active_jobs[chat_id] = ActiveJob(
        chat_id=chat_id,
        board_url=url,
        pipeline=pipeline,
        cancel_event=cancel_event,
        status_msg=status_msg
    )

    loop = asyncio.get_running_loop()
    last_update_time = [0.0]

    def on_progress(current: int, total: int, result):
        if cancel_event.is_set():
            return
        now = time.time()
        # Throttle updates to avoid Telegram 429 flood rate limit (every 4s or final)
        if now - last_update_time[0] >= 4.0 or current == total:
            last_update_time[0] = now
            pct = int((current / total) * 100) if total else 0
            
            res_str = result.final_resolution if result else "处理中"
            higher_badge = "🔥 挖掘出更高清原图" if (result and result.is_higher_res) else "📌 官方原图兜底"
            
            now_str = datetime.now().strftime("%H:%M:%S")
            prog_text = (
                f"⚙️ **【正在工作中...】**\n\n"
                f"📊 **当前进度**：`[{current}/{total}]` ({pct}%)\n"
                f"🕒 **实时心跳**：`{now_str}` (活跃运行中)\n"
                f"🖼️ **最新处理**：Pin `{result.pin_id if result else ''}`\n"
                f"📐 **最终分辨率**：`{res_str}`\n"
                f"🔍 **处理结果**：{higher_badge}\n"
                f"⏳ 正在继续处理下一张，如需终止请点下方按钮..."
            )
            async def _safe_edit():
                try:
                    await status_msg.edit_text(
                        prog_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_progress_keyboard()
                    )
                except Exception:
                    pass

            asyncio.run_coroutine_threadsafe(_safe_edit(), loop)

    try:
        # Send typing action while working
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Run pipeline in executor
        pkg_result = await loop.run_in_executor(
            None,
            lambda: pipeline.run(url, on_progress=on_progress, cancel_event=cancel_event)
        )

        # Check if user manually stopped the task
        if cancel_event.is_set():
            await status_msg.edit_text(
                f"🛑 **【任务已手动停止】**\n\n"
                f"• 目标链接：`{url}`\n"
                f"• 当前状态：已根据您的指令强制终止\n"
                f"• 资源清理：已清空本次任务未完成的临时文件，不占用磁盘空间。\n\n"
                "随时可以发送新的图板链接或单张 Pin 链接继续使用！",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_shortcut_keyboard()
            )
            return

        if pkg_result.total_pins == 0:
            await status_msg.edit_text(
                "⚠️ 未在此链接中找到任何 Pin 图片，请检查图板是否公开或链接是否正确。"
            )
            return

        # Check direct download link from tunnel_server
        direct_url = tunnel_server.get_file_url(pkg_result.zip_path)
        direct_section = ""
        if direct_url:
            direct_section = (
                f"\n\n🚀 **【手机满速高速直链下载（推荐）】**\n"
                f"👉 [📥 点击一键下载【完整合集 ZIP】({pkg_result.total_size_mb} MB)]({direct_url})\n"
                f"*(包含全部 {pkg_result.total_pins} 张原图与报告的完整单一压缩包，无需解压任何分卷)*"
            )

        delivery_hint = (
            f"📦 由于总大小（{pkg_result.total_size_mb} MB）超出 Telegram 50MB 上传限制，下方将通过分卷形式同步发送 Telegram 备份；\n"
            f"💡 **强烈建议直接点击上方直链，一键直接保存完整无损大 ZIP！**"
            if len(pkg_result.all_zip_parts) > 1 else
            "📦 正在准备交付文件..."
        )

        summary_text = (
            f"🎉 **【工作中任务已全部完成！】**\n\n"
            f"📊 **数据统计**：\n"
            f"• 总计处理 Pin 数：`{pkg_result.total_pins}`\n"
            f"• 成功获取：`{pkg_result.success_count}`\n"
            f"• 挖掘出更高清原图：`{pkg_result.higher_res_count}` 张\n"
            f"• 总打包大小：`{pkg_result.total_size_mb} MB`"
            f"{direct_section}\n\n"
            f"{delivery_hint}"
        )
        await status_msg.edit_text(summary_text, parse_mode=ParseMode.MARKDOWN)

        # Telegram Document delivery strategy:
        # If <= 3 parts and each <= 49MB, send directly to Telegram chat.
        # If huge board (>3 parts), recommend direct link to avoid 30+ message spam.
        parts_to_send = pkg_result.all_zip_parts if len(pkg_result.all_zip_parts) <= 3 else [pkg_result.all_zip_parts[0]]

        for idx, part in enumerate(parts_to_send, start=1):
            part_size_mb = round(part.stat().st_size / (1024 * 1024), 2)
            if part_size_mb > 49.0:
                continue
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            caption_suffix = f" (第 {idx}/{len(pkg_result.all_zip_parts)} 卷)" if len(pkg_result.all_zip_parts) > 1 else ""
            sent = False
            for attempt in range(1, 4):
                try:
                    with open(part, "rb") as f:
                        await update.message.reply_document(
                            document=f,
                            filename=part.name,
                            caption=f"📁 {part.name}{caption_suffix} ({part_size_mb} MB)\n包含高清原图与 report.csv 报告。",
                            read_timeout=1800.0,
                            write_timeout=1800.0
                        )
                    sent = True
                    break
                except Exception as upload_err:
                    logger.warning(f"Upload attempt {attempt} failed: {upload_err}")
                    await asyncio.sleep(2)

            if not sent:
                await update.message.reply_text(
                    f"⚠️ 上传至 Telegram 网络超时，请点击上方的高速直链在浏览器中直接下载：`{part.name}`"
                )

        if len(pkg_result.all_zip_parts) > 3:
            await update.message.reply_text(
                f"ℹ️ 由于该图板包含大量图片（总计 {len(pkg_result.all_zip_parts)} 个分卷，共 {pkg_result.total_size_mb} MB），"
                f"为防刷屏与方便手机解压，推荐直接使用上方的 **高速直链** 一键下载完整无损包！"
            )

    except Exception as e:
        logger.error(f"Telegram Bot error processing {url}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ 处理过程中遇到错误：`{str(e)}`", parse_mode=ParseMode.MARKDOWN)
    finally:
        active_jobs.pop(chat_id, None)


def run_bot(token: Optional[str] = None):
    """Start the Telegram bot."""
    bot_token = token or settings.telegram_bot_token
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured!")
        return

    from telegram.request import HTTPXRequest

    tunnel_server.start()
    logger.info("Initializing Telegram Bot with generous upload timeouts (1800s)...")
    request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=60.0,
        read_timeout=1800.0,
        write_timeout=1800.0,
        pool_timeout=60.0
    )

    async def periodic_cleanup_task():
        """Background task running every 30 minutes to clean expired files."""
        from utils.cleanup import storage_manager
        while True:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                res = storage_manager.run_full_cleanup()
                if res.get("freed_mb", 0) > 0:
                    logger.info(f"Periodic background cleanup freed {res['freed_mb']} MB.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Periodic cleanup loop error: {e}")

    async def on_post_init(application):
        await set_bot_commands(application)
        from utils.cleanup import storage_manager
        storage_manager.run_full_cleanup()
        asyncio.create_task(periodic_cleanup_task())

    app = (
        ApplicationBuilder()
        .token(bot_token)
        .request(request)
        .concurrent_updates(True)
        .post_init(on_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("yande", yande_help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sample", sample_command))
    app.add_handler(CommandHandler("cookie", cookie_command))
    app.add_handler(CommandHandler("clearcookie", clearcookie_command))
    app.add_handler(CommandHandler("decrypt", decrypt_command))
    app.add_handler(CommandHandler("saucenao", saucenao_command))
    app.add_handler(CommandHandler("clean", clean_command))
    app.add_handler(CommandHandler("cleanup", clean_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", stop_command))

    # Yande.re photo & image document handlers (forwarded or direct)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_or_image))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo_or_image))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f"Telegram Bot Update error: {context.error}")

    app.add_error_handler(global_error_handler)

    logger.info("Telegram Bot is running! Press Ctrl+C to stop.")
    app.run_polling()
