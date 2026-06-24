"""B2C self-serve — человек без тренера сам собирает себе рацион.

Переиспользует общий движок готовых блюд (generate_week, create_cart) напрямую,
без привязки к тренеру/клиенту и без квот. Состояние (профиль, история) на этом
этапе хранится на стороне клиента (браузер); серверные аккаунты добавим позже.
"""
import os
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import SelfServeStore
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


@router.post("/week")
async def selfserve_week(b: WeekBody):
    days = await generate_week(
        P=b.protein or 0, F=b.fat or 0, C=b.carbs or 0, K=b.kcal,
        restrictions=(b.restrictions or None),
        meal_count=max(2, min(5, b.meal_count)),
        start=date.today(),
        days_count=max(1, min(7, b.days_count)),
    )
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


# ── Вход (Telegram Login Widget) и хранилище: профиль + история планов ──
class AuthBody(BaseModel):
    tg_user: dict = {}


class SaveProfileBody(BaseModel):
    tg_user: dict = {}
    data: dict = {}


class SavePlanBody(BaseModel):
    tg_user: dict = {}
    plan: dict = {}


class GetPlanBody(BaseModel):
    tg_user: dict = {}
    id: int = 0


@router.get("/config")
async def selfserve_config():
    """Имя бота для кнопки «Войти через Telegram» (пусто — входа нет, только гость)."""
    return {"bot_username": os.getenv("BOT_USERNAME", "")}


async def _get_store(db: AsyncSession, tid: int):
    return (await db.execute(
        select(SelfServeStore).where(SelfServeStore.telegram_id == tid)
    )).scalar_one_or_none()


@router.post("/profile/get")
async def profile_get(b: AuthBody, db: AsyncSession = Depends(get_db)):
    tid = _tg_id(b.tg_user)
    if not tid:
        return {"authorized": False, "profile": None}
    s = await _get_store(db, tid)
    return {"authorized": True, "name": b.tg_user.get("first_name"),
            "profile": (s.profile if s else None)}


@router.post("/profile/save")
async def profile_save(b: SaveProfileBody, db: AsyncSession = Depends(get_db)):
    tid = _tg_id(b.tg_user)
    if not tid:
        return {"ok": False, "authorized": False}
    s = await _get_store(db, tid)
    if s:
        s.profile = b.data
        s.name = b.tg_user.get("first_name")
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(telegram_id=tid, name=b.tg_user.get("first_name"),
                              profile=b.data, plans=[]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/save")
async def plan_save(b: SavePlanBody, db: AsyncSession = Depends(get_db)):
    tid = _tg_id(b.tg_user)
    if not tid:
        return {"ok": False, "authorized": False}
    now = datetime.now()
    entry = {"id": int(now.timestamp()), "created_at": now.strftime("%Y-%m-%d %H:%M"),
             "target": (b.plan or {}).get("target", {}), "plan": b.plan}
    s = await _get_store(db, tid)
    if s:
        plans = list(s.plans or [])
        plans.insert(0, entry)
        s.plans = plans[:30]                 # переприсваиваем — иначе JSON не обновится
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(telegram_id=tid, name=b.tg_user.get("first_name"),
                              profile=None, plans=[entry]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/history")
async def plan_history(b: AuthBody, db: AsyncSession = Depends(get_db)):
    tid = _tg_id(b.tg_user)
    if not tid:
        return {"authorized": False, "plans": []}
    s = await _get_store(db, tid)
    out = [{"id": p.get("id"), "created_at": p.get("created_at"), "target": p.get("target", {})}
           for p in (s.plans if s and s.plans else [])]
    return {"authorized": True, "plans": out}


@router.post("/plan/get")
async def plan_get(b: GetPlanBody, db: AsyncSession = Depends(get_db)):
    tid = _tg_id(b.tg_user)
    if not tid:
        return {"plan": None}
    s = await _get_store(db, tid)
    for p in (s.plans if s and s.plans else []):
        if p.get("id") == b.id:
            return {"plan": p.get("plan")}
    return {"plan": None}
