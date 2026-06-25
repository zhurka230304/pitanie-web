"""B2C self-serve — человек без тренера сам собирает себе рацион.

Переиспользует общий движок готовых блюд (generate_week, create_cart) напрямую,
без привязки к тренеру/клиенту и без квот. Состояние (профиль, история) на этом
этапе хранится на стороне клиента (браузер); серверные аккаунты добавим позже.
"""
import os
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt

from database import get_db
from models import SelfServeStore, User
from auth import hash_password, verify_password, create_token, SECRET_KEY, ALGORITHM
from services.week_planner import generate_week
from services.food_groups import coverage_report
from services.vkusvill import create_cart

router = APIRouter(prefix="/api/self-serve", tags=["self-serve"])


def _tg_id(tg_user: dict):
    """telegram_id из объекта Telegram Login Widget (мягкая проверка)."""
    try:
        return int(tg_user["id"]) if tg_user and tg_user.get("id") else None
    except (ValueError, TypeError, KeyError):
        return None


def _uid_from_token(token: str):
    """user_id из JWT-токена (вход по почте) или None."""
    if not token:
        return None
    try:
        return int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])
    except Exception:
        return None


def _user_key(token: str, tg_user: dict):
    """Единый ключ пользователя: 'u<id>' (почта) или 'tg<id>' (Telegram)."""
    uid = _uid_from_token(token)
    if uid:
        return f"u{uid}"
    tid = _tg_id(tg_user)
    if tid:
        return f"tg{tid}"
    return None


# ── Расчёт КБЖУ по антропометрике (Харрис-Бенедикт — методика проекта) ──
ACTIVITY = {
    "sedentary": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725, "very_high": 1.9,
}


def _bmr(sex: str, weight: float, height: float, age: int) -> float:
    if sex == "male":
        return 66.5 + 13.75 * weight + 5.003 * height - 6.755 * age
    return 655 + 9.6 * weight + 1.8 * height - 4.7 * age


class KbjuBody(BaseModel):
    sex: str = "female"
    weight: float = 60
    height: float = 165
    age: int = 30
    activity: str = "moderate"
    goal: str = "loss"          # loss (−15%) | maintain | gain (+10%)


@router.post("/kbju")
async def selfserve_kbju(b: KbjuBody):
    bmr = _bmr(b.sex, b.weight, b.height, b.age)
    norm = bmr * ACTIVITY.get(b.activity, 1.55)
    kcal = norm * (0.85 if b.goal == "loss" else 1.10 if b.goal == "gain" else 1.0)
    protein = round(b.weight * 1.8)
    fat = round(b.weight * 1.0)
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {
        "kcal": round(kcal), "protein": protein, "fat": fat, "carbs": carbs,
        "bmr": round(bmr), "maintenance": round(norm),
    }


# ── Недельный план готовых блюд под КБЖУ (тот же движок, что у тренера) ──
class WeekBody(BaseModel):
    kcal: float = 1950
    protein: float = 150
    fat: float = 65
    carbs: float = 180
    meal_count: int = 3
    restrictions: str | None = None
    days_count: int = 7


def _day_kcal(meals):
    return sum((d.get("nutrition") or {}).get("calories", 0)
               for m in meals for d in m.get("dishes", []))


def _scale_dish(dish, f):
    """Масштабировать порцию блюда и его КБЖУ на коэффициент f."""
    n = dish.get("nutrition")
    if n:
        for k in ("protein", "fat", "carbohydrates", "calories"):
            if isinstance(n.get(k), (int, float)):
                n[k] = round(n[k] * f, 1)
    if isinstance(dish.get("needed_g"), (int, float)):
        dish["needed_g"] = int(round(dish["needed_g"] * f))
    if isinstance(dish.get("portion"), (int, float)):
        dish["portion"] = round(dish["portion"] * f, 2)


# человеку понятная доля упаковки (без весов): ¼, ⅓, ½, ⅔, ¾, вся
_FRIENDLY = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0]
_FRIENDLY_LABEL = {0.25: "¼ упаковки", 0.33: "⅓ упаковки", 0.5: "половина упаковки",
                   0.67: "⅔ упаковки", 0.75: "¾ упаковки", 1.0: "вся упаковка"}


def _set_portion(dish, new_p):
    cur = dish.get("portion") or 1.0
    if cur > 0:
        _scale_dish(dish, new_p / cur)
    dish["portion"] = new_p
    dish["portion_label"] = _FRIENDLY_LABEL.get(new_p, "вся упаковка")


def _fit_day(meals, target_kcal):
    """Подогнать день под цель ±100, выбирая каждому блюду удобную долю упаковки
    (¼/⅓/½/⅔/¾/вся) — без граммов и весов."""
    dishes = [d for m in meals for d in m.get("dishes", [])
              if (d.get("nutrition") or {}).get("calories")]
    if not dishes or target_kcal <= 0:
        return
    units = [d["nutrition"]["calories"] / (d.get("portion") or 1.0) for d in dishes]
    cur_total = sum(d["nutrition"]["calories"] for d in dishes)
    # не больше целой упаковки (в корзине одна упаковка на блюдо)
    f = max(0.4, min(1.0, target_kcal / cur_total)) if cur_total else 1.0
    choice = [min(_FRIENDLY, key=lambda fr: abs(fr - (d.get("portion") or 1.0) * f))
              for d in dishes]

    def day_total():
        return sum(units[i] * choice[i] for i in range(len(dishes)))

    for _ in range(60):                       # жадно двигаем доли к цели
        err = day_total() - target_kcal
        if abs(err) <= 80:
            break
        best = None
        for i in range(len(dishes)):
            ci = _FRIENDLY.index(choice[i])
            for nj in (ci - 1, ci + 1):
                if 0 <= nj < len(_FRIENDLY):
                    ne = abs(err + units[i] * (_FRIENDLY[nj] - choice[i]))
                    if best is None or ne < best[0]:
                        best = (ne, i, _FRIENDLY[nj])
        if best is None or best[0] >= abs(err):
            break
        choice[best[1]] = best[2]

    for i, d in enumerate(dishes):
        _set_portion(d, choice[i])


