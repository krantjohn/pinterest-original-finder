"""
Telegram Bot implementation for Pinterest Original Finder.
Listens for Pinterest board or pin links, tracks progress,
and sends back the packaged ZIP file containing original images and reports.
"""
import asyncio
import os
import re
import time
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from config import settings
from core.pipeline import BoardPipeline
from utils.logger import logger

PINTEREST_URL_REGEX = re.compile(
    r"(https?://(?:www\.|[a-z]{2}\.)?pinterest\.[\w.]+(?:/pin/[^/\s?#]+|/[^/\s?#]+/[^/\s?#]+/?)|https?://pin\.it/[^\s?#]+)",
    re.IGNORECASE
)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_text = (
        "👋 **你好！我是 Pinterest 高清原图提取机器人**\n\n"
        "🎯 **核心功能**：\n"
        "• 输入 Pinterest 图板或单张 Pin 链接\n"
        "• 优先通过作者 Outbound / 来源页面寻找原始无压缩超大图\n"
        "• 自动通过反向搜图（Pixiv / Twitter / ArtStation / 个人站）兜底补全\n"
        "• 自动打包成 ZIP 文件并附带完整处理报告（CSV/JSON）直接发回\n\n"
        "📌 **直接发送链接给我即可开始处理！**\n"
        "例如：`https://www.pinterest.com/username/boardname/` 或 `https://pin.it/xxx`"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming user message and extract Pinterest board links."""
    text = update.message.text or ""
    match = PINTEREST_URL_REGEX.search(text)
    if not match:
        await update.message.reply_text(
            "⚠️ 未检测到有效的 Pinterest 链接。\n请发送 Pinterest 图板或 Pin 链接，例如：\n"
            "`https://www.pinterest.com/user/board/` 或 `https://pin.it/xyz`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    url = match.group(1)
    status_msg = await update.message.reply_text(
        f"🚀 **已收到链接**：`{url}`\n正在解析图板并初始化任务...",
        parse_mode=ParseMode.MARKDOWN
    )

    loop = asyncio.get_running_loop()
    pipeline = BoardPipeline()

    last_update_time = [0.0]
    last_reported_index = [0]

    def on_progress(current: int, total: int, result):
        now = time.time()
        # Throttle updates to avoid Telegram 429 flood rate limit (at most every 5 seconds)
        if now - last_update_time[0] >= 5.0 or current == total:
            last_update_time[0] = now
            last_reported_index[0] = current
            pct = int((current / total) * 100) if total else 0
            res_str = result.final_resolution if result else "处理中"
            prog_text = (
                f"⏳ **正在处理图板**：\n"
                f"进度：`[{current}/{total}]` ({pct}%)\n"
                f"最新完成：`{result.pin_id if result else ''}` ({res_str})\n"
                f"渠道：`{result.status_label if result else ''}`"
            )
            # Schedule message edit in the asyncio loop
            asyncio.run_coroutine_threadsafe(
                status_msg.edit_text(prog_text, parse_mode=ParseMode.MARKDOWN),
                loop
            )

    try:
        # Run blocking pipeline in executor thread
        pkg_result = await loop.run_in_executor(
            None,
            lambda: pipeline.run(url, on_progress=on_progress)
        )

        if pkg_result.total_pins == 0:
            await status_msg.edit_text(
                "⚠️ 未在此链接中找到任何 Pin 图片，请检查图板是否公开或链接是否正确。"
            )
            return

        summary_text = (
            f"🎉 **处理完成！**\n\n"
            f"📊 **数据统计**：\n"
            f"• 总计处理 Pin 数：`{pkg_result.total_pins}`\n"
            f"• 成功获取：`{pkg_result.success_count}`\n"
            f"• 找到更高清原图：`{pkg_result.higher_res_count}`\n"
            f"• 总打包大小：`{pkg_result.total_size_mb} MB`\n\n"
            f"📦 正在上传 ZIP 压缩包与报告..."
        )
        await status_msg.edit_text(summary_text, parse_mode=ParseMode.MARKDOWN)

        # Send ZIP parts
        for part in pkg_result.all_zip_parts:
            part_size_mb = round(part.stat().st_size / (1024 * 1024), 2)
            if part_size_mb > 49.0:
                await update.message.reply_text(
                    f"⚠️ 文件 `{part.name}` 大小为 {part_size_mb}MB，超过 Telegram 机器人 50MB 上传限制。\n"
                    f"文件已保存在服务器本地路径：`{part.resolve()}`"
                )
            else:
                with open(part, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=part.name,
                        caption=f"📁 {part.name} ({part_size_mb} MB)"
                    )

    except Exception as e:
        logger.error(f"Telegram Bot error processing {url}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ 处理过程中遇到错误：`{str(e)}`", parse_mode=ParseMode.MARKDOWN)


def run_bot(token: Optional[str] = None):
    """Start the Telegram bot."""
    bot_token = token or settings.telegram_bot_token
    if not bot_token:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not configured! "
            "Please set TELEGRAM_BOT_TOKEN environment variable or pass --token argument."
        )
        return

    logger.info("Initializing Telegram Bot...")
    app = ApplicationBuilder().token(bot_token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    logger.info("Telegram Bot is running! Press Ctrl+C to stop.")
    app.run_polling()
