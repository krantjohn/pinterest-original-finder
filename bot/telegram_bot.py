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
from utils.logger import logger
from utils.tunnel_server import tunnel_server

PINTEREST_URL_REGEX = re.compile(
    r"(https?://(?:www\.|[a-z]{2}\.)?pinterest\.[\w.]+(?:/pin/[^/\s?#]+|/[^/\s?#]+/[^/\s?#]+/?)|https?://pin\.it/[^\s?#]+)",
    re.IGNORECASE
)

SAMPLE_URL = "https://pin.it/6IyIv54kK"

def get_shortcut_keyboard():
    """Return inline keyboard with quick actions."""
    keyboard = [
        [
            InlineKeyboardButton("📖 使用帮助", callback_data="help"),
            InlineKeyboardButton("⚡ 样例测试", callback_data="sample")
        ],
        [
            InlineKeyboardButton("📊 运行状态", callback_data="status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def set_bot_commands(application):
    """Register shortcut commands in Telegram Bot menu."""
    commands = [
        BotCommand("start", "启动机器人与打开功能面板"),
        BotCommand("help", "使用帮助与支持格式"),
        BotCommand("status", "查看服务器与机器人状态"),
        BotCommand("sample", "获取测试图板链接快速体验"),
    ]
    await application.bot.set_my_commands(commands)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_text = (
        "👋 **你好！我是 Pinterest 高清原图提取机器人 (Pinnta)**\n\n"
        "⚡ **核心能力**：\n"
        "• 输入 Pinterest 图板或单张 Pin 链接\n"
        "• 优先通过作者 Outbound / 来源页面寻找原始无压缩超大图\n"
        "• 自动通过反向搜图（Pixiv / Twitter / ArtStation / 个人站）兜底补全\n"
        "• 自动打包成 ZIP 文件并附带完整处理报告（CSV/JSON）直接发回\n\n"
        "📌 **使用方式**：\n"
        "👉 **直接把 Pinterest 链接发给我即可开始！**\n"
        "（支持完整图板链接、单 Pin 链接、手机端 `pin.it` 短链）\n\n"
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
        "📖 **使用指南 & 常见问题**\n\n"
        "1. **支持哪些链接？**\n"
        "   • 手机 App 分享的短链接：`https://pin.it/xxxxxx`\n"
        "   • 完整图板链接：`https://www.pinterest.com/username/boardname/`\n"
        "   • 单张 Pin：`https://www.pinterest.com/pin/123456789/`\n\n"
        "2. **寻找原图的原理？**\n"
        "   Pinterest 会压缩图片。机器人会深入来源页面（如 Pixiv / Twitter / 摄影站）抓取未压缩母盘，或通过反向搜图匹配超清版本。\n\n"
        "3. **怎么接收结果？**\n"
        "   处理完毕后，机器人会把打包好的 **ZIP 压缩包** 发回此聊天窗口，手机直接点击保存即可。"
    )
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_shortcut_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    status_text = (
        "🟢 **系统运行状态正常**\n\n"
        "• 状态：`在线工作中 (Online)`\n"
        "• 检索通道：`来源穿透 (Tier 1)` + `反向搜图 (Tier 2)` + `官方原图 (Tier 3)`\n"
        "• 打包格式：`ZIP 压缩包 + UTF-8 CSV 明细报告`\n"
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


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shortcut buttons clicked in chat."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "help":
        await help_command(update, context)
    elif data == "status":
        await status_command(update, context)
    elif data == "sample":
        await sample_command(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming user message with Pinterest links."""
    text = update.message.text or ""
    match = PINTEREST_URL_REGEX.search(text)
    if not match:
        await update.message.reply_text(
            "⚠️ 未检测到有效的 Pinterest 链接。\n请直接发送 Pinterest 图板或 Pin 链接，例如：\n"
            "`https://pin.it/6IyIv54kK`\n或点击下方快捷菜单：",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_shortcut_keyboard()
        )
        return

    url = match.group(1)
    chat_id = update.effective_chat.id

    # 1. Immediate visual response: sending chat action (typing)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # 2. Immediate acknowledgment showing clearly that the bot is WORKING
    status_msg = await update.message.reply_text(
        f"⚙️ **【正在工作中...】**\n"
        f"🚀 **已成功接收任务！**\n"
        f"🔗 链接：`{url}`\n"
        f"⏳ 正在极速解析图板并深度检索真正原图，请稍候...",
        parse_mode=ParseMode.MARKDOWN
    )

    loop = asyncio.get_running_loop()
    pipeline = BoardPipeline()

    last_update_time = [0.0]

    def on_progress(current: int, total: int, result):
        now = time.time()
        # Throttle updates to avoid Telegram 429 flood rate limit (every 4s or final)
        if now - last_update_time[0] >= 4.0 or current == total:
            last_update_time[0] = now
            pct = int((current / total) * 100) if total else 0
            
            res_str = result.final_resolution if result else "处理中"
            higher_badge = "🔥 挖掘出更高清原图" if (result and result.is_higher_res) else "📌 官方原图兜底"
            
            prog_text = (
                f"⚙️ **【正在工作中...】**\n\n"
                f"📊 **当前进度**：`[{current}/{total}]` ({pct}%)\n"
                f"🖼️ **最新处理**：Pin `{result.pin_id if result else ''}`\n"
                f"📐 **最终分辨率**：`{res_str}`\n"
                f"🔍 **处理结果**：{higher_badge}\n"
                f"⏳ 正在继续处理下一张，请稍候..."
            )
            async def _safe_edit():
                try:
                    await status_msg.edit_text(prog_text, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass

            asyncio.run_coroutine_threadsafe(_safe_edit(), loop)

    try:
        # Send typing action while working
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Run pipeline in executor
        pkg_result = await loop.run_in_executor(
            None,
            lambda: pipeline.run(url, on_progress=on_progress)
        )

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

    app = (
        ApplicationBuilder()
        .token(bot_token)
        .request(request)
        .post_init(set_bot_commands)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sample", sample_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    logger.info("Telegram Bot is running! Press Ctrl+C to stop.")
    app.run_polling()
