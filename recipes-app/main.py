"""Журка-Рецепты — Telegram Mini App: рецепты + подбор под КБЖУ + корзина ВкусВилл.

Самостоятельное приложение, не зависит от приложения готовых блюд.
Личный кабинет: вход через Telegram, профиль (КБЖУ/настройки) хранится в SQLite.
"""
import os
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from kbju import calc_kbju
from planner import plan_week, build_meal, aggregate_shopping
import vkusvill

app = FastAPI(title="Журка — Рецепты")

BASE = os.path.dirname(os.path.abspath(__file__))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
DB_PATH = os.getenv("RECIPES_DB", os.path.join(BASE, "data", "profiles.db"))


def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS profiles (tg_id TEXT PRIMARY KEY, name TEXT, data TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS plans ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id TEXT, created_at TEXT, data TEXT)")
    return con


@app.on_event("startup")
def _init_db():
    con = _db()
    con.commit()
    con.close()
    print(f"[recipes] DB_PATH = {DB_PATH}")


def _validate_sig(parsed: dict) -> bool:
    """Проверка подписи Telegram (best effort). Не блокирует — только для инфо."""
    if not BOT_TOKEN:
        return False
    try:
        p = dict(parsed)
        hash_val = p.pop("hash", [""])[0]
        check = "\n".join(f"{k}={v[0]}" for k, v in sorted(p.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, hash_val)
    except Exception:
        return False


def _tg_user(init_data: str = "", tg_user: dict | None = None):
    """(tg_id, first_name) для пользователя Telegram.
    Два источника: initData (Mini App внутри Telegram) ИЛИ объект Telegram Login
    Widget (вход на обычном сайте). Подпись — мягкая проверка (без чувствительных
    данных). Аноним без Telegram (None) хранит данные локально в браузере."""
    if init_data:
        try:
            parsed = dict(parse_qs(init_data, keep_blank_values=True))
            user = json.loads(parsed.get("user", ["{}"])[0])
            uid = user.get("id")
            if uid:
                return str(uid), user.get("first_name")
        except Exception:
            pass
    if tg_user and tg_user.get("id"):
        return str(tg_user["id"]), tg_user.get("first_name")
    return None, None


class KbjuBody(BaseModel):
    sex: str = "female"
    weight: float = 60
    height: float = 165
    age: int = 30
    activity: str = "3"
    goal: str = "loss"


class PlanBody(BaseModel):
    kcal: float = 1950
    protein: float = 153
    fat: float = 68
    carbs: float = 160
    days: int = 7
    breakfast_max_time: int = 20
    breakfast_freq: int = 7
    lunch_freq: int = 2
    dinner_freq: int = 2
    snacks: int = 0
    snack_quick: bool = True
    overnight: bool = True
    exclude: str = ""


class CartBody(BaseModel):
    ingredients: list


class SwapBody(BaseModel):
    kind: str = "main"          # breakfast / main / snack
    kcal: float = 500
    label: str = "Обед"
    exclude: str = ""
    avoid_id: str = ""
    overnight: bool = True
    breakfast_max_time: int = 20
    snack_quick: bool = True


class ShoppingBody(BaseModel):
    ingredients: list = []


class AuthBody(BaseModel):
    init_data: str = ""
    tg_user: dict = {}


class SaveProfileBody(BaseModel):
    init_data: str = ""
    tg_user: dict = {}
    data: dict


class SavePlanBody(BaseModel):
    init_data: str = ""
    tg_user: dict = {}
    plan: dict


class GetPlanBody(BaseModel):
    init_data: str = ""
    tg_user: dict = {}
    id: int


@app.get("/api/config")
async def api_config():
    """Конфиг для фронта: имя бота для кнопки «Войти через Telegram»."""
    return {"bot_username": BOT_USERNAME}


@app.post("/api/kbju")
async def api_kbju(b: KbjuBody):
    return calc_kbju(b.sex, b.weight, b.height, b.age, b.activity, b.goal)


@app.post("/api/plan")
async def api_plan(b: PlanBody):
    target = {"kcal": b.kcal, "protein": b.protein, "fat": b.fat, "carbs": b.carbs}
    return plan_week(target, days=max(1, min(7, b.days)),
                     breakfast_max_time=b.breakfast_max_time,
                     breakfast_freq=b.breakfast_freq, lunch_freq=b.lunch_freq,
                     dinner_freq=b.dinner_freq, snacks=b.snacks,
                     snack_quick=b.snack_quick, overnight=b.overnight,
                     exclude=b.exclude)


@app.post("/api/plan/swap")
async def api_swap(b: SwapBody):
    m = build_meal(b.kind, b.kcal, exclude=b.exclude, avoid_id=b.avoid_id or None,
                   overnight=b.overnight, breakfast_max_time=b.breakfast_max_time,
                   snack_quick=b.snack_quick)
    m["label"] = b.label
    return {"meal": m}


@app.post("/api/shopping")
async def api_shopping(b: ShoppingBody):
    return {"shopping_raw_g": aggregate_shopping(b.ingredients)}


@app.post("/api/cart")
async def api_cart(b: CartBody):
    matched = await vkusvill.match_ingredients(b.ingredients)
    xml_ids = [m["xml_id"] for m in matched["found"] if m.get("xml_id")]
    url = await vkusvill.create_cart(xml_ids) if xml_ids else ""
    return {"cart_url": url, **matched}


@app.post("/api/profile/get")
async def profile_get(b: AuthBody):
    uid, name = _tg_user(b.init_data, b.tg_user)
    if not uid:
        return {"authorized": False, "profile": None}
    con = _db()
    row = con.execute("SELECT data FROM profiles WHERE tg_id=?", (uid,)).fetchone()
    con.close()
    profile = json.loads(row[0]) if row else None
    return {"authorized": True, "name": name, "profile": profile}


@app.post("/api/profile/save")
async def profile_save(b: SaveProfileBody):
    uid, name = _tg_user(b.init_data, b.tg_user)
    if not uid:
        return {"ok": False, "authorized": False}
    con = _db()
    con.execute(
        "INSERT INTO profiles (tg_id, name, data) VALUES (?,?,?) "
        "ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name, data=excluded.data",
        (uid, name or "", json.dumps(b.data, ensure_ascii=False)),
    )
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/plan/save")
async def plan_save(b: SavePlanBody):
    import datetime
    uid, _ = _tg_user(b.init_data, b.tg_user)
    if not uid:
        return {"ok": False, "authorized": False}
    con = _db()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO plans (tg_id, created_at, data) VALUES (?,?,?)",
                (uid, now, json.dumps(b.plan, ensure_ascii=False)))
    # храним только последние 30 планов на пользователя
    con.execute("DELETE FROM plans WHERE tg_id=? AND id NOT IN "
                "(SELECT id FROM plans WHERE tg_id=? ORDER BY id DESC LIMIT 30)", (uid, uid))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/plan/history")
