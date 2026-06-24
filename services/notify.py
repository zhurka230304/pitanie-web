"""Telegram-уведомления клиентам через Bot API.

Уведомление — best effort: если клиент не нажимал Start у бота,
sendMessage вернёт 403 — логируем и не ломаем основной сценарий.
"""
import logging
import os

import httpx

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "pitanie_zhurka_bot")
APP_NAME = os.getenv("TG_APP_NAME", "app")

RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def fmt_date_ru(d) -> str:
    return f"{d.day} {RU_MONTHS[d.month - 1]}"


async def send_telegram_message(telegram_id: int, text: str) -> bool:
    if not BOT_TOKEN or not telegram_id:
        logging.warning("notify: BOT_TOKEN или telegram_id не заданы — пропуск")
        return False

    # Кнопку-ссылку на Mini App добавляем только если короткое имя приложения
    # задано в .env. Иначе URL https://t.me/bot/app невалиден и Telegram
    # отклоняет ВЕСЬ запрос (BUTTON_URL_INVALID) — уведомление не доходит.
    reply_markup = None
    if APP_NAME and APP_NAME != "app":
        reply_markup = {
            "inline_keyboard": [[{
                "text": "Открыть план 🦢",
                "url": f"https://t.me/{BOT_USERNAME}/{APP_NAME}",
            }]]
        }

    async def _post(markup):
        payload = {"chat_id": telegram_id, "text": text}
        if markup:
            payload["reply_markup"] = markup
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload, timeout=10,
            )
            return r.json()

    try:
        data = await _post(reply_markup)
        if data.get("ok"):
            return True
        desc = str(data.get("description", ""))
        logging.warning(f"notify: sendMessage failed for {telegram_id}: {str(data)[:200]}")
        # повтор без кнопки, если проблема именно в URL кнопки
        if reply_markup and "BUTTON_URL" in desc.upper():
            data = await _post(None)
            return bool(data.get("ok"))
        return False
    except Exception as e:
        logging.warning(f"notify: {e!r}")
        return False


async def notify_plan_sent(telegram_id: int, dates: list) -> bool:
    """Уведомление об отправленном плане: один день или диапазон недели."""
    if not dates:
        return False
    dates = sorted(dates)
    if len(dates) == 1:
        text = (
            f"🦢 Тебе пришёл план питания на {fmt_date_ru(dates[0])}!\n"
            f"Открой Журку: отмечай съеденное и собери корзину ВкусВилл."
        )
    else:
        text = (
            f"🦢 Тебе пришёл план питания на неделю "
            f"({fmt_date_ru(dates[0])} — {fmt_date_ru(dates[-1])})!\n"
            f"Открой Журку: отмечай съеденное и собирай корзины ВкусВилл."
        )
    return await send_telegram_message(telegram_id, text)
