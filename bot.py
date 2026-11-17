import asyncio
import glob
import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import uuid
from typing import Dict, Any, List, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import requests
import yt_dlp

import zlapi
from zlapi import ZaloAPI, _util
import zlapi._state
from zlapi.models import Message, ThreadType, MessageStyle, MultiMsgStyle
from aiohttp import web

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8811761404:AAHtqn-AfREnp6Fs6lS4W3gxSgD3qVJ_FIU"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
CONFIG_FILE = os.path.join(BASE_DIR, "saved_treo_configs.json")
AUTOCHAT_CONFIG_FILE = os.path.join(BASE_DIR, "saved_autochat_configs.json")
CHATBOT_CONFIG_FILE = os.path.join(BASE_DIR, "saved_chatbot_configs.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

USER_STATES: Dict[int, dict] = {}

# Quản lý tiến trình treo tự động lặp (Treo Zalo): { user_id: { "task": asyncio.Task, "config": {...}, "client": ZaloAPI, "group_ids": [...] } }
RUNNING_TREO_SESSIONS: Dict[int, dict] = {}

# Quản lý phiên Auto Chat (cầu nối trực tiếp nhắn tin): { user_id: { "client": ZaloAPI, "group_ids": [...], "imei": "...", "groups_info": [...] } }
ACTIVE_AUTOCHAT_SESSIONS: Dict[int, dict] = {}

# Quản lý phiên AI Chatbot Zalo (Gemini AI tự động trả lời khi được tag @): { user_id: { "client": ZaloAPI, "group_ids": [...], "gemini_key": "...", "thread": threading.Thread, "running": bool } }
ACTIVE_CHATBOT_SESSIONS: Dict[int, dict] = {}

# Bảng màu chuẩn được Zalo hỗ trợ chính thức
COLOR_OPTIONS = [
    {"id": "none", "name": "Mặc định", "hex": None},
    {"id": "red", "name": "🔴 Đỏ", "hex": "db342e"},
    {"id": "orange", "name": "🟠 Cam", "hex": "f37021"},
    {"id": "green", "name": "🟢 Xanh lá", "hex": "27ae60"},
    {"id": "blue", "name": "🔵 Xanh dương", "hex": "1877f2"},
]


# ==========================================
# QUẢN LÝ LƯU TRỮ CẤU HÌNH TREO & AUTOCHAT
# ==========================================

def load_saved_configs() -> dict:
    """Tải danh sách cấu hình treo đã lưu từ file JSON"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Lỗi đọc file cấu hình treo: {e}")
    return {}


def save_user_treo_config(user_id: int, config_data: dict):
    """Lưu cấu hình treo của người dùng vào file JSON"""
    try:
        all_configs = load_saved_configs()
        to_save = dict(config_data)
        if isinstance(to_save.get("selected_group_ids"), set):
            to_save["selected_group_ids"] = list(to_save["selected_group_ids"])
        all_configs[str(user_id)] = to_save
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(all_configs, f, ensure_ascii=False, indent=2)
        logger.info(f"Đã lưu cấu hình treo cho user {user_id}")
    except Exception as e:
        logger.error(f"Lỗi ghi cấu hình treo: {e}")


def delete_user_treo_config(user_id: int):
    """Xóa cấu hình treo của người dùng"""
    try:
        all_configs = load_saved_configs()
        str_uid = str(user_id)
        if str_uid in all_configs:
            del all_configs[str_uid]
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(all_configs, f, ensure_ascii=False, indent=2)
            logger.info(f"Đã xóa cấu hình treo của user {user_id}")
            return True
    except Exception as e:
        logger.error(f"Lỗi xóa cấu hình treo: {e}")
    return False


def get_user_saved_config(user_id: int) -> dict:
    """Lấy cấu hình đã lưu của 1 user"""
    all_configs = load_saved_configs()
    return all_configs.get(str(user_id))


def load_saved_autochat_configs() -> dict:
    """Tải cấu hình autochat đã lưu từ file JSON"""
    if os.path.exists(AUTOCHAT_CONFIG_FILE):
        try:
            with open(AUTOCHAT_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Lỗi đọc file autochat: {e}")
    return {}


def save_user_autochat_config(user_id: int, config_data: dict):
    """Lưu thông tin đăng nhập và nhóm autochat"""
    try:
        all_configs = load_saved_autochat_configs()
        to_save = dict(config_data)
        if isinstance(to_save.get("selected_group_ids"), set):
            to_save["selected_group_ids"] = list(to_save["selected_group_ids"])
        all_configs[str(user_id)] = to_save
        with open(AUTOCHAT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(all_configs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi cấu hình autochat: {e}")


def get_user_autochat_config(user_id: int) -> dict:
    all_configs = load_saved_autochat_configs()
    return all_configs.get(str(user_id))


def load_saved_chatbot_configs() -> dict:
    """Tải cấu hình chatbot AI Zalo từ file JSON"""
    if os.path.exists(CHATBOT_CONFIG_FILE):
        try:
            with open(CHATBOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Lỗi đọc file chatbot: {e}")
    return {}


def save_user_chatbot_config(user_id: int, config_data: dict):
    """Lưu cấu hình chatbot AI Zalo (IMEI, cookies, groups, gemini_key)"""
    try:
        all_configs = load_saved_chatbot_configs()
        to_save = dict(config_data)
        if isinstance(to_save.get("selected_group_ids"), set):
            to_save["selected_group_ids"] = list(to_save["selected_group_ids"])
        all_configs[str(user_id)] = to_save
        with open(CHATBOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(all_configs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi cấu hình chatbot: {e}")


def get_user_chatbot_config(user_id: int) -> dict:
    all_configs = load_saved_chatbot_configs()
    return all_configs.get(str(user_id))


def parse_cookies_input(cookie_str: str) -> dict:
    """Parser đa năng cho cookies Zalo"""
    if not cookie_str:
        return {}

    text = cookie_str.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            res = {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip()}
            if res:
                return res
        elif isinstance(data, list):
            res = {}
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("key")
                    val = item.get("value") or item.get("val")
                    if name is not None and val is not None:
                        res[str(name).strip()] = str(val).strip()
            if res:
                return res
    except Exception:
        pass

    cookies = {}
    normalized = text.replace("\r\n", ";").replace("\n", ";")
    for part in normalized.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().strip('"').strip("'")
            v = v.strip().strip('"').strip("'")
            if k:
                cookies[k] = v

    if cookies and "zpw_sek" in cookies:
        return cookies

    matches = re.findall(r'([a-zA-Z0-9_\-\.]+)\s*[=:]\s*([^\r\n;,"]+)', text)
    if matches:
        regex_cookies = {k.strip(): v.strip() for k, v in matches if k.strip()}
        if "zpw_sek" in regex_cookies or len(regex_cookies) > len(cookies):
            return regex_cookies

    return cookies


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start"""
    user_name = update.effective_user.first_name or "Bạn"
    text = (
        f"👋 Xin chào {user_name}!\n\n"
        f"Gõ /menu để mở bảng điều khiển."
    )
    await update.message.reply_text(text)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu dạng bảng nút bấm"""
    user_id = update.effective_user.id
    is_running_treo = user_id in RUNNING_TREO_SESSIONS and not RUNNING_TREO_SESSIONS[user_id]["task"].done()
    is_autochat_on = user_id in ACTIVE_AUTOCHAT_SESSIONS
    is_chatbot_on = user_id in ACTIVE_CHATBOT_SESSIONS and ACTIVE_CHATBOT_SESSIONS[user_id].get("running", False)
    saved_cfg = get_user_saved_config(user_id)

    autochat_status = "🟢 Đang bật" if is_autochat_on else "⚪ Đang tắt"
    chatbot_status = "🟢 Đang bật" if is_chatbot_on else "⚪ Đang tắt"
    treo_status = "🟢 Đang chạy" if is_running_treo else "⚪ Đang dừng"

    text = (
        "🤖 <b>BẢNG ĐIỀU KHIỂN BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"• 🧠 Chatbot AI Zalo: <b>{chatbot_status}</b>\n"
        f"• 💬 Auto Chat (Cầu nối): <b>{autochat_status}</b>\n"
        f"• 🚀 Treo Zalo (Lặp tin): <b>{treo_status}</b>\n"
        f"• 💾 Cấu hình treo đã lưu: <b>{'Đã có' if saved_cfg else 'Chưa lưu'}</b>\n\n"
        "Vui lòng chọn tính năng bên dưới:"
    )

    # Hàng 1: Chatbot AI Zalo (Gemini)
    if is_chatbot_on:
        row_chatbot = [
            InlineKeyboardButton("🧠 Trạng Thái AI Chatbot", callback_data="chatbot_status"),
            InlineKeyboardButton("🛑 Tắt AI Chatbot", callback_data="stop_chatbot")
        ]
    else:
        row_chatbot = [
            InlineKeyboardButton("🧠 Bật Chatbot AI Zalo (Gemini)", callback_data="start_chatbot")
        ]

    # Hàng 2: Auto Chat (tính năng nhắn gì gửi nấy)
    if is_autochat_on:
        row_autochat = [
            InlineKeyboardButton("💬 Trạng Thái Auto Chat", callback_data="autochat_status"),
            InlineKeyboardButton("🛑 Tắt Auto Chat", callback_data="stop_autochat")
        ]
    else:
        row_autochat = [
            InlineKeyboardButton("💬 Bật Auto Chat (Cầu Nối)", callback_data="start_autochat")
        ]

    # Hàng 3: Treo lặp
    row_treo = []
    if is_running_treo:
        row_treo.append(InlineKeyboardButton("⚙️ Tùy Chỉnh Treo", callback_data="cfg_open"))
        row_treo.append(InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo"))
    else:
        row_treo.append(InlineKeyboardButton("🚀 Bắt đầu Treo Zalo (Lặp)", callback_data="start_treo"))

    row_manage = [InlineKeyboardButton("📁 Quản Lý Cấu Hình Treo", callback_data="manage_saved_config")]

    row_extra = [
        InlineKeyboardButton("🎵 Tải Nhạc / Video", callback_data="start_tainhac"),
        InlineKeyboardButton("🔄 Làm mới Menu", callback_data="refresh_menu")
    ]

    keyboard = [row_chatbot, row_autochat, row_treo, row_manage, row_extra]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)



# ==========================================
# PHẦN QUẢN LÝ CẤU HÌNH ĐÃ LƯU (TREO LẶP)
# ==========================================

async def show_manage_saved_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị bảng quản lý cấu hình đã lưu và các nút Chạy lại / Xóa"""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    saved_cfg = get_user_saved_config(user_id)
    is_running = user_id in RUNNING_TREO_SESSIONS and not RUNNING_TREO_SESSIONS[user_id]["task"].done()

    if not saved_cfg:
        text = (
            "📁 <b>QUẢN LÝ THIẾT LẬP TREO ZALO</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ Bạn chưa có cấu hình thiết lập treo nào được lưu.\n\n"
            "Khi bạn thiết lập treo lần đầu qua <b>/treozalo</b>, cấu hình (IMEI, Cookies, Nội dung, Delay, Kiểu chữ) "
            "sẽ tự động được lưu lại để bạn có thể chạy lại nhanh chóng bất cứ lúc nào!"
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Thiết Lập Mới (/treozalo)", callback_data="start_treo")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
    else:
        content = saved_cfg.get("spam_content") or saved_cfg.get("content", "")
        delay = saved_cfg.get("delay", 10)
        fmt = saved_cfg.get("format_settings", {})
        selected_groups = saved_cfg.get("selected_group_ids", [])
        imei = saved_cfg.get("zalo_imei", "N/A")

        color_name = "Mặc định"
        for c in COLOR_OPTIONS:
            if c["id"] == fmt.get("color"):
                color_name = c["name"]
                break

        styles = []
        if fmt.get("bold"): styles.append("In đậm")
        if fmt.get("italic"): styles.append("In nghiêng")
        if fmt.get("underline"): styles.append("Gạch chân")
        if fmt.get("strike"): styles.append("Gạch ngang")
        style_str = ", ".join(styles) if styles else "Bình thường"

        text = (
            "📁 <b>QUẢN LÝ THIẾT LẬP TREO ĐÃ LƯU</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"📱 IMEI: <code>{html.escape(imei)}</code>\n"
            f"👥 Số nhóm đã chọn: <b>{len(selected_groups)} nhóm</b>\n"
            f"📝 Nội dung: <i>{html.escape(content)}</i>\n"
            f"⏱ Delay: <b>{delay} giây</b>\n"
            f"🎨 Màu chữ: <b>{color_name}</b>\n"
            f"🔤 Kiểu chữ: <b>{style_str}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👉 Bạn có thể chạy ngay với cấu hình này hoặc Xóa thiết lập:"
        )

        keyboard = []
        if not is_running:
            keyboard.append([InlineKeyboardButton("▶️ Chạy Lại Thiết Lập Này", callback_data="run_saved_config")])
            keyboard.append([InlineKeyboardButton("🚀 Thiết Lập Mới Thay Thế", callback_data="start_treo_fresh")])
        else:
            keyboard.append([InlineKeyboardButton("⚙️ Tùy Chỉnh Phiên Đang Chạy", callback_data="cfg_open")])

        keyboard.append([InlineKeyboardButton("🗑 Xóa Cấu Hình Đã Lưu", callback_data="delete_saved_config")])
        keyboard.append([InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        try:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)


async def handle_delete_saved_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xóa cấu hình đã lưu của người dùng"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    ok = delete_user_treo_config(user_id)

    if ok:
        text = "✅ <b>Đã xóa thông tin thiết lập treo thành công!</b>"
    else:
        text = "ℹ️ Không tìm thấy thiết lập nào để xóa."

    keyboard = [
        [InlineKeyboardButton("🚀 Thiết Lập Treo Mới", callback_data="start_treo")],
        [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
    ]
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest:
        pass


# ==========================================
# PHẦN 1: TẢI NHẠC / VIDEO (YOUTUBE, TIKTOK)
# ==========================================

async def tainhac_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khởi động lệnh /tainhac"""
    user_id = update.effective_user.id
    USER_STATES[user_id] = {"state": "WAITING_MUSIC_URL"}

    prompt = (
        "🎵 <b>TẢI NHẠC & VIDEO TỰ ĐỘNG</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Hỗ trợ: <b>YouTube</b>, <b>TikTok</b>, Facebook, Reels, Shorts...\n\n"
        "👉 Vui lòng dán <b>Link liên kết</b> bạn muốn tải vào đây:\n"
        "<i>(Gõ /cancel để hủy)</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, parse_mode="HTML")
    else:
        await update.message.reply_text(prompt, parse_mode="HTML")


def get_quick_title_oembed(url: str) -> str:
    """Lấy tiêu đề nhanh (<0.3s) qua oEmbed API công khai không tốn tài nguyên"""
    try:
        if "youtube.com" in url or "youtu.be" in url:
            oe_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
            req = urllib.request.Request(oe_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    return data.get("title", "Video YouTube")
        elif "tiktok.com" in url:
            oe_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(oe_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    return data.get("title", "Video TikTok")
    except Exception as e:
        logger.warning(f"oEmbed fetch fail: {e}")
    return "Media Video / Audio"


async def handle_music_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi người dùng gửi đường link video/nhạc"""
    user_id = update.effective_user.id
    url = update.message.text.strip()

    if not url.startswith("http://") and not url.startswith("https://"):
        await update.message.reply_text("⚠️ Link không hợp lệ! Vui lòng gửi liên kết bắt đầu bằng http:// hoặc https://")
        return

    status_msg = await update.message.reply_text("🔍 Đang nhận diện liên kết...")

    loop = asyncio.get_running_loop()
    title = await loop.run_in_executor(None, get_quick_title_oembed, url)

    USER_STATES[user_id] = {
        "state": "WAITING_FORMAT_CHOICE",
        "url": url,
        "title": title
    }

    keyboard = [
        [
            InlineKeyboardButton("🎵 Tải Âm Thanh (MP3)", callback_data="dl_format_mp3"),
            InlineKeyboardButton("🎬 Tải Video (MP4)", callback_data="dl_format_mp4")
        ],
        [
            InlineKeyboardButton("❌ Hủy Bỏ", callback_data="dl_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await status_msg.edit_text(
        f"🎯 <b>Đã tìm thấy:</b> <i>{html.escape(title)}</i>\n\n"
        "👉 Bạn muốn tải ở định dạng nào?",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def handle_music_format_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lựa chọn MP3 hoặc MP4"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id)

    if not user_data or "url" not in user_data:
        await query.edit_message_text("⚠️ Liên kết đã hết hạn hoặc không tìm thấy. Vui lòng gửi lại link!")
        return

    data = query.data
    if data == "dl_cancel":
        USER_STATES.pop(user_id, None)
        await query.edit_message_text("❌ Đã hủy tải.")
        return

    url = user_data["url"]
    title = user_data.get("title", "download")
    is_mp3 = (data == "dl_format_mp3")
    ext = "mp3" if is_mp3 else "mp4"

    format_desc = "Âm thanh MP3" if is_mp3 else "Video MP4"
    status_msg = await query.edit_message_text(f"⏳ Đang tải xuống <b>{format_desc}</b>...\nVui lòng đợi giây lát!", parse_mode="HTML")

    file_uuid = uuid.uuid4().hex[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_uuid}.%(ext)s")

    def run_download():
        ydl_opts = {
            'outtmpl': output_template,
            'max_filesize': 48 * 1024 * 1024,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
        }
        if is_mp3:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'best[ext=mp4][filesize<=48M]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
                'merge_output_format': 'mp4',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, run_download)
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Lỗi khi tải yt-dlp: {err_msg}")
        if "File is larger than max-filesize" in err_msg:
            await status_msg.edit_text("❌ File có dung lượng vượt quá giới hạn 50MB của bot Telegram! Hãy chọn video ngắn hơn hoặc chọn tải MP3.")
        else:
            await status_msg.edit_text(f"❌ Tải thất bại: {html.escape(err_msg[:200])}")
        USER_STATES.pop(user_id, None)
        return

    found_files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_uuid}.*"))
    if not found_files:
        await status_msg.edit_text("❌ Không tìm thấy file đã tải sau khi xử lý.")
        USER_STATES.pop(user_id, None)
        return

    downloaded_file = found_files[0]
    file_size = os.path.getsize(downloaded_file)
    if file_size > 50 * 1024 * 1024:
        await status_msg.edit_text("❌ File vượt quá 50MB (giới hạn Telegram). Không thể gửi!")
        try:
            os.remove(downloaded_file)
        except Exception:
            pass
        USER_STATES.pop(user_id, None)
        return

    await status_msg.edit_text("📤 Đang gửi file lên Telegram...")

    try:
        if is_mp3:
            with open(downloaded_file, 'rb') as f:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=f,
                    title=title,
                    caption=f"🎵 <b>{html.escape(title)}</b>\nĐã tải bởi @{(await context.bot.get_me()).username}",
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120
                )
        else:
            with open(downloaded_file, 'rb') as f:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=f,
                    caption=f"🎬 <b>{html.escape(title)}</b>\nĐã tải bởi @{(await context.bot.get_me()).username}",
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120
                )
        await status_msg.delete()
    except Exception as e:
        await query.message.reply_text(f"❌ Lỗi gửi file: {e}")
    finally:
        try:
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
        except Exception:
            pass
        USER_STATES.pop(user_id, None)


# ==========================================
# PHẦN 2.5: CHATBOT ZALO AI (TỰ ĐỘNG PHẢN HỒI KHI ĐƯỢC TAG @)
# Tự động dùng AI phản hồi thông minh, KHÔNG CẦN NHẬP API KEY THỦ CÔNG
# ==========================================

def call_gemini_api(prompt: str, api_key: str = None) -> str:
    """Tạo câu trả lời AI thông minh tự động (hỗ trợ cả Gemini API Key lẫn AI Engine miễn phí)"""
    # 1. Nếu có Gemini API Key hợp lệ, ưu tiên gọi Gemini API
    if api_key and api_key.strip():
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key.strip()}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"Bạn là trợ lý ảo thông minh trên Zalo. Hãy trả lời thân thiện, ngắn gọn, súc tích (dưới 80 từ) cho câu hỏi sau:\n{prompt}"}
                        ]
                    }
                ]
            }
            resp = requests.post(url, json=payload, timeout=12)
            data = resp.json()
            if "candidates" in data and len(data["candidates"]) > 0:
                content = data["candidates"][0].get("content", {})
                parts = content.get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip()
        except Exception as e:
            logger.warning(f"Gemini API gặp lỗi: {e}, chuyển sang Free AI Engine fallback...")

    # 2. Tự động dùng AI Engine thông minh (OpenAI/Pollinations) không cần API key
    try:
        url = "https://text.pollinations.ai/"
        system_prompt = "Bạn là trợ lý ảo thông minh trên Zalo. Hãy trả lời tiếng Việt thân thiện, tự nhiên, ngắn gọn (dưới 80 từ)."
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "model": "openai",
            "seed": 42
        }
        resp = requests.post(url, json=payload, timeout=12)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()
    except Exception as e_poll:
        logger.error(f"Lỗi AI Engine fallback: {e_poll}")

    return "Chào bạn, mình là AI Chatbot Zalo! Bạn cần mình hỗ trợ điều gì không?"