async def plan_history(b: AuthBody):
    uid, _ = _tg_user(b.init_data, b.tg_user)
    if not uid:
        return {"authorized": False, "plans": []}
    con = _db()
    rows = con.execute("SELECT id, created_at, data FROM plans WHERE tg_id=? ORDER BY id DESC",
                       (uid,)).fetchall()
    con.close()
    out = []
    for rid, created, data in rows:
        try:
            tgt = json.loads(data).get("target", {})
        except Exception:
            tgt = {}
        out.append({"id": rid, "created_at": created, "target": tgt})
    return {"authorized": True, "plans": out}


@app.get("/api/_debug")
async def debug():
    con = _db()
    pc = con.execute("SELECT count(*) FROM profiles").fetchone()[0]
    plc = con.execute("SELECT count(*) FROM plans").fetchone()[0]
    con.close()
    return {"db_path": DB_PATH, "profiles": pc, "plans": plc,
            "bot_token_set": bool(BOT_TOKEN), "bot_username": BOT_USERNAME}


@app.post("/api/plan/get")
async def plan_get(b: GetPlanBody):
    uid, _ = _tg_user(b.init_data, b.tg_user)
    if not uid:
        return {"plan": None}
    con = _db()
    row = con.execute("SELECT data FROM plans WHERE id=? AND tg_id=?", (b.id, uid)).fetchone()
    con.close()
    return {"plan": json.loads(row[0]) if row else None}


app.mount("/img", StaticFiles(directory=os.path.join(BASE, "data", "img")), name="img")
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"),
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