@router.post("/week")
async def selfserve_week(b: WeekBody):
    days = await generate_week(
        P=b.protein or 0, F=b.fat or 0, C=b.carbs or 0, K=b.kcal,
        restrictions=(b.restrictions or None),
        meal_count=max(2, min(5, b.meal_count)),
        start=date.today(),
        days_count=max(1, min(7, b.days_count)),
    )
    for day in days:                      # дневная калорийность в пределах ±100
        _fit_day(day.get("meals", []), b.kcal)
    return {"days": days, "coverage": coverage_report(days)}


# ── Корзина ВкусВилл из выбранных дней (блюда с in_cart=True) ──
class CartBody(BaseModel):
    days: list = []            # дни плана (или их подмножество)
    xml_ids: list = []         # либо напрямую список xml_id


@router.post("/cart")
async def selfserve_cart(b: CartBody):
    xml_ids, seen = [], set()
    if b.xml_ids:
        for x in b.xml_ids:
            if x and str(x) not in seen:
                seen.add(str(x))
                xml_ids.append(x)
    else:
        for day in (b.days or []):
            for meal in day.get("meals", []):
                for dish in meal.get("dishes", []):
                    xid = dish.get("xml_id")
                    if xid and dish.get("in_cart", True) and str(xid) not in seen:
                        seen.add(str(xid))
                        xml_ids.append(xid)
    url = await create_cart(xml_ids) if xml_ids else ""
    return {"cart_url": url, "count": len(xml_ids)}


# ── Регистрация/вход по почте (переиспользуем auth.py + модель User) ──
class RegisterBody(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/register")
async def selfserve_register(b: RegisterBody, db: AsyncSession = Depends(get_db)):
    email = (b.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Введите корректную почту")
    if len(b.password) < 6:
        raise HTTPException(400, "Пароль не короче 6 символов")
    exists = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "Эта почта уже зарегистрирована — войдите")
    user = User(email=email, name=(b.name.strip() or email.split("@")[0]),
                hashed_password=hash_password(b.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"token": create_token(user.id), "name": user.name}


@router.post("/login")
async def selfserve_login(b: LoginBody, db: AsyncSession = Depends(get_db)):
    email = (b.email or "").strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(b.password, user.hashed_password):
        raise HTTPException(401, "Неверная почта или пароль")
    return {"token": create_token(user.id), "name": user.name}


# ── Хранилище: профиль + история планов (вход по почте или Telegram) ──
class AuthBody(BaseModel):
    tg_user: dict = {}
    token: str = ""


class SaveProfileBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    data: dict = {}


class SavePlanBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    plan: dict = {}


class GetPlanBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    id: int = 0


@router.get("/config")
async def selfserve_config():
    """Имя бота для кнопки «Войти через Telegram» (пусто — Telegram-вход не настроен)."""
    return {"bot_username": os.getenv("BOT_USERNAME", "")}


async def _get_store(db: AsyncSession, key: str):
    return (await db.execute(
        select(SelfServeStore).where(SelfServeStore.user_key == key)
    )).scalar_one_or_none()


def _disp_name(b):
    return b.tg_user.get("first_name") if b.tg_user else None


@router.post("/profile/get")
async def profile_get(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "profile": None}
    s = await _get_store(db, key)
    return {"authorized": True, "name": (s.name if s else _disp_name(b)),
            "profile": (s.profile if s else None)}


@router.post("/profile/save")
async def profile_save(b: SaveProfileBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    s = await _get_store(db, key)
    if s:
        s.profile = b.data
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), profile=b.data, plans=[]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/save")
async def plan_save(b: SavePlanBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    now = datetime.now()
    entry = {"id": int(now.timestamp()), "created_at": now.strftime("%Y-%m-%d %H:%M"),
             "target": (b.plan or {}).get("target", {}), "plan": b.plan}
    s = await _get_store(db, key)
    if s:
        plans = list(s.plans or [])
        plans.insert(0, entry)
        s.plans = plans[:30]                 # переприсваиваем — иначе JSON не обновится
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), profile=None, plans=[entry]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/history")
async def plan_history(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "plans": []}
    s = await _get_store(db, key)
    out = [{"id": p.get("id"), "created_at": p.get("created_at"), "target": p.get("target", {})}
           for p in (s.plans if s and s.plans else [])]
    return {"authorized": True, "plans": out}


@router.post("/plan/get")
async def plan_get(b: GetPlanBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"plan": None}
    s = await _get_store(db, key)
    for p in (s.plans if s and s.plans else []):
        if p.get("id") == b.id:
            return {"plan": p.get("plan")}
    return {"plan": None}


# ── Трекинг: вес ({date,kg}) и отметки «съел/заказал» по датам ──
class TrackBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    tracking: dict = {}


@router.post("/track/get")
async def track_get(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "tracking": {}}
    s = await _get_store(db, key)
    return {"authorized": True, "tracking": (s.tracking if s and s.tracking else {})}


@router.post("/track/save")
async def track_save(b: TrackBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    s = await _get_store(db, key)
    if s:
        s.tracking = b.tracking
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), tracking=b.tracking, plans=[]))
    await db.commit()
    return {"ok": True}