class ZaloAIChatbotListener(ZaloAPI):
    """Lớp kế thừa ZaloAPI để lắng nghe sự kiện tin nhắn trong nhóm Zalo"""
    def __init__(self, imei=None, cookies=None, target_groups=None, gemini_key=None, *args, **kwargs):
        super().__init__(imei=imei, cookies=cookies, auto_login=False, *args, **kwargs)
        self.target_groups = set(str(g) for g in (target_groups or []))
        self.gemini_key = gemini_key
        self.is_running = True

    def onMessage(self, mid=None, author_id=None, message=None, message_object=None, thread_id=None, thread_type=ThreadType.USER):
        try:
            if not self.is_running:
                return

            # Chỉ xử lý tin nhắn trong nhóm và nằm trong danh sách nhóm đã chọn
            if thread_type != ThreadType.GROUP:
                return
            if str(thread_id) not in self.target_groups:
                return

            # Bỏ qua tin nhắn do chính bot gửi
            my_id = str(self.user_id) if hasattr(self, "user_id") and self.user_id else str(self._state.cloud_id)
            if str(author_id) == my_id:
                return

            msg_text = ""
            if isinstance(message, str):
                msg_text = message
            elif message_object and hasattr(message_object, "content") and isinstance(message_object.content, str):
                msg_text = message_object.content

            # Kiểm tra xem có mention (tag @) bot hay không
            is_mentioned = False
            raw_mention = None
            if message_object:
                raw_mention = getattr(message_object, "mentions", None) or getattr(message_object, "mention", None)

            if raw_mention:
                try:
                    if isinstance(raw_mention, str):
                        raw_mention = json.loads(raw_mention)
                    if isinstance(raw_mention, list):
                        for m in raw_mention:
                            if str(m.get("uid")) == my_id:
                                is_mentioned = True
                                break
                except Exception:
                    pass

            # Fallback nếu văn bản chứa @
            if not is_mentioned and ("@" in msg_text):
                is_mentioned = True

            if not is_mentioned:
                return

            logger.info(f"AI Chatbot phát hiện được tag trong nhóm {thread_id} bởi {author_id}: {msg_text}")

            clean_prompt = re.sub(r'@[^\s]+', '', msg_text).strip()
            if not clean_prompt:
                clean_prompt = "Chào bạn!"

            # Gọi Gemini AI
            ai_reply = call_gemini_api(clean_prompt, self.gemini_key)

            # Phản hồi lại và tag người hỏi
            tag_text = "@Bạn "
            full_reply_text = f"{tag_text}{ai_reply}"
            mention_data = json.dumps([{
                "pos": 0,
                "len": len(tag_text.strip()),
                "uid": str(author_id),
                "type": 0
            }])

            reply_msg = Message(text=full_reply_text, mention=mention_data)
            try:
                self.sendMentionMessage(reply_msg, groupId=thread_id)
            except Exception as e_send:
                logger.warning(f"Lỗi sendMentionMessage: {e_send}, thử gửi tin nhắn thường...")
                self.sendMessage(Message(text=full_reply_text), thread_id=thread_id, thread_type=ThreadType.GROUP)

        except Exception as e:
            logger.error(f"Lỗi trong onMessage AI Chatbot: {e}", exc_info=True)


def start_chatbot_thread(user_id: int, client: ZaloAPI, group_ids: list, gemini_key: str):
    """Chạy vòng lặp lắng nghe WebSocket trong một luồng riêng biệt"""
    import threading

    # Khởi tạo instance chuyên lắng nghe
    listener = ZaloAIChatbotListener(
        imei=client._imei,
        cookies=client._state.get_cookies(),
        target_groups=group_ids,
        gemini_key=gemini_key
    )
    # Sao chép trạng thái đăng nhập từ client đã login thành công
    listener._state = client._state
    listener._imei = client._imei
    listener.user_id = client.user_id
    listener.cloud_id = client.cloud_id

    def run_listen():
        logger.info(f"Bắt đầu lắng nghe tin nhắn Zalo AI Chatbot cho user {user_id}...")
        try:
            listener.listen(reconnect=5)
        except Exception as e:
            logger.error(f"Luồng listen Zalo kết thúc: {e}")

    t = threading.Thread(target=run_listen, daemon=True)
    t.start()

    ACTIVE_CHATBOT_SESSIONS[user_id] = {
        "client": listener,
        "group_ids": group_ids,
        "gemini_key": gemini_key,
        "thread": t,
        "running": True
    }


