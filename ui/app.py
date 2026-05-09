import os
import asyncio
import io
from typing import Dict, Optional, Tuple
from urllib.parse import quote
from PIL import Image


import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    BufferedInputFile
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://app:8000").rstrip("/")
DEFAULT_PERIOD = os.environ.get("DEFAULT_PERIOD", "30 days")
DEFAULT_RADIUS = int(os.environ.get("DEFAULT_RADIUS", "1000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

USER_PREFS: Dict[int, Dict] = {}

def get_user_prefs(uid: int) -> Dict:
    if uid not in USER_PREFS:
        USER_PREFS[uid] = {"period": DEFAULT_PERIOD, "radius": DEFAULT_RADIUS, "last_loc": None, "await_city": False}
    return USER_PREFS[uid]

def make_main_kb() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🗺 Общая карта"), KeyboardButton(text="🛣 Общая карта (линии)")],
        [KeyboardButton(text="🏙 По городу"), KeyboardButton(text="📍 По локации", request_location=True)],
        [KeyboardButton(text="⚙️ Настройки")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def settings_kb(current_period: str, current_radius: int) -> InlineKeyboardMarkup:
    periods = ["7 days", "14 days", "30 days", "90 days"]
    radii = [500, 1000, 2000, 3000]
    row1 = [InlineKeyboardButton(text=("✅ " if p == current_period else "") + p, callback_data=f"set_period:{p}") for p in periods]
    row2 = [InlineKeyboardButton(text=("✅ " if r == current_radius else "") + f"{r} м", callback_data=f"set_radius:{r}") for r in radii]
    row3 = [InlineKeyboardButton(text="📗 Легенда", callback_data="legend")]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])

def heatmap_url_global(period: str, metric="p95", w=1200, h=800) -> str:
    return f"{BACKEND_URL}/heatmap_global?period={quote(period)}&metric={metric}&img_w={w}&img_h={h}"

def heatmap_url_global_lines(period: str, w=1200, h=800, line_w_m=10) -> str:
    return (
        f"{BACKEND_URL}/heatmap_global_lines"
        f"?period={quote(period)}&img_w={w}&img_h={h}&line_w_m={line_w_m}"
    )

def heatmap_url_bbox(min_lat, min_lon, max_lat, max_lon, period, metric="p95", w=1000, h=800) -> str:
    return (f"{BACKEND_URL}/heatmap_bbox?min_lat={min_lat}&min_lon={min_lon}"
            f"&max_lat={max_lat}&max_lon={max_lon}&period={quote(period)}&metric={metric}&img_w={w}&img_h={h}")

def heatmap_url_loc(lat: float, lon: float, radius_m: int, period: str, metric="p95", w=800, h=800) -> str:
    return (f"{BACKEND_URL}/heatmap?lat={lat:.6f}&lon={lon:.6f}"
            f"&radius_m={radius_m}&period={quote(period)}&metric={metric}&img_w={w}&img_h={h}")

def heatmap_url_loc_lines(lat: float, lon: float, radius_m: int, period: str, w=800, h=800, line_w_m=10) -> str:
    return (
        f"{BACKEND_URL}/heatmap_lines?lat={lat:.6f}&lon={lon:.6f}"
        f"&radius_m={radius_m}&period={quote(period)}&img_w={w}&img_h={h}&line_w_m={line_w_m}"
    )

async def send_png(chat_id: int, url: str, caption: str):
    """
    Совместимость со старыми вызовами. Тянет PNG с backend, конвертирует в JPEG с белым фоном
    и отправляет как фото (в Telegram будет по центру, без «прилипания» кверху).
    """
    try:
        resp = requests.get(url, timeout=40)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        # если прозрачность — кладём на белый фон
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        out.seek(0)

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(out.read(), filename="heatmap.jpg"),
            caption=caption
        )
    except Exception as e:
        await bot.send_message(chat_id, f"Не удалось получить карту: {e}")

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    _ = get_user_prefs(msg.from_user.id)
    text = (
        "Привет! Я бот вибромониторинга дорожного покрытия.\n\n"
        "Выберите режим:\n"
        "• 🗺 Общая карта — точечная тепловая карта\n"
        "• 🛣 Общая карта (линии) — маршрут линиями\n"
        "• 🏙 По городу — карта по границам города\n"
        "• 📍 По локации — карта вокруг вашей точки\n\n"
        "Период и радиус можно настроить в ⚙️ Настройки."
    )
    await msg.answer(text, reply_markup=make_main_kb())

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer("Нажмите 🗺 Общая карта или выберите другой режим. Настройте период/радиус в ⚙️ Настройки.")

