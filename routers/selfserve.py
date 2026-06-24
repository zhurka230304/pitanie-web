"""B2C self-serve — человек без тренера сам собирает себе рацион.

Переиспользует общий движок готовых блюд (generate_week, create_cart) напрямую,
без привязки к тренеру/клиенту и без квот. Состояние (профиль, история) на этом
этапе хранится на стороне клиента (браузер); серверные аккаунты добавим позже.
"""
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from services.week_planner import generate_week
from services.food_groups import coverage_report
from services.vkusvill import create_cart

router = APIRouter(prefix="/api/self-serve", tags=["self-serve"])


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