async def chatbot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khởi động lệnh /chatbotzalo"""
    user_id = update.effective_user.id

    if user_id in ACTIVE_CHATBOT_SESSIONS and ACTIVE_CHATBOT_SESSIONS[user_id].get("running"):
        session = ACTIVE_CHATBOT_SESSIONS[user_id]
        num_groups = len(session.get("group_ids", []))
        keyboard = [
            [InlineKeyboardButton("🛑 Tắt AI Chatbot", callback_data="stop_chatbot")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
        msg = (
            "🧠 <b>CHATBOT AI ZALO ĐANG BẬT!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Đang hoạt động trên: <b>{num_groups} nhóm</b>\n"
            "🤖 Bộ não: <b>Google Gemini AI</b>\n"
            "👉 Khi có thành viên tag @tài khoản của bạn trong các nhóm này, bot sẽ tự động trả lời thông minh!\n\n"
            "Muốn tắt, bạn bấm nút bên dưới hoặc gõ /stopchatbot."
        )
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        return

    saved_cb = get_user_chatbot_config(user_id)
    saved_auto = get_user_autochat_config(user_id)
    saved_treo = get_user_saved_config(user_id)

    candidate = saved_cb if (saved_cb and saved_cb.get("zalo_imei")) else (saved_auto if (saved_auto and saved_auto.get("zalo_imei")) else saved_treo)

    if candidate and candidate.get("zalo_imei") and candidate.get("zalo_cookies"):
        imei = candidate.get("zalo_imei")
        sel_count = len(candidate.get("selected_group_ids", []))
        has_key = bool(candidate.get("gemini_key"))
        text = (
            "🧠 <b>CẤU HÌNH CHATBOT AI ZALO (GEMINI AI)</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Tính năng: <b>Tự động phân tích và phản hồi thông minh bằng Gemini AI khi có người tag @ bạn trong nhóm Zalo!</b>\n\n"
            f"📱 IMEI đã lưu: <code>{html.escape(imei)}</code>\n"
            f"👥 Nhóm đã lưu: <b>{sel_count} nhóm</b>\n"
            f"🔑 Gemini API Key: <b>{'Đã có sẵn' if has_key else 'Chưa có'}</b>\n\n"
            "👉 Bạn muốn dùng tài khoản Zalo đã lưu hay đăng nhập mới?"
        )
        keyboard = [
            [InlineKeyboardButton("⚡ Dùng Tài Khoản Đã Lưu", callback_data="cb_use_saved")],
            [InlineKeyboardButton("🆕 Nhập IMEI & Cookie Mới", callback_data="cb_start_fresh")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        return

    await chatbot_start_fresh(update, context)


async def chatbot_start_fresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhập IMEI mới cho Chatbot Zalo"""
    user_id = update.effective_user.id
    USER_STATES[user_id] = {"state": "CB_WAITING_IMEI"}

    prompt = (
        "🧠 <b>[CHATBOT AI - BƯỚC 1/4] NHẬP IMEI ZALO</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Vui lòng gửi mã IMEI tài khoản Zalo của bạn:\n"
        "<i>(Gõ /cancel để hủy)</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, parse_mode="HTML")
    else:
        await update.message.reply_text(prompt, parse_mode="HTML")


async def handle_cb_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận IMEI cho Chatbot Zalo"""
    user_id = update.effective_user.id
    imei = update.message.text.strip()
    if not imei:
        await update.message.reply_text("⚠️ IMEI không hợp lệ. Vui lòng gửi lại IMEI:")
        return

    USER_STATES[user_id] = {"state": "CB_WAITING_COOKIES", "zalo_imei": imei}

    text = (
        f"✅ Đã nhận IMEI: <code>{html.escape(imei)}</code>\n\n"
        "🍪 <b>[CHATBOT AI - BƯỚC 2/4] NHẬP COOKIES ZALO</b>\n\n"
        "Vui lòng gửi Cookies tài khoản Zalo của bạn (chuỗi header hoặc JSON):\n"
        "<i>(Bắt buộc phải chứa cookie <b>zpw_sek</b>)</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def handle_cb_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận cookies, đăng nhập và lấy danh sách nhóm cho Chatbot Zalo"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    cookies_input = update.message.text.strip()
    parsed_cookies = parse_cookies_input(cookies_input)

    if not parsed_cookies or "zpw_sek" not in parsed_cookies:
        await update.message.reply_text(
            "⚠️ Cookies không hợp lệ hoặc thiếu <code>zpw_sek</code>!\n"
            "Vui lòng copy lại toàn bộ cookie từ <code>chat.zalo.me</code>:",
            parse_mode="HTML"
        )
        return

    imei = user_data.get("zalo_imei")
    user_data["zalo_cookies"] = parsed_cookies

    status_msg = await update.message.reply_text(
        f"⏳ Đang kết nối Zalo với IMEI <code>{html.escape(imei)}</code>...",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, parsed_cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực Zalo"
        await status_msg.edit_text(
            f"❌ <b>Đăng nhập Zalo thất bại!</b>\n\n<code>{html.escape(err_msg)}</code>\n\n👉 Thử lại với /chatbotzalo",
            parse_mode="HTML"
        )
        USER_STATES.pop(user_id, None)
        return

    if not group_list:
        await status_msg.edit_text("⚠️ Đăng nhập thành công nhưng tài khoản hiện chưa tham gia nhóm nào!")
        USER_STATES.pop(user_id, None)
        return

    user_data["state"] = "CB_WAITING_GROUPS"
    user_data["zalo_client"] = client
    user_data["zalo_groups"] = group_list
    user_data["selected_group_ids"] = set()
    user_data["group_page"] = 0

    reply_markup = build_chatbot_group_keyboard(group_list, set(), page=0)
    prompt_text = (
        f"👥 <b>[CHATBOT AI - BƯỚC 3/3] CHỌN NHÓM ĐỂ AI TRẢ LỜI</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Tìm thấy: <b>{len(group_list)} nhóm</b>\n"
        "👉 Bấm chọn những nhóm bạn muốn AI tự động trả lời khi được tag @, sau đó bấm <b>'🚀 XÁC NHẬN BẬT AI CHATBOT'</b>:"
    )

    await status_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)


async def handle_cb_use_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dùng thông tin tài khoản đã lưu cho Chatbot Zalo"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    saved_cb = get_user_chatbot_config(user_id)
    saved_auto = get_user_autochat_config(user_id)
    saved_treo = get_user_saved_config(user_id)
    candidate = saved_cb if (saved_cb and saved_cb.get("zalo_imei")) else (saved_auto if (saved_auto and saved_auto.get("zalo_imei")) else saved_treo)

    if not candidate:
        await query.edit_message_text("⚠️ Không tìm thấy cấu hình đã lưu. Vui lòng bắt đầu lại bằng /chatbotzalo")
        return

    imei = candidate.get("zalo_imei")
    cookies = candidate.get("zalo_cookies")
    prev_selected = set(candidate.get("selected_group_ids", []))
    saved_gemini_key = candidate.get("gemini_key", "")

    status_msg = await query.edit_message_text("⏳ Đang kết nối Zalo bằng tài khoản đã lưu...", parse_mode="HTML")

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực Zalo"
        await status_msg.edit_text(
            f"❌ <b>Không thể kết nối Zalo với Cookies đã lưu!</b>\n\n<code>{html.escape(err_msg)}</code>\n\n"
            "💡 Cookies có thể đã hết hạn, hãy chọn <b>'🆕 Nhập IMEI & Cookie Mới'</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆕 Nhập IMEI & Cookie Mới", callback_data="cb_start_fresh")]
            ])
        )
        return

    valid_selected = {g["id"] for g in group_list if g["id"] in prev_selected}

    USER_STATES[user_id] = {
        "state": "CB_WAITING_GROUPS",
        "zalo_imei": imei,
        "zalo_cookies": cookies,
        "zalo_client": client,
        "zalo_groups": group_list,
        "selected_group_ids": valid_selected,
        "group_page": 0,
        "gemini_key": saved_gemini_key
    }

    reply_markup = build_chatbot_group_keyboard(group_list, valid_selected, page=0)
    prompt_text = (
        f"👥 <b>[CHATBOT AI - BƯỚC 3/3] CHỌN NHÓM ĐỂ AI TRẢ LỜI</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Tìm thấy: <b>{len(group_list)} nhóm</b> | Đang chọn: <b>{len(valid_selected)} nhóm</b>\n\n"
        "👉 Bấm chọn nhóm và nhấn <b>'🚀 XÁC NHẬN BẬT AI CHATBOT'</b>:"
    )

    await status_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)


def build_chatbot_group_keyboard(group_list: List[dict], selected_ids: Set[str], page: int = 0) -> InlineKeyboardMarkup:
    """Bảng chọn nhóm cho Chatbot AI"""
    PAGE_SIZE = 8
    total_pages = max(1, (len(group_list) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    current_groups = group_list[start_idx:end_idx]

    keyboard = []
    for g in current_groups:
        gid = g["id"]
        gname = g["name"]
        icon = "✅" if gid in selected_ids else "⬜"
        display_name = f"{icon} {gname[:25]}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"cb_grp_toggle_{gid}_{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Trước", callback_data=f"cb_grp_page_{page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="cb_grp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Sau ➡️", callback_data=f"cb_grp_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    all_selected = (len(selected_ids) == len(group_list)) and (len(group_list) > 0)
    toggle_all_btn = (
        InlineKeyboardButton("❌ Bỏ chọn tất cả", callback_data=f"cb_grp_unselect_all_{page}")
        if all_selected
        else InlineKeyboardButton("✅ Chọn tất cả nhóm", callback_data=f"cb_grp_select_all_{page}")
    )
    keyboard.append([toggle_all_btn])

    keyboard.append([
        InlineKeyboardButton(f"🚀 XÁC NHẬN BẬT AI CHATBOT ({len(selected_ids)} nhóm)", callback_data="cb_grp_confirm")
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_cb_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút bấm chọn nhóm Chatbot AI - BẬT NGAY TỰ ĐỘNG KHÔNG CẦN NHẬP KEY"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    data = query.data

    group_list = user_data.get("zalo_groups", [])
    selected_ids: Set[str] = user_data.setdefault("selected_group_ids", set())
    page = user_data.get("group_page", 0)

    if data == "cb_grp_noop":
        return

    if data == "cb_grp_confirm":
        if not selected_ids:
            await query.answer("⚠️ Bạn chưa chọn nhóm nào! Vui lòng chọn ít nhất 1 nhóm.", show_alert=True)
            return

        # Tự động lấy key đã lưu hoặc tự kích hoạt AI Engine tích hợp sẵn
        saved_key = user_data.get("gemini_key") or ""
        await activate_chatbot_session(user_id, user_data, saved_key, query.message, is_edit=True)
        return


    if data.startswith("cb_grp_toggle_"):
        parts = data.split("_")
        gid = parts[3]
        page = int(parts[4])
        user_data["group_page"] = page

        if gid in selected_ids:
            selected_ids.remove(gid)
        else:
            selected_ids.add(gid)

    elif data.startswith("cb_grp_page_"):
        page = int(data.split("_")[3])
        user_data["group_page"] = page

    elif data.startswith("cb_grp_select_all_"):
        page = int(data.split("_")[4])
        user_data["group_page"] = page
        for g in group_list:
            selected_ids.add(g["id"])

    elif data.startswith("cb_grp_unselect_all_"):
        page = int(data.split("_")[4])
        user_data["group_page"] = page
        selected_ids.clear()

    reply_markup = build_chatbot_group_keyboard(group_list, selected_ids, page=page)
    prompt_text = (
        f"👥 <b>[CHATBOT AI - BƯỚC 3/4] CHỌN NHÓM ĐỂ AI TRẢ LỜI</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Tìm thấy: <b>{len(group_list)} nhóm</b> | Đang chọn: <b>{len(selected_ids)} nhóm</b>:"
    )
    try:
        await query.edit_message_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        pass


async def handle_cb_gemini_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận Gemini API Key và kích hoạt Chatbot Zalo"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    gemini_key = update.message.text.strip() if update.message else ""

    if not gemini_key:
        await update.message.reply_text("⚠️ API Key không được để trống. Vui lòng nhập lại:")
        return

    await activate_chatbot_session(user_id, user_data, gemini_key, update.message)


async def handle_cb_use_saved_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dùng key Gemini đã lưu"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    gemini_key = user_data.get("gemini_key")

    if not gemini_key:
        await query.edit_message_text("⚠️ Không tìm thấy key đã lưu. Vui lòng gửi API Key vào đây:")
        return

    await activate_chatbot_session(user_id, user_data, gemini_key, query.message, is_edit=True)


async def activate_chatbot_session(user_id: int, user_data: dict, gemini_key: str, message_obj, is_edit: bool = False):
    """Kích hoạt và lưu session Chatbot AI Zalo"""
    client: ZaloAPI = user_data.get("zalo_client")
    imei = user_data.get("zalo_imei")
    cookies = user_data.get("zalo_cookies")
    selected_ids = list(user_data.get("selected_group_ids", set()))
    group_list = user_data.get("zalo_groups", [])

    if not client or not selected_ids:
        err_text = "⚠️ Phiên cài đặt bị gián đoạn. Vui lòng thử lại với /chatbotzalo"
        if is_edit:
            await message_obj.edit_text(err_text)
        else:
            await message_obj.reply_text(err_text)
        USER_STATES.pop(user_id, None)
        return

    # Lưu cấu hình vào JSON
    save_user_chatbot_config(user_id, {
        "zalo_imei": imei,
        "zalo_cookies": cookies,
        "selected_group_ids": selected_ids,
        "gemini_key": gemini_key
    })

    # Dừng phiên cũ nếu có
    if user_id in ACTIVE_CHATBOT_SESSIONS:
        try:
            ACTIVE_CHATBOT_SESSIONS[user_id]["running"] = False
            ACTIVE_CHATBOT_SESSIONS[user_id]["client"].is_running = False
        except Exception:
            pass

    # Bắt đầu luồng lắng nghe Zalo WebSocket
    start_chatbot_thread(user_id, client, selected_ids, gemini_key)

    chosen_groups_info = [g for g in group_list if g["id"] in selected_ids]
    names_preview = "\n".join([f"• <b>{html.escape(g['name'])}</b>" for g in chosen_groups_info[:5]])
    if len(chosen_groups_info) > 5:
        names_preview += f"\n<i>... và {len(chosen_groups_info) - 5} nhóm khác</i>"

    success_text = (
        "🧠 <b>ĐÃ BẬT CHATBOT AI ZALO THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Nhóm áp dụng: <b>{len(selected_ids)} nhóm</b>\n"
        f"{names_preview}\n"
        "🤖 Bộ não: <b>Google Gemini AI (1.5 Flash)</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👉 <b>Cơ chế hoạt động:</b> Khi bất kỳ ai trong các nhóm trên tag <b>@tên Zalo của bạn</b> kèm câu hỏi,\n"
        "Bot Zalo sẽ tự động dùng Gemini AI để trả lời và phản hồi tag lại người đó ngay lập tức!\n\n"
        "<i>(Gõ /stopchatbot hoặc bấm nút bên dưới bất cứ lúc nào để tắt)</i>"
    )
    keyboard = [
        [InlineKeyboardButton("🛑 Tắt AI Chatbot", callback_data="stop_chatbot")],
        [InlineKeyboardButton("📱 Mở Menu", callback_data="refresh_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_edit:
        await message_obj.edit_text(success_text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await message_obj.reply_text(success_text, parse_mode="HTML", reply_markup=reply_markup)

    USER_STATES.pop(user_id, None)


async def stop_chatbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tắt tính năng Chatbot AI Zalo"""
    user_id = update.effective_user.id
    if user_id in ACTIVE_CHATBOT_SESSIONS and ACTIVE_CHATBOT_SESSIONS[user_id].get("running"):
        ACTIVE_CHATBOT_SESSIONS[user_id]["running"] = False
        try:
            ACTIVE_CHATBOT_SESSIONS[user_id]["client"].is_running = False
            ws = getattr(ACTIVE_CHATBOT_SESSIONS[user_id]["client"], "ws", None)
            if ws:
                ws.close()
        except Exception:
            pass
        del ACTIVE_CHATBOT_SESSIONS[user_id]
        msg = "🛑 <b>Đã tắt tính năng Chatbot AI Zalo!</b>\nBot sẽ không còn tự động trả lời khi được tag @ nữa."
    else:
        msg = "ℹ️ Tính năng Chatbot AI Zalo hiện chưa được bật."

    keyboard = [
        [InlineKeyboardButton("🧠 Bật Lại AI Chatbot", callback_data="start_chatbot")],
        [InlineKeyboardButton("📱 Mở Menu", callback_data="refresh_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)


async def show_chatbot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem trạng thái Chatbot AI Zalo"""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    session = ACTIVE_CHATBOT_SESSIONS.get(user_id)

    if not session or not session.get("running"):
        text = "⚪ <b>Chatbot AI Zalo hiện đang TẮT.</b>"
        keyboard = [
            [InlineKeyboardButton("🧠 Bật Chatbot AI Ngay", callback_data="start_chatbot")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
    else:
        num = len(session.get("group_ids", []))
        text = (
            "🧠 <b>TRẠNG THÁI CHATBOT AI ZALO (ĐANG HOẠT ĐỘNG)</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Đang theo dõi và trả lời trên: <b>{num} nhóm</b>\n"
            "🤖 Bộ não: <b>Google Gemini AI</b>\n"
            "⚡ Trạng thái: Sẵn sàng nhận diện tag @ và trả lời tự động!"
        )
        keyboard = [
            [InlineKeyboardButton("🛑 Tắt AI Chatbot", callback_data="stop_chatbot")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        try:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)


async def autochat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu thiết lập Auto Chat: Kiểm tra xem đã có cấu hình lưu hay chưa"""
    user_id = update.effective_user.id

    if user_id in ACTIVE_AUTOCHAT_SESSIONS:
        session = ACTIVE_AUTOCHAT_SESSIONS[user_id]
        num_groups = len(session.get("group_ids", []))
        keyboard = [
            [InlineKeyboardButton("🛑 Dừng Auto Chat", callback_data="stop_autochat")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
        msg = (
            "💬 <b>AUTO CHAT HIỆN ĐANG HOẠT ĐỘNG!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Đang kết nối tới <b>{num_groups} nhóm</b> Zalo.\n"
            "👉 Bất kỳ tin nhắn nào bạn gửi cho bot lúc này đều được chuyển tiếp ngay vào các nhóm Zalo đó.\n\n"
            "Muốn dừng, bấm nút bên dưới hoặc gõ lệnh /stopautochat."
        )
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        return

    saved_auto = get_user_autochat_config(user_id)
    saved_treo = get_user_saved_config(user_id)

    # Ưu tiên lấy cấu hình autochat, nếu chưa có thì lấy từ treo zalo
    candidate = saved_auto if (saved_auto and saved_auto.get("zalo_imei") and saved_auto.get("zalo_cookies")) else saved_treo

    if candidate and candidate.get("zalo_imei") and candidate.get("zalo_cookies"):
        imei = candidate.get("zalo_imei")
        sel_count = len(candidate.get("selected_group_ids", []))
        text = (
            "💬 <b>BẬT AUTO CHAT (CẦU NỐI TELEGRAM ➔ ZALO)</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Tính năng này giúp bạn: <b>Nhắn tin gì bên Tele, Bot sẽ tự động gửi tin nhắn đó vào nhóm Zalo đã chọn!</b>\n\n"
            f"📱 Phát hiện IMEI đã lưu: <code>{html.escape(imei)}</code>\n"
            f"👥 Nhóm đã lưu trước đó: <b>{sel_count} nhóm</b>\n\n"
            "👉 Bạn muốn dùng thông tin đăng nhập đã lưu hay nhập IMEI/Cookie mới?"
        )
        keyboard = [
            [InlineKeyboardButton("⚡ Dùng Tài Khoản Đã Lưu", callback_data="autochat_use_saved")],
            [InlineKeyboardButton("🆕 Nhập IMEI & Cookie Mới", callback_data="autochat_start_fresh")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        return

    await autochat_start_fresh(update, context)


async def autochat_start_fresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhập IMEI mới cho Auto Chat"""
    user_id = update.effective_user.id
    USER_STATES[user_id] = {"state": "AUTOCHAT_WAITING_IMEI"}

    prompt = (
        "💬 <b>[AUTO CHAT - BƯỚC 1/3] NHẬP IMEI ZALO</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Vui lòng gửi mã IMEI tài khoản Zalo của bạn vào đây:\n"
        "<i>(Gõ /cancel để hủy)</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, parse_mode="HTML")
    else:
        await update.message.reply_text(prompt, parse_mode="HTML")


async def handle_autochat_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận IMEI cho Auto Chat và hỏi Cookies"""
    user_id = update.effective_user.id
    imei = update.message.text.strip()
    if not imei:
        await update.message.reply_text("⚠️ IMEI không hợp lệ. Vui lòng nhập lại IMEI:")
        return

    USER_STATES[user_id] = {"state": "AUTOCHAT_WAITING_COOKIES", "zalo_imei": imei}

    text = (
        f"✅ Đã nhận IMEI: <code>{html.escape(imei)}</code>\n\n"
        "🍪 <b>[AUTO CHAT - BƯỚC 2/3] NHẬP COOKIES ZALO</b>\n\n"
        "Vui lòng gửi Cookies tài khoản Zalo của bạn (Chuỗi Header Cookie hoặc JSON Cookie):\n"
        "<i>(Bắt buộc phải chứa cookie <b>zpw_sek</b>)</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


def zalo_login_helper(imei: str, parsed_cookies: dict):
    """Hàm đăng nhập và lấy danh sách nhóm Zalo (dùng chung cho luồng mới và chạy lại từ cấu hình lưu)"""
    try:
        ts = _util.now()
        login_url = f"https://wpa.chat.zalo.me/api/login/getLoginInfo?imei={imei}&type=30&client_version=645&computer_name=Web&ts={ts}"
        headers = zlapi._state.headers

        logger.info(f"Gửi request getLoginInfo với imei={imei}...")
        resp = requests.get(login_url, headers=headers, cookies=parsed_cookies, timeout=15)
        logger.info(f"Zalo getLoginInfo status: {resp.status_code}, response: {resp.text[:300]}")

        try:
            data = resp.json()
        except Exception:
            return None, [], f"Máy chủ Zalo trả về dữ liệu không hợp lệ (Mã HTTP {resp.status_code})"

        error_code = data.get("error_code")
        if error_code != 0:
            err_msg = data.get("error_message") or str(data)
            return None, [], f"Lỗi #{error_code} từ Zalo: {err_msg}"

        login_data = data.get("data", {})
        secret_key = login_data.get("zpw_enk")
        uid = login_data.get("uid")

        if not secret_key:
            return None, [], "Zalo không trả về secret key (zpw_enk). Vui lòng kiểm tra lại IMEI hoặc Cookies."

        client = ZaloAPI(imei=imei, cookies=parsed_cookies, auto_login=False)
        client._state.set_cookies(parsed_cookies)
        client._state._config = {
            "phone_number": str(login_data.get("phone_number")),
            "secret_key": str(secret_key),
            "send2me_id": str(uid),
            "zpw_ws": login_data.get("zpw_ws"),
        }
        client._state.cloud_id = str(uid)
        client._state.user_imei = imei
        client._state._loggedin = True
        client._imei = imei
        client.cloud_id = str(uid)
        client.user_id = str(uid)

        groups_resp = client.fetchAllGroups()

        grid_map = {}
        if hasattr(groups_resp, "gridVerMap") and isinstance(groups_resp.gridVerMap, dict):
            grid_map = groups_resp.gridVerMap
        elif isinstance(groups_resp, dict):
            grid_map = groups_resp.get("gridVerMap", {})

        group_ids = list(grid_map.keys())
        if not group_ids:
            return client, [], None

        group_name_map = {}
        try:
            info_resp = client.fetchGroupInfo({gid: 0 for gid in group_ids})
            grid_info_map = getattr(info_resp, "gridInfoMap", {})
            if isinstance(grid_info_map, dict):
                for gid, gdata in grid_info_map.items():
                    name = getattr(gdata, "name", None) or (gdata.get("name") if isinstance(gdata, dict) else None)
                    if name:
                        group_name_map[str(gid)] = name
        except Exception as e_info:
            logger.warning(f"Lỗi lấy tên nhóm: {e_info}")

        group_list = []
        for gid in group_ids:
            gid_str = str(gid)
            gname = group_name_map.get(gid_str) or f"Nhóm {gid_str[-6:]}"
            group_list.append({"id": gid_str, "name": gname})

        return client, group_list, None
    except Exception as e:
        logger.error(f"Ngoại lệ khi đăng nhập Zalo: {e}", exc_info=True)
        return None, [], str(e)


async def handle_autochat_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận cookies, đăng nhập Zalo, hiện số nhóm và danh sách nhóm cho Auto Chat"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    cookies_input = update.message.text.strip()
    parsed_cookies = parse_cookies_input(cookies_input)

    has_sek = "zpw_sek" in parsed_cookies

    if not parsed_cookies or not has_sek:
        await update.message.reply_text(
            "⚠️ Cookies không hợp lệ hoặc thiếu cookie <code>zpw_sek</code>!\n"
            "Vui lòng copy lại toàn bộ cookie từ <code>chat.zalo.me</code>:",
            parse_mode="HTML"
        )
        return

    imei = user_data.get("zalo_imei")
    user_data["zalo_cookies"] = parsed_cookies

    status_msg = await update.message.reply_text(
        f"⏳ Đang kết nối Zalo với IMEI <code>{html.escape(imei)}</code>...",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, parsed_cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực Zalo"
        await status_msg.edit_text(
            f"❌ <b>Đăng nhập Zalo thất bại!</b>\n\n<code>{html.escape(err_msg)}</code>\n\n👉 Thử lại với /autochat",
            parse_mode="HTML"
        )
        USER_STATES.pop(user_id, None)
        return

    if not group_list:
        await status_msg.edit_text("⚠️ Đăng nhập thành công nhưng tài khoản hiện chưa tham gia nhóm nào!")
        USER_STATES.pop(user_id, None)
        return

    user_data["state"] = "AUTOCHAT_WAITING_GROUPS"
    user_data["zalo_client"] = client
    user_data["zalo_groups"] = group_list
    user_data["selected_group_ids"] = set()
    user_data["group_page"] = 0

    reply_markup = build_autochat_group_keyboard(group_list, set(), page=0)
    prompt_text = (
        f"👥 <b>[AUTO CHAT - BƯỚC 3/3] CHỌN NHÓM ĐỂ GỬI TIN</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Đã tìm thấy tổng cộng: <b>{len(group_list)} nhóm</b>\n"
        "👉 Bấm vào các nhóm bạn muốn gửi tin nhắn tới, sau đó bấm <b>'🚀 XÁC NHẬN BẬT AUTO CHAT'</b>:"
    )
    await status_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)


async def handle_autochat_use_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dùng thông tin tài khoản đã lưu để hiển thị danh sách nhóm cho Auto Chat"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    saved_auto = get_user_autochat_config(user_id)
    saved_treo = get_user_saved_config(user_id)
    candidate = saved_auto if (saved_auto and saved_auto.get("zalo_imei")) else saved_treo

    if not candidate:
        await query.edit_message_text("⚠️ Không tìm thấy cấu hình đã lưu. Vui lòng bắt đầu lại bằng /autochat")
        return

    imei = candidate.get("zalo_imei")
    cookies = candidate.get("zalo_cookies")
    prev_selected = set(candidate.get("selected_group_ids", []))

    status_msg = await query.edit_message_text("⏳ Đang kết nối Zalo bằng tài khoản đã lưu...", parse_mode="HTML")

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực Zalo"
        await status_msg.edit_text(
            f"❌ <b>Không thể kết nối Zalo với Cookies đã lưu!</b>\n\n<code>{html.escape(err_msg)}</code>\n\n"
            "💡 Cookies có thể đã hết hạn, hãy chọn <b>'🆕 Nhập IMEI & Cookie Mới'</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆕 Nhập IMEI & Cookie Mới", callback_data="autochat_start_fresh")]
            ])
        )
        return

    # Lọc những ID đã lưu mà còn trong group_list
    valid_selected = {g["id"] for g in group_list if g["id"] in prev_selected}

    USER_STATES[user_id] = {
        "state": "AUTOCHAT_WAITING_GROUPS",
        "zalo_imei": imei,
        "zalo_cookies": cookies,
        "zalo_client": client,
        "zalo_groups": group_list,
        "selected_group_ids": valid_selected,
        "group_page": 0
    }

    reply_markup = build_autochat_group_keyboard(group_list, valid_selected, page=0)
    prompt_text = (
        f"👥 <b>[AUTO CHAT - BƯỚC 3/3] CHỌN NHÓM ĐỂ GỬI TIN</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Đã tìm thấy tổng cộng: <b>{len(group_list)} nhóm</b>\n"
        f"Đang chọn: <b>{len(valid_selected)} nhóm</b>\n\n"
        "👉 Bấm chọn nhóm và nhấn <b>'🚀 XÁC NHẬN BẬT AUTO CHAT'</b>:"
    )
    await status_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)


def build_autochat_group_keyboard(group_list: List[dict], selected_ids: Set[str], page: int = 0) -> InlineKeyboardMarkup:
    """Tạo bảng nút bấm chọn nhóm riêng cho Auto Chat"""
    PAGE_SIZE = 8
    total_pages = max(1, (len(group_list) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    current_groups = group_list[start_idx:end_idx]

    keyboard = []
    for g in current_groups:
        gid = g["id"]
        gname = g["name"]
        icon = "✅" if gid in selected_ids else "⬜"
        display_name = f"{icon} {gname[:25]}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"ac_grp_toggle_{gid}_{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Trước", callback_data=f"ac_grp_page_{page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="ac_grp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Sau ➡️", callback_data=f"ac_grp_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    all_selected = (len(selected_ids) == len(group_list)) and (len(group_list) > 0)
    toggle_all_btn = (
        InlineKeyboardButton("❌ Bỏ chọn tất cả", callback_data=f"ac_grp_unselect_all_{page}")
        if all_selected
        else InlineKeyboardButton("✅ Chọn tất cả nhóm", callback_data=f"ac_grp_select_all_{page}")
    )
    keyboard.append([toggle_all_btn])

    keyboard.append([
        InlineKeyboardButton(f"🚀 XÁC NHẬN BẬT AUTO CHAT ({len(selected_ids)} nhóm)", callback_data="ac_grp_confirm")
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_autochat_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút bấm chọn nhóm Auto Chat"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    data = query.data

    group_list = user_data.get("zalo_groups", [])
    selected_ids: Set[str] = user_data.setdefault("selected_group_ids", set())
    page = user_data.get("group_page", 0)

    if data == "ac_grp_noop":
        return

    if data == "ac_grp_confirm":
        if not selected_ids:
            await query.answer("⚠️ Bạn chưa chọn nhóm nào! Vui lòng chọn ít nhất 1 nhóm.", show_alert=True)
            return

        client = user_data.get("zalo_client")
        imei = user_data.get("zalo_imei")
        cookies = user_data.get("zalo_cookies")

        # Lưu vào ACTIVE_AUTOCHAT_SESSIONS
        chosen_groups_info = [g for g in group_list if g["id"] in selected_ids]
        ACTIVE_AUTOCHAT_SESSIONS[user_id] = {
            "client": client,
            "group_ids": list(selected_ids),
            "groups_info": chosen_groups_info,
            "imei": imei
        }

        # Lưu cấu hình autochat
        save_user_autochat_config(user_id, {
            "zalo_imei": imei,
            "zalo_cookies": cookies,
            "selected_group_ids": list(selected_ids)
        })

        USER_STATES.pop(user_id, None)

        group_names_preview = "\n".join([f"• <b>{html.escape(g['name'])}</b>" for g in chosen_groups_info[:5]])
        if len(chosen_groups_info) > 5:
            group_names_preview += f"\n<i>... và {len(chosen_groups_info) - 5} nhóm khác</i>"

        success_text = (
            "🎉 <b>ĐÃ BẬT TÍNH NĂNG AUTO CHAT THÀNH CÔNG!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Đã kết nối: <b>{len(selected_ids)} nhóm Zalo</b>\n"
            f"{group_names_preview}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👉 <b>BÂY GIỜ:</b> Bạn chỉ cần nhắn tin văn bản trực tiếp vào đây trên Telegram,\n"
            "Bot Zalo sẽ <b>gửi y hệt nội dung đó vào tất cả các nhóm trên</b> ngay lập tức!\n\n"
            "<i>(Gõ /stopautochat hoặc bấm nút bên dưới bất cứ lúc nào để dừng)</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🛑 Tắt Auto Chat", callback_data="stop_autochat")],
            [InlineKeyboardButton("📱 Mở Menu Chính", callback_data="refresh_menu")]
        ]
        await query.edit_message_text(success_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("ac_grp_toggle_"):
        parts = data.split("_")
        gid = parts[3]
        page = int(parts[4])
        user_data["group_page"] = page

        if gid in selected_ids:
            selected_ids.remove(gid)
        else:
            selected_ids.add(gid)

    elif data.startswith("ac_grp_page_"):
        page = int(data.split("_")[3])
        user_data["group_page"] = page

    elif data.startswith("ac_grp_select_all_"):
        page = int(data.split("_")[4])
        user_data["group_page"] = page
        for g in group_list:
            selected_ids.add(g["id"])

    elif data.startswith("ac_grp_unselect_all_"):
        page = int(data.split("_")[4])
        user_data["group_page"] = page
        selected_ids.clear()

    reply_markup = build_autochat_group_keyboard(group_list, selected_ids, page=page)
    prompt_text = (
        f"👥 <b>[AUTO CHAT - BƯỚC 3/3] CHỌN NHÓM ĐỂ GỬI TIN</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Đã tìm thấy: <b>{len(group_list)} nhóm</b> | Đang chọn: <b>{len(selected_ids)} nhóm</b>:"
    )
    try:
        await query.edit_message_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        pass


async def stop_autochat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tắt tính năng Auto Chat"""
    user_id = update.effective_user.id
    if user_id in ACTIVE_AUTOCHAT_SESSIONS:
        del ACTIVE_AUTOCHAT_SESSIONS[user_id]
        msg = "🛑 <b>Đã tắt tính năng Auto Chat!</b>\nTin nhắn gửi trên Telegram giờ sẽ không còn chuyển tiếp sang Zalo nữa."
    else:
        msg = "ℹ️ Bạn hiện chưa bật tính năng Auto Chat."

    keyboard = [
        [InlineKeyboardButton("💬 Bật Lại Auto Chat", callback_data="start_autochat")],
        [InlineKeyboardButton("📱 Mở Menu", callback_data="refresh_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)


async def show_autochat_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem trạng thái Auto Chat hiện tại"""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    session = ACTIVE_AUTOCHAT_SESSIONS.get(user_id)

    if not session:
        text = "⚪ <b>Auto Chat hiện đang TẮT.</b>"
        keyboard = [
            [InlineKeyboardButton("💬 Bật Auto Chat Ngay", callback_data="start_autochat")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]
    else:
        num = len(session.get("group_ids", []))
        groups_info = session.get("groups_info", [])
        names = "\n".join([f"• <b>{html.escape(g['name'])}</b>" for g in groups_info[:8]])
        if len(groups_info) > 8:
            names += f"\n<i>... và {len(groups_info) - 8} nhóm khác</i>"

        text = (
            "💬 <b>TRẠNG THÁI AUTO CHAT (ĐANG HOẠT ĐỘNG)</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Đang kết nối gửi tới <b>{num} nhóm</b>:\n{names}\n\n"
            "👉 Nhắn bất cứ nội dung gì ở đây trên Tele sẽ được gửi thẳng vào các nhóm trên!"
        )
        keyboard = [
            [InlineKeyboardButton("🛑 Tắt Auto Chat", callback_data="stop_autochat")],
            [InlineKeyboardButton("🔙 Quay lại Menu", callback_data="refresh_menu")]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        try:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)


async def forward_autochat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chuyển tiếp trực tiếp tin nhắn từ Telegram sang các nhóm Zalo"""
    user_id = update.effective_user.id
    session = ACTIVE_AUTOCHAT_SESSIONS.get(user_id)
    if not session:
        return False

    client: ZaloAPI = session.get("client")
    group_ids = session.get("group_ids", [])
    text = update.message.text

    if not text or not client or not group_ids:
        return False

    loop = asyncio.get_running_loop()

    def send_to_all():
        success_count = 0
        errors = []
        msg = Message(text=text)
        for gid in group_ids:
            try:
                client.sendMessage(msg, thread_id=gid, thread_type=ThreadType.GROUP)
                success_count += 1
            except Exception as e:
                errors.append(f"{gid}: {e}")
        return success_count, errors

    success_cnt, errors = await loop.run_in_executor(None, send_to_all)
    logger.info(f"Auto Chat user {user_id}: Đã gửi tin nhắn '{text[:20]}' tới {success_cnt}/{len(group_ids)} nhóm.")

    # Thông báo trạng thái gửi nhỏ gọn cho người dùng
    if success_cnt == len(group_ids):
        await update.message.reply_text(
            f"✅ Đã gửi tới <b>{success_cnt} nhóm</b> Zalo!",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"⚠️ Đã gửi tới <b>{success_cnt}/{len(group_ids)} nhóm</b> Zalo.\n"
            f"<i>({len(errors)} nhóm gửi không thành công)</i>",
            parse_mode="HTML"
        )
    return True


# ==========================================
# PHẦN 3: TREO ZALO (LẶP LIÊN TỤC & TÙY CHỈNH)
# ==========================================

def build_zalo_message_style(content_len: int, format_settings: dict):
    """Tạo đối tượng MultiMsgStyle chuẩn của Zalo (cỡ font tiêu chuẩn + style + color)"""
    styles_list = []

    styles_list.append(MessageStyle(offset=0, length=content_len, style="font", size="18", auto_format=False))

    if format_settings.get("bold"):
        styles_list.append(MessageStyle(offset=0, length=content_len, style="bold", auto_format=False))

    if format_settings.get("italic"):
        styles_list.append(MessageStyle(offset=0, length=content_len, style="italic", auto_format=False))

    if format_settings.get("underline"):
        styles_list.append(MessageStyle(offset=0, length=content_len, style="underline", auto_format=False))

    if format_settings.get("strike"):
        styles_list.append(MessageStyle(offset=0, length=content_len, style="strike", auto_format=False))

    color_id = format_settings.get("color", "none")
    for c in COLOR_OPTIONS:
        if c["id"] == color_id and c["hex"]:
            styles_list.append(MessageStyle(offset=0, length=content_len, style="color", color=c["hex"], auto_format=False))
            break

    return MultiMsgStyle(styles_list)


async def run_treo_worker(user_id: int, client: ZaloAPI, group_ids: list, config: dict, context: ContextTypes.DEFAULT_TYPE):
    """Vòng lặp nền gửi tin nhắn Zalo, đọc trực tiếp từ dict config để áp dụng thay đổi thời gian thực"""
    loop = asyncio.get_running_loop()

    def send_one(gid, text, style):
        try:
            msg = Message(text=text, style=style)
            client.sendMessage(msg, thread_id=gid, thread_type=ThreadType.GROUP)
            return True, None
        except Exception as e:
            return False, str(e)

    try:
        while True:
            content = config.get("content", "")
            format_settings = config.get("format_settings", {})
            delay = float(config.get("delay", 10.0))

            msg_style = build_zalo_message_style(len(content), format_settings)

            for gid in group_ids:
                ok, err = await loop.run_in_executor(None, send_one, gid, content, msg_style)
                if not ok:
                    logger.warning(f"Lỗi gửi nhóm {gid}: {err}")
                await asyncio.sleep(0.5)

            await asyncio.sleep(delay)

    except asyncio.CancelledError:
        logger.info(f"User {user_id} đã huỷ task treo Zalo.")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⏹ <b>Đã dừng tiến trình Treo Zalo!</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Lỗi trong quá trình treo: {e}")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Tiến trình treo Zalo dừng do lỗi: {e}",
            )
        except Exception:
            pass
    finally:
        RUNNING_TREO_SESSIONS.pop(user_id, None)


def build_config_panel_keyboard(config: dict) -> InlineKeyboardMarkup:
    """Bảng điều khiển tùy chỉnh trực tiếp khi treo đang chạy"""
    fmt = config.get("format_settings", {})
    delay = config.get("delay", 10)
    current_color = fmt.get("color", "none")

    keyboard = [
        [
            InlineKeyboardButton("📝 Đổi Nội Dung", callback_data="cfg_change_content"),
            InlineKeyboardButton(f"⏱ Đổi Delay ({delay}s)", callback_data="cfg_change_delay"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if fmt.get('bold') else '⬜'} In Đậm", callback_data="cfg_toggle_bold"),
            InlineKeyboardButton(f"{'✅' if fmt.get('italic') else '⬜'} In Nghiêng", callback_data="cfg_toggle_italic"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if fmt.get('underline') else '⬜'} Gạch Chân", callback_data="cfg_toggle_underline"),
            InlineKeyboardButton(f"{'✅' if fmt.get('strike') else '⬜'} Gạch Ngang", callback_data="cfg_toggle_strike"),
        ],
    ]

    color_row = []
    for c in COLOR_OPTIONS:
        is_cur = (current_color == c["id"])
        prefix = "🔘" if is_cur else "⚪"
        btn_text = f"{prefix} {c['name']}"
        color_row.append(InlineKeyboardButton(btn_text, callback_data=f"cfg_color_{c['id']}"))
        if len(color_row) == 2:
            keyboard.append(color_row)
            color_row = []
    if color_row:
        keyboard.append(color_row)

    keyboard.append([
        InlineKeyboardButton("✅ Lưu & Đóng Bảng", callback_data="cfg_close"),
        InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_config_panel_text(config: dict, num_groups: int) -> str:
    fmt = config.get("format_settings", {})
    delay = config.get("delay", 10)
    content = config.get("content", "")
    current_color = fmt.get("color", "none")

    color_name = "Mặc định"
    for c in COLOR_OPTIONS:
        if c["id"] == current_color:
            color_name = c["name"]
            break

    styles = []
    if fmt.get("bold"): styles.append("In đậm")
    if fmt.get("italic"): styles.append("In nghiêng")
    if fmt.get("underline"): styles.append("Gạch chân")
    if fmt.get("strike"): styles.append("Gạch ngang")
    style_str = ", ".join(styles) if styles else "Bình thường"

    return (
        "⚙️ <b>BẢNG TÙY CHỈNH TREO ZALO (REAL-TIME)</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Số nhóm treo: <b>{num_groups} nhóm</b>\n"
        f"📝 Nội dung: <i>{html.escape(content)}</i>\n"
        f"⏱ Giãn cách (Delay): <b>{delay} giây</b>\n"
        f"🎨 Màu chữ: <b>{color_name}</b>\n"
        f"🔤 Kiểu chữ: <b>{style_str}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👉 Bấm nút bên dưới để đổi nội dung, màu sắc hoặc delay tức thì:"
    )


async def open_config_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mở bảng tùy chỉnh real-time"""
    user_id = update.effective_user.id
    session = RUNNING_TREO_SESSIONS.get(user_id)

    if not session or session["task"].done():
        msg = "ℹ️ Hiện không có tiến trình Treo Zalo nào đang chạy để tùy chỉnh."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    config = session["config"]
    num_groups = len(session.get("group_ids", []))
    text = get_config_panel_text(config, num_groups)
    reply_markup = build_config_panel_keyboard(config)

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)


async def handle_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý các nút bấm trong bảng tùy chỉnh real-time"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    session = RUNNING_TREO_SESSIONS.get(user_id)
    if not session or session["task"].done():
        await query.edit_message_text("ℹ️ Tiến trình Treo Zalo đã dừng hoặc không tồn tại.")
        return

    config = session["config"]
    num_groups = len(session.get("group_ids", []))
    data = query.data

    if data == "cfg_close":
        saved = get_user_saved_config(user_id)
        if saved:
            saved["spam_content"] = config.get("content")
            saved["content"] = config.get("content")
            saved["delay"] = config.get("delay")
            saved["format_settings"] = config.get("format_settings")
            save_user_treo_config(user_id, saved)

        await query.edit_message_text(
            "✅ <b>Đã áp dụng và lưu các cài đặt mới vào tiến trình treo Zalo!</b>\n"
            "Tiến trình đang tiếp tục chạy với thiết lập mới. Gõ /menu hoặc /stoptreo để quản lý.",
            parse_mode="HTML"
        )
        return

    if data == "cfg_change_content":
        USER_STATES[user_id] = {"state": "WAITING_NEW_CONTENT"}
        await query.message.reply_text(
            "✍️ <b>Nhập nội dung mới:</b>\n"
            "Vui lòng gửi nội dung tin nhắn mới mà bạn muốn đổi sang:",
            parse_mode="HTML"
        )
        return

    if data == "cfg_change_delay":
        USER_STATES[user_id] = {"state": "WAITING_NEW_DELAY"}
        await query.message.reply_text(
            "⏱ <b>Nhập số giây delay mới:</b>\n"
            "Vui lòng nhập số giây (ví dụ: <code>5</code>, <code>10</code>, <code>20</code>):",
            parse_mode="HTML"
        )
        return

    fmt = config.setdefault("format_settings", {})
    if data == "cfg_toggle_bold":
        fmt["bold"] = not fmt.get("bold", False)
    elif data == "cfg_toggle_italic":
        fmt["italic"] = not fmt.get("italic", False)
    elif data == "cfg_toggle_underline":
        fmt["underline"] = not fmt.get("underline", False)
    elif data == "cfg_toggle_strike":
        fmt["strike"] = not fmt.get("strike", False)
    elif data.startswith("cfg_color_"):
        col_id = data.replace("cfg_color_", "")
        fmt["color"] = col_id

    saved = get_user_saved_config(user_id)
    if saved:
        saved["format_settings"] = fmt
        save_user_treo_config(user_id, saved)

    text = get_config_panel_text(config, num_groups)
    reply_markup = build_config_panel_keyboard(config)

    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        pass


async def treozalo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu /treozalo: Kiểm tra xem đã có cấu hình lưu hay chưa"""
    user_id = update.effective_user.id
    if user_id in RUNNING_TREO_SESSIONS and not RUNNING_TREO_SESSIONS[user_id]["task"].done():
        keyboard = [
            [
                InlineKeyboardButton("⚙️ Tùy Chỉnh Cài Đặt", callback_data="cfg_open"),
                InlineKeyboardButton("⏹ Dừng Treo Ngay", callback_data="stop_treo"),
            ]
        ]
        msg = "⚠️ Bạn đang có một tiến trình treo Zalo đang chạy! Bạn có thể tùy chỉnh hoặc dừng lại:"
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return

    saved_cfg = get_user_saved_config(user_id)
    if saved_cfg and saved_cfg.get("zalo_imei") and saved_cfg.get("zalo_cookies"):
        text = (
            "⚙️ <b>PHÁT HIỆN THIẾT LẬP TREO ĐÃ LƯU TRƯỚC ĐÓ</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"📱 IMEI: <code>{html.escape(saved_cfg.get('zalo_imei', ''))}</code>\n"
            f"👥 Số nhóm đã chọn: <b>{len(saved_cfg.get('selected_group_ids', []))} nhóm</b>\n"
            f"📝 Nội dung: <i>{html.escape(saved_cfg.get('spam_content') or saved_cfg.get('content', ''))}</i>\n"
            f"⏱ Delay: <b>{saved_cfg.get('delay', 10)} giây</b>\n\n"
            "👉 Bạn có muốn <b>Chạy Lại Ngay</b> bằng cấu hình này hay <b>Thiết Lập Mới</b>?"
        )
        keyboard = [
            [InlineKeyboardButton("▶️ Chạy Lại Ngay", callback_data="run_saved_config")],
            [InlineKeyboardButton("🆕 Thiết Lập Mới Từ Đầu", callback_data="start_treo_fresh")],
            [InlineKeyboardButton("🗑 Xóa Thiết Lập Cũ", callback_data="delete_saved_config")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        return

    await treozalo_start_fresh(update, context)


async def treozalo_start_fresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu thiết lập treo mới hoàn toàn từ Bước 1: IMEI"""
    user_id = update.effective_user.id
    USER_STATES[user_id] = {"state": "WAITING_IMEI"}

    prompt = (
        "📱 <b>[BƯỚC 1/5] NHẬP IMEI ZALO</b>\n\n"
        "Vui lòng gửi mã IMEI của bạn vào đây:\n"
        "<i>(Gõ /cancel để hủy)</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, parse_mode="HTML")
    else:
        await update.message.reply_text(prompt, parse_mode="HTML")


async def run_saved_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chạy lại tiến trình treo ngay từ cấu hình đã lưu mà không cần nhập lại từ đầu"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    saved_cfg = get_user_saved_config(user_id)

    if not saved_cfg:
        await query.edit_message_text("⚠️ Không tìm thấy cấu hình đã lưu. Vui lòng thiết lập mới bằng /treozalo")
        return

    imei = saved_cfg.get("zalo_imei")
    cookies = saved_cfg.get("zalo_cookies")
    selected_ids = saved_cfg.get("selected_group_ids", [])
    content = saved_cfg.get("spam_content") or saved_cfg.get("content", "")
    format_settings = saved_cfg.get("format_settings", {})
    delay = float(saved_cfg.get("delay", 10.0))

    if not imei or not cookies or not selected_ids:
        await query.edit_message_text("⚠️ Cấu hình đã lưu bị thiếu thông tin. Vui lòng thiết lập lại mới bằng /treozalo")
        return

    status_msg = await query.edit_message_text(
        "⏳ Đang đăng nhập Zalo bằng thông tin đã lưu...",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực Zalo"
        await status_msg.edit_text(
            f"❌ <b>Không thể kết nối Zalo với Cookies đã lưu!</b>\n\n"
            f"<b>Lỗi:</b> <code>{html.escape(err_msg)}</code>\n\n"
            "💡 <i>Cookies của bạn có thể đã hết hạn. Hãy bấm <b>'🆕 Thiết Lập Mới Từ Đầu'</b> để cập nhật Cookies mới!</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆕 Thiết Lập Mới Từ Đầu", callback_data="start_treo_fresh")],
                [InlineKeyboardButton("🗑 Xóa Cấu Hình Cũ", callback_data="delete_saved_config")]
            ])
        )
        return

    config = {
        "content": content,
        "format_settings": format_settings,
        "delay": delay
    }

    task = asyncio.create_task(run_treo_worker(user_id, client, selected_ids, config, context))
    RUNNING_TREO_SESSIONS[user_id] = {
        "task": task,
        "config": config,
        "client": client,
        "group_ids": selected_ids
    }

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Tùy Chỉnh Real-time", callback_data="cfg_open"),
            InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    summary = (
        "🚀 <b>BẮT ĐẦU TREO ZALO (TỪ CẤU HÌNH LƯU) THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"• Số nhóm: <b>{len(selected_ids)} nhóm</b>\n"
        f"• Nội dung: <i>{html.escape(content)}</i>\n"
        f"• Delay: <b>{delay} giây</b>\n\n"
        "💡 <i>Bạn có thể bấm <b>'⚙️ Tùy Chỉnh Real-time'</b> để đổi nội dung, màu chữ hoặc delay ngay khi đang chạy!</i>"
    )
    await status_msg.edit_text(summary, parse_mode="HTML", reply_markup=reply_markup)


async def handle_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận IMEI và chuyển sang bước 2: Nhập Cookies"""
    user_id = update.effective_user.id
    imei = update.message.text.strip()
    if not imei:
        await update.message.reply_text("⚠️ IMEI không hợp lệ. Vui lòng nhập lại IMEI:")
        return

    USER_STATES[user_id] = {"state": "WAITING_COOKIES", "zalo_imei": imei}

    text = (
        f"✅ Đã nhận IMEI: <code>{html.escape(imei)}</code>\n\n"
        "🍪 <b>[BƯỚC 2/5] NHẬP COOKIES ZALO</b>\n\n"
        "Vui lòng gửi Cookies tài khoản Zalo của bạn (Chuỗi Header Cookie hoặc JSON Cookie):\n"
        "<i>(Bắt buộc phải chứa cookie <b>zpw_sek</b>)</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


def build_group_selection_keyboard(group_list: List[dict], selected_ids: Set[str], page: int = 0) -> InlineKeyboardMarkup:
    """Tạo bảng nút bấm chọn nhóm theo tên"""
    PAGE_SIZE = 8
    total_pages = max(1, (len(group_list) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    current_groups = group_list[start_idx:end_idx]

    keyboard = []
    for g in current_groups:
        gid = g["id"]
        gname = g["name"]
        icon = "✅" if gid in selected_ids else "⬜"
        display_name = f"{icon} {gname[:25]}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"grp_toggle_{gid}_{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Trước", callback_data=f"grp_page_{page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="grp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Sau ➡️", callback_data=f"grp_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    all_selected = (len(selected_ids) == len(group_list)) and (len(group_list) > 0)
    toggle_all_btn = (
        InlineKeyboardButton("❌ Bỏ chọn tất cả", callback_data=f"grp_unselect_all_{page}")
        if all_selected
        else InlineKeyboardButton("✅ Chọn tất cả nhóm", callback_data=f"grp_select_all_{page}")
    )
    keyboard.append([toggle_all_btn])

    keyboard.append([
        InlineKeyboardButton(f"🚀 XÁC NHẬN ({len(selected_ids)} nhóm)", callback_data="grp_confirm")
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận cookies, đăng nhập Zalo, lấy tên nhóm và hiển thị nút bấm"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    cookies_input = update.message.text.strip()
    parsed_cookies = parse_cookies_input(cookies_input)

    found_keys = list(parsed_cookies.keys())
    has_sek = "zpw_sek" in parsed_cookies

    logger.info(f"Đã trích xuất {len(parsed_cookies)} cookies. Has zpw_sek: {has_sek}. Keys: {found_keys[:10]}")

    if not parsed_cookies:
        await update.message.reply_text(
            "⚠️ Không trích xuất được cookie nào từ nội dung bạn gửi.\n"
            "Vui lòng copy lại toàn bộ cookie từ trình duyệt:",
        )
        return

    if not has_sek:
        await update.message.reply_text(
            f"⚠️ Đã đọc được {len(parsed_cookies)} trường cookie nhưng <b>thiếu cookie quan trọng nhất là 'zpw_sek'</b>!\n\n"
            "Vui lòng copy lại đầy đủ cookie từ <code>chat.zalo.me</code>:",
            parse_mode="HTML"
        )
        return

    imei = user_data.get("zalo_imei")
    user_data["zalo_cookies"] = parsed_cookies

    status_msg = await update.message.reply_text(
        f"⏳ Đã đọc được <b>{len(parsed_cookies)}</b> cookies (đã có <code>zpw_sek</code>).\n"
        f"Đang kết nối Zalo để đăng nhập...",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    client, group_list, err = await loop.run_in_executor(None, zalo_login_helper, imei, parsed_cookies)

    if err or not client:
        err_msg = err or "Lỗi xác thực không xác định"
        user_hint = ""
        if "102" in err_msg or "timeout" in err_msg.lower() or "session" in err_msg.lower():
            user_hint = "\n\n💡 <i>Cookies này đã hết hạn. Hãy mở chat.zalo.me và lấy lại Cookies mới!</i>"

        await status_msg.edit_text(
            f"❌ <b>Đăng nhập Zalo thất bại!</b>\n\n<b>Chi tiết lỗi:</b>\n<code>{html.escape(err_msg)}</code>{user_hint}\n\n👉 Vui lòng thử lại với /treozalo",
            parse_mode="HTML"
        )
        USER_STATES.pop(user_id, None)
        return

    if not group_list:
        await status_msg.edit_text(
            "⚠️ Đăng nhập thành công nhưng tài khoản hiện chưa tham gia nhóm nào.\n"
            "Vui lòng tham gia nhóm Zalo và thử lại!",
        )
        USER_STATES.pop(user_id, None)
        return

    user_data["state"] = "WAITING_GROUPS"
    user_data["zalo_client"] = client
    user_data["zalo_groups"] = group_list
    user_data["selected_group_ids"] = set()
    user_data["group_page"] = 0

    reply_markup = build_group_selection_keyboard(group_list, set(), page=0)
    prompt_text = (
        f"👥 <b>[BƯỚC 3/5] CHỌN NHÓM CẦN TREO</b>\n"
        f"Tìm thấy <b>{len(group_list)} nhóm</b>. Bấm vào nút tên nhóm để chọn:"
    )
    await status_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)


async def handle_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý sự kiện bấm nút chọn nhóm"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    data = query.data

    group_list = user_data.get("zalo_groups", [])
    selected_ids: Set[str] = user_data.setdefault("selected_group_ids", set())
    page = user_data.get("group_page", 0)

    if data == "grp_noop":
        return

    if data == "grp_confirm":
        if not selected_ids:
            await query.answer("⚠️ Bạn chưa chọn nhóm nào! Vui lòng chọn ít nhất 1 nhóm.", show_alert=True)
            return

        user_data["state"] = "WAITING_CONTENT"
        prompt = (
            f"✅ Đã chọn <b>{len(selected_ids)} nhóm</b>!\n\n"
            "✍️ <b>[BƯỚC 4/5] NHẬP NỘI DUNG TREO</b>\n\n"
            "Vui lòng gửi nội dung tin nhắn bạn muốn gửi vào nhóm:"
        )
        try:
            await query.edit_message_text(prompt, parse_mode="HTML")
        except BadRequest:
            pass
        return

    if data.startswith("grp_toggle_"):
        parts = data.split("_")
        gid = parts[2]
        page = int(parts[3])
        user_data["group_page"] = page

        if gid in selected_ids:
            selected_ids.remove(gid)
        else:
            selected_ids.add(gid)

    elif data.startswith("grp_page_"):
        page = int(data.split("_")[2])
        user_data["group_page"] = page

    elif data.startswith("grp_select_all_"):
        page = int(data.split("_")[3])
        user_data["group_page"] = page
        for g in group_list:
            selected_ids.add(g["id"])

    elif data.startswith("grp_unselect_all_"):
        page = int(data.split("_")[3])
        user_data["group_page"] = page
        selected_ids.clear()

    reply_markup = build_group_selection_keyboard(group_list, selected_ids, page=page)
    prompt_text = (
        f"👥 <b>[BƯỚC 3/5] CHỌN NHÓM CẦN TREO</b>\n"
        f"Tìm thấy <b>{len(group_list)} nhóm</b>. Đang chọn: <b>{len(selected_ids)} nhóm</b>:"
    )

    try:
        await query.edit_message_text(prompt_text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        pass


def build_formatting_keyboard(format_settings: dict) -> InlineKeyboardMarkup:
    """Tạo bảng nút bấm chọn định dạng chữ & màu sắc khi setup ban đầu"""
    is_bold = format_settings.get("bold", False)
    is_italic = format_settings.get("italic", False)
    is_underline = format_settings.get("underline", False)
    is_strike = format_settings.get("strike", False)
    current_color = format_settings.get("color", "none")

    keyboard = [
        [
            InlineKeyboardButton(f"{'✅' if is_bold else '⬜'} In Đậm", callback_data="fmt_toggle_bold"),
            InlineKeyboardButton(f"{'✅' if is_italic else '⬜'} In Nghiêng", callback_data="fmt_toggle_italic"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if is_underline else '⬜'} Gạch Chân", callback_data="fmt_toggle_underline"),
            InlineKeyboardButton(f"{'✅' if is_strike else '⬜'} Gạch Ngang", callback_data="fmt_toggle_strike"),
        ],
    ]

    color_row = []
    for c in COLOR_OPTIONS:
        is_cur = (current_color == c["id"])
        prefix = "🔘" if is_cur else "⚪"
        btn_text = f"{prefix} {c['name']}"
        color_row.append(InlineKeyboardButton(btn_text, callback_data=f"fmt_color_{c['id']}"))
        if len(color_row) == 2:
            keyboard.append(color_row)
            color_row = []
    if color_row:
        keyboard.append(color_row)

    keyboard.append([
        InlineKeyboardButton("🚀 Tiếp Tục (Sang Bước Nhập Delay)", callback_data="fmt_confirm")
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nội dung treo và chuyển sang chọn định dạng chữ & màu sắc"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    content = update.message.text
    if not content or not content.strip():
        await update.message.reply_text("⚠️ Nội dung không được để trống. Vui lòng nhập lại:")
        return

    user_data["spam_content"] = content
    user_data["state"] = "WAITING_FORMAT"

    format_settings = {
        "bold": False,
        "italic": False,
        "underline": False,
        "strike": False,
        "color": "none"
    }
    user_data["format_settings"] = format_settings

    reply_markup = build_formatting_keyboard(format_settings)
    prompt = (
        "🎨 <b>[TÙY CHỌN] ĐỊNH DẠNG CHỮ & MÀU SẮC</b>\n\n"
        "• Kích thước: <b>Tiêu chuẩn</b>\n"
        "• Bạn có thể chọn In đậm, In nghiêng, Gạch chân, Gạch ngang và Màu chữ bằng các nút bên dưới:\n\n"
        "Bấm <b>'🚀 Tiếp Tục'</b> khi hoàn tất."
    )
    await update.message.reply_text(prompt, parse_mode="HTML", reply_markup=reply_markup)


async def handle_format_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý toggle nút định dạng chữ và chọn màu trong setup ban đầu"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})
    data = query.data

    format_settings = user_data.setdefault("format_settings", {
        "bold": False,
        "italic": False,
        "underline": False,
        "strike": False,
        "color": "none"
    })

    if data == "fmt_confirm":
        user_data["state"] = "WAITING_DELAY"
        prompt = (
            "⏱ <b>[BƯỚC 5/5] NHẬP THỜI GIAN DELAY</b>\n\n"
            "Vui lòng nhập số giây delay giữa mỗi vòng gửi (ví dụ: <code>10</code> hoặc <code>30</code>):\n"
            "<i>(Khuyến nghị >= 5 giây)</i>"
        )
        try:
            await query.edit_message_text(prompt, parse_mode="HTML")
        except BadRequest:
            pass
        return

    if data == "fmt_toggle_bold":
        format_settings["bold"] = not format_settings["bold"]
    elif data == "fmt_toggle_italic":
        format_settings["italic"] = not format_settings["italic"]
    elif data == "fmt_toggle_underline":
        format_settings["underline"] = not format_settings["underline"]
    elif data == "fmt_toggle_strike":
        format_settings["strike"] = not format_settings["strike"]
    elif data.startswith("fmt_color_"):
        col_id = data.replace("fmt_color_", "")
        format_settings["color"] = col_id

    reply_markup = build_formatting_keyboard(format_settings)
    prompt = (
        "🎨 <b>[TÙY CHỌN] ĐỊNH DẠNG CHỮ & MÀU SẮC</b>\n\n"
        "• Kích thước: <b>Tiêu chuẩn</b>\n"
        "• Bạn có thể chọn In đậm, In nghiêng, Gạch chân, Gạch ngang và Màu chữ bằng các nút bên dưới:\n\n"
        "Bấm <b>'🚀 Tiếp Tục'</b> khi hoàn tất."
    )
    try:
        await query.edit_message_text(prompt, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        pass


async def handle_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số giây delay, LƯU THIẾT LẬP TỰ ĐỘNG và khởi chạy vòng lặp treo Zalo"""
    user_id = update.effective_user.id
    user_data = USER_STATES.get(user_id, {})

    delay_str = update.message.text.strip()
    try:
        delay = float(delay_str)
        if delay < 1:
            await update.message.reply_text("⚠️ Thời gian delay tối thiểu là 1 giây. Vui lòng nhập lại:")
            return
    except ValueError:
        await update.message.reply_text("⚠️ Vui lòng nhập số giây hợp lệ (vd: 5, 10, 30):")
        return

    client: ZaloAPI = user_data.get("zalo_client")
    selected_ids = list(user_data.get("selected_group_ids", set()))
    content = user_data.get("spam_content")
    format_settings = user_data.get("format_settings", {})
    imei = user_data.get("zalo_imei")
    cookies = user_data.get("zalo_cookies")

    save_data = {
        "zalo_imei": imei,
        "zalo_cookies": cookies,
        "selected_group_ids": selected_ids,
        "spam_content": content,
        "format_settings": format_settings,
        "delay": delay
    }
    save_user_treo_config(user_id, save_data)

    config = {
        "content": content,
        "format_settings": format_settings,
        "delay": delay
    }

    task = asyncio.create_task(run_treo_worker(user_id, client, selected_ids, config, context))
    RUNNING_TREO_SESSIONS[user_id] = {
        "task": task,
        "config": config,
        "client": client,
        "group_ids": selected_ids
    }

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Tùy Chỉnh Real-time", callback_data="cfg_open"),
            InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    summary = (
        "🚀 <b>BẮT ĐẦU TREO ZALO THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"• Số nhóm: <b>{len(selected_ids)} nhóm</b>\n"
        f"• Delay ban đầu: <b>{delay} giây</b>\n"
        f"• Trạng thái lưu: <b>💾 Đã tự động lưu thiết lập</b>\n\n"
        "💡 <i>Bạn có thể bấm nút <b>'⚙️ Tùy Chỉnh Real-time'</b> bất kỳ lúc nào để đổi nội dung, màu chữ hoặc delay mà không cần dừng bot!</i>"
    )
    await update.message.reply_text(summary, parse_mode="HTML", reply_markup=reply_markup)
    USER_STATES.pop(user_id, None)


async def handle_new_content_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý đổi nội dung trong lúc đang chạy"""
    user_id = update.effective_user.id
    new_text = update.message.text
    session = RUNNING_TREO_SESSIONS.get(user_id)

    if not session or session["task"].done():
        await update.message.reply_text("ℹ️ Tiến trình Treo Zalo đã dừng.")
        USER_STATES.pop(user_id, None)
        return

    if not new_text or not new_text.strip():
        await update.message.reply_text("⚠️ Nội dung không được để trống. Vui lòng nhập lại:")
        return

    session["config"]["content"] = new_text

    saved = get_user_saved_config(user_id)
    if saved:
        saved["spam_content"] = new_text
        saved["content"] = new_text
        save_user_treo_config(user_id, saved)

    USER_STATES.pop(user_id, None)

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Mở Bảng Tùy Chỉnh", callback_data="cfg_open"),
            InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo"),
        ]
    ]
    await update.message.reply_text(
        f"✅ <b>Đã cập nhật nội dung mới thành công!</b>\n"
        f"Nội dung hiện tại: <i>{html.escape(new_text)}</i>\n\n"
        "Vòng gửi tiếp theo sẽ tự động áp dụng nội dung này.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_new_delay_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý đổi delay trong lúc đang chạy"""
    user_id = update.effective_user.id
    delay_str = update.message.text.strip()
    session = RUNNING_TREO_SESSIONS.get(user_id)

    if not session or session["task"].done():
        await update.message.reply_text("ℹ️ Tiến trình Treo Zalo đã dừng.")
        USER_STATES.pop(user_id, None)
        return

    try:
        new_delay = float(delay_str)
        if new_delay < 1:
            await update.message.reply_text("⚠️ Delay tối thiểu là 1 giây. Vui lòng nhập lại:")
            return
    except ValueError:
        await update.message.reply_text("⚠️ Vui lòng nhập số giây hợp lệ (vd: 5, 10, 30):")
        return

    session["config"]["delay"] = new_delay

    saved = get_user_saved_config(user_id)
    if saved:
        saved["delay"] = new_delay
        save_user_treo_config(user_id, saved)

    USER_STATES.pop(user_id, None)

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Mở Bảng Tùy Chỉnh", callback_data="cfg_open"),
            InlineKeyboardButton("⏹ Dừng Treo", callback_data="stop_treo"),
        ]
    ]
    await update.message.reply_text(
        f"✅ <b>Đã đổi Delay thành {new_delay} giây!</b>\n"
        "Vòng lặp sẽ áp dụng ngay sau đợt gửi hiện tại.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def stop_treo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /stoptreo để dừng tiến trình treo"""
    user_id = update.effective_user.id
    session = RUNNING_TREO_SESSIONS.get(user_id)

    if session and not session["task"].done():
        session["task"].cancel()
        msg = "⏳ Đang gửi yêu cầu dừng tiến trình Treo Zalo..."
    else:
        msg = "ℹ️ Hiện không có tiến trình Treo Zalo nào đang chạy."

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg)
    else:
        await update.message.reply_text(msg)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Huỷ bỏ thao tác hiện tại"""
    user_id = update.effective_user.id
    USER_STATES.pop(user_id, None)
    await update.message.reply_text("❌ Đã hủy thao tác.")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Định tuyến các tin nhắn văn bản"""
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    state_data = USER_STATES.get(user_id)
    st = state_data.get("state") if state_data else None

    # 1. Trạng thái cấu hình đổi nội dung hoặc delay real-time
    if st == "WAITING_NEW_CONTENT":
        return await handle_new_content_input(update, context)
    elif st == "WAITING_NEW_DELAY":
        return await handle_new_delay_input(update, context)

    # 1.5. Các bước nhập thông tin Chatbot AI Zalo
    if st == "CB_WAITING_IMEI":
        return await handle_cb_imei(update, context)
    elif st == "CB_WAITING_COOKIES":
        return await handle_cb_cookies(update, context)
    elif st == "CB_WAITING_GEMINI_KEY":
        return await handle_cb_gemini_key_input(update, context)

    # 2. Các bước nhập thông tin thiết lập Auto Chat
    if st == "AUTOCHAT_WAITING_IMEI":
        return await handle_autochat_imei(update, context)
    elif st == "AUTOCHAT_WAITING_COOKIES":
        return await handle_autochat_cookies(update, context)

    # 3. Các bước nhập thông tin Treo Zalo
    if st == "WAITING_MUSIC_URL":
        return await handle_music_url(update, context)
    elif st == "WAITING_IMEI":
        return await handle_imei(update, context)
    elif st == "WAITING_COOKIES":
        return await handle_cookies(update, context)
    elif st == "WAITING_CONTENT":
        return await handle_content(update, context)
    elif st == "WAITING_DELAY":
        return await handle_delay(update, context)

    # 4. Nếu gửi link trực tiếp (khi không trong bước setup nào)
    if text.startswith("http://") or text.startswith("https://"):
        USER_STATES[user_id] = {"state": "WAITING_MUSIC_URL"}
        return await handle_music_url(update, context)

    # 5. NẾU ĐANG BẬT AUTO CHAT -> Chuyển tiếp tin nhắn sang Zalo!
    if user_id in ACTIVE_AUTOCHAT_SESSIONS:
        return await forward_autochat_message(update, context)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý toàn bộ nút bấm Inline Keyboard"""
    query = update.callback_query
    data = query.data

    # Các nút chính từ menu
    if data == "start_chatbot":
        return await chatbot_start(update, context)
    elif data == "cb_start_fresh":
        return await chatbot_start_fresh(update, context)
    elif data == "cb_use_saved":
        return await handle_cb_use_saved(update, context)
    elif data == "cb_use_saved_key":
        return await handle_cb_use_saved_key(update, context)
    elif data == "stop_chatbot":
        return await stop_chatbot_command(update, context)
    elif data == "chatbot_status":
        return await show_chatbot_status(update, context)
    elif data.startswith("cb_grp_"):
        return await handle_cb_group_callback(update, context)

    elif data == "start_autochat":
        return await autochat_start(update, context)
    elif data == "start_autochat_fresh":
        return await autochat_start_fresh(update, context)
    elif data == "autochat_use_saved":
        return await handle_autochat_use_saved(update, context)
    elif data == "stop_autochat":
        return await stop_autochat_command(update, context)
    elif data == "autochat_status":
        return await show_autochat_status(update, context)
    elif data.startswith("ac_grp_"):
        return await handle_autochat_group_callback(update, context)

    # Treo lặp
    elif data == "start_treo":
        return await treozalo_start(update, context)
    elif data == "start_treo_fresh":
        return await treozalo_start_fresh(update, context)
    elif data == "run_saved_config":
        return await run_saved_config_callback(update, context)
    elif data == "manage_saved_config":
        return await show_manage_saved_config(update, context)
    elif data == "delete_saved_config":
        return await handle_delete_saved_config(update, context)
    elif data == "stop_treo":
        return await stop_treo_command(update, context)

    # Tải nhạc
    elif data == "start_tainhac":
        return await tainhac_start(update, context)
    elif data == "refresh_menu":
        return await menu_command(update, context)
    elif data == "cfg_open":
        return await open_config_panel(update, context)
    elif data.startswith("cfg_"):
        return await handle_config_callback(update, context)
    elif data.startswith("dl_format_"):
        return await handle_music_format_callback(update, context)
    elif data.startswith("grp_"):
        return await handle_group_callback(update, context)
    elif data.startswith("fmt_"):
        return await handle_format_callback(update, context)


async def run_dummy_web_server():
    """Khởi chạy HTTP server mini ngay lập tức khi bot start để Render nhận diện PORT thành công 100%"""
    try:
        async def handle_ping(request):
            return web.Response(text="Bot Telegram is running healthy 24/7!", content_type="text/plain")

        server_app = web.Application()
        server_app.router.add_get("/", handle_ping)
        server_app.router.add_get("/health", handle_ping)

        port = int(os.environ.get("PORT", 8080))
        runner = web.AppRunner(server_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"===> HEALTH CHECK HTTP SERVER ĐÃ MỞ TẠI 0.0.0.0:{port} <===")
    except Exception as e:
        logger.error(f"Lỗi khởi chạy Dummy Web Server: {e}")


async def post_init(application):
    """Kích hoạt web server ngay khi Application khởi tạo"""
    await run_dummy_web_server()



def main():
    print("Khởi động Telegram Bot...")
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=60.0,
        write_timeout=60.0,
        media_write_timeout=120.0,
    )
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).post_init(post_init).build()

    # Đăng ký các lệnh
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("chatbotzalo", chatbot_start))
    app.add_handler(CommandHandler("stopchatbot", stop_chatbot_command))
    app.add_handler(CommandHandler("autochat", autochat_start))
    app.add_handler(CommandHandler("stopautochat", stop_autochat_command))
    app.add_handler(CommandHandler("tainhac", tainhac_start))
    app.add_handler(CommandHandler("treozalo", treozalo_start))
    app.add_handler(CommandHandler("stoptreo", stop_treo_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("cauhinh", open_config_panel))
    app.add_handler(CommandHandler("quanly", show_manage_saved_config))

    # Xử lý toàn bộ Callback Buttons
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Router nhận tin nhắn văn bản
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("Bot đang chạy và lắng nghe tin nhắn...")
    app.run_polling()


if __name__ == "__main__":
    main()