@dp.message(F.text == "⚙️ Настройки")
async def show_settings(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    await msg.answer(
        f"Текущие настройки:\nПериод: {prefs['period']}\nРадиус (для локации): {prefs['radius']} м",
        reply_markup=settings_kb(prefs["period"], prefs["radius"])
    )

@dp.callback_query(F.data.startswith("set_period:"))
async def set_period(cb: CallbackQuery):
    prefs = get_user_prefs(cb.from_user.id)
    period = cb.data.split(":", 1)[1]
    prefs["period"] = period
    await cb.message.edit_reply_markup(reply_markup=settings_kb(prefs["period"], prefs["radius"]))
    await cb.answer(f"Период: {period}")

@dp.callback_query(F.data.startswith("set_radius:"))
async def set_radius(cb: CallbackQuery):
    prefs = get_user_prefs(cb.from_user.id)
    radius = int(cb.data.split(":", 1)[1])
    prefs["radius"] = radius
    await cb.message.edit_reply_markup(reply_markup=settings_kb(prefs["period"], prefs["radius"]))
    await cb.answer(f"Радиус: {radius} м")

@dp.callback_query(F.data == "legend")
async def show_legend(cb: CallbackQuery):
    text = (
        "Легенда:\n"
        "• Зеленый — ровное покрытие\n"
        "• Желтый — слабые неровности\n"
        "• Оранжевый — заметные неровности\n"
        "• Красный — сильные дефекты / кочки / ямы\n\n"
        "Линии и точки строятся по рассчитанному roughness score."
    )
    await cb.message.answer(text)
    await cb.answer()

@dp.message(F.text == "🗺 Общая карта")
async def common_map(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    url = heatmap_url_global(prefs["period"])
    await send_png(msg.chat.id, url, f"Общая карта. Период: {prefs['period']}")

@dp.message(F.text == "🛣 Общая карта (линии)")
async def common_map_lines(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    url = heatmap_url_global_lines(prefs["period"])
    await send_png(msg.chat.id, url, f"Общая карта (линии). Период: {prefs['period']}")

@dp.message(F.text == "🏙 По городу")
async def ask_city(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    prefs["await_city"] = True
    await msg.answer("Введите название города (например: Санкт-Петербург).")

def geocode_city_bbox(query: str) -> Optional[Tuple[float, float, float, float]]:
    # Nominatim OSM
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": "VibroRoadBot/1.0 (edu project)"}
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    arr = r.json()
    if not arr:
        return None
    bb = arr[0].get("boundingbox")
    # boundingbox: [south, north, west, east] в строках
    south = float(bb[0]); north = float(bb[1]); west = float(bb[2]); east = float(bb[3])
    return (south, west, north, east)

@dp.message(F.text & ~F.text.in_({"🗺 Общая карта", "🏙 По городу", "⚙️ Настройки"}))
async def handle_text(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    if prefs.get("await_city"):
        prefs["await_city"] = False
        try:
            bbox = geocode_city_bbox(msg.text.strip())
        except Exception as e:
            await msg.answer(f"Ошибка геокодера: {e}")
            return
        if not bbox:
            await msg.answer("Не нашёл такой город. Попробуйте другое название.")
            return
        min_lat, min_lon, max_lat, max_lon = bbox
        url = heatmap_url_bbox(min_lat, min_lon, max_lat, max_lon, prefs["period"])
        await send_png(msg.chat.id, url, f"Карта по городу «{msg.text}». Период: {prefs['period']}")
        return
    # Иначе игнор или помощь
    await msg.answer("Нажмите кнопку меню или отправьте локацию.")

@dp.message(F.location)
async def on_location(msg: Message):
    prefs = get_user_prefs(msg.from_user.id)
    lat = msg.location.latitude
    lon = msg.location.longitude
    prefs["last_loc"] = (lat, lon)
    await msg.answer("Получил локацию. Строю карту…")
    url = heatmap_url_loc_lines(lat, lon, prefs["radius"], prefs["period"])
    await send_png(msg.chat.id, url, f"Период: {prefs['period']}, радиус: {prefs['radius']} м")

async def main():
    print("Bot started. Backend:", BACKEND_URL)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
