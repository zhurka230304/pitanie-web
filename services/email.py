import asyncio
import smtplib
import os
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# явно загружаем .env из папки проекта
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=True)

SMTP_HOST = "smtp.yandex.ru"
SMTP_PORT = 465


def _send_sync(to: str, subject: str, html: str):
    smtp_user = os.getenv("SMTP_USER", "zhurka.pitanie@yandex.ru")
    smtp_password = os.getenv("SMTP_PASSWORD", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to, msg.as_string())


async def send_email(to: str, subject: str, html: str):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send_sync, to, subject, html)


async def send_welcome(to: str, name: str):
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px;background:#f4f7f0;">
      <div style="background:#ffffff;border-radius:16px;padding:36px 40px;">
        <h1 style="font-size:1.5rem;color:#1a1a1a;margin-bottom:8px;">Привет, {name}! 👋</h1>
        <p style="color:#6b7a5e;font-size:1rem;line-height:1.6;margin-bottom:24px;">
          Ты зарегистрировался в <strong>Журке</strong> — сервисе подбора готовых блюд из ВкусВилла по твоему КБЖУ.
        </p>
        <div style="background:#e8f4db;border-radius:12px;padding:20px 24px;margin-bottom:24px;">
          <p style="color:#5a9032;font-size:0.95rem;line-height:1.6;margin:0;">
            Введи свои белки, жиры и углеводы — и мы подберём готовые блюда, которые точно впишутся в твой рацион.
          </p>
        </div>
        <p style="color:#6b7a5e;font-size:0.85rem;margin:0;">
          Если не ты регистрировался — просто проигнорируй это письмо.
        </p>
      </div>
    </div>
    """
    await send_email(to, "Добро пожаловать в Журку!", html)


async def send_search_results(to: str, name: str, results: dict):
    mode = results.get("mode", "single")
    proteins = results.get("proteins", 0)
    fats = results.get("fats", 0)
    carbs = results.get("carbs", 0)
    calories = results.get("calories", 0)
    cart_url = results.get("cart_url", "")

    # Build items list
    items_html = ""
    if results.get("items"):
        for item in results["items"]:
            n = item.get("nutrition") or {}
            items_html += _item_row(item["name"], item.get("needed_g", 0), n, item.get("url", ""))
    elif results.get("meals"):
        for meal in results["meals"]:
            items_html += f'<tr><td colspan="2" style="padding:12px 0 6px;font-weight:700;color:#5a9032;font-size:0.85rem;text-transform:uppercase;letter-spacing:0.5px;">{meal["label"]}</td></tr>'
            for item in meal.get("items", []):
                n = item.get("nutrition") or {}
                items_html += _item_row(item["name"], item.get("needed_g", 0), n, item.get("url", ""))

    cart_btn = ""
    if cart_url:
        cart_btn = f'<a href="{cart_url}" style="display:inline-block;background:#7ab648;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:12px;font-weight:600;font-size:1rem;margin-top:24px;">🛒 Добавить в корзину ВкусВилла</a>'

    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;background:#f4f7f0;">
      <div style="background:#ffffff;border-radius:16px;padding:36px 40px;">
        <h1 style="font-size:1.4rem;color:#1a1a1a;margin-bottom:4px;">Твои блюда готовы, {name}!</h1>
        <p style="color:#6b7a5e;font-size:0.9rem;margin-bottom:20px;">
          Б {proteins}г · Ж {fats}г · У {carbs}г · {int(calories)} ккал
        </p>
        <table style="width:100%;border-collapse:collapse;">
          {items_html}
        </table>
        {cart_btn}
        <p style="color:#6b7a5e;font-size:0.8rem;margin-top:24px;margin-bottom:0;">
          Журка — подбор блюд из ВкусВилла по КБЖУ
        </p>
      </div>
    </div>
    """
    await send_email(to, "Журка нашла блюда для тебя 🥗", html)


def _item_row(name: str, needed_g: int, nutrition: dict, url: str) -> str:
    kcal = nutrition.get("calories", "")
    protein = nutrition.get("protein", "")
    kcal_str = f" · {kcal} ккал" if kcal else ""
    protein_str = f" · Б {protein}г" if protein else ""
    link = f'<a href="{url}" style="color:#7ab648;text-decoration:none;font-size:0.78rem;">открыть →</a>' if url else ""
    return f"""
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid #e8f4db;vertical-align:top;">
        <div style="font-weight:600;font-size:0.9rem;color:#1a1a1a;">{name}</div>
        <div style="font-size:0.8rem;color:#6b7a5e;margin-top:2px;">{needed_g}г{protein_str}{kcal_str}</div>
      </td>
      <td style="padding:10px 0;border-bottom:1px solid #e8f4db;text-align:right;vertical-align:top;">{link}</td>
    </tr>
    """
