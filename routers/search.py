import asyncio
import random
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from database import get_db
from models import User, SearchHistory
from auth import get_optional_user
from services.vkusvill import (
    fetch_enriched_items, create_cart, format_item_dict, calc_portion,
    get_query_pool, MEAL_PLANS, MEAL_TYPE_QUERIES,
)
from services.gpt import run_gpt_selection, run_gpt_combinations, is_single_dish_request
from services.email import send_search_results
from services.seen_dishes import reset_seen_dishes, get_disliked_dish_ids, get_liked_dish_ids

router = APIRouter(prefix="/api/search", tags=["search"])

DELIVERY_SERVICES = {
    "vkusvill": "ВкусВилл",
    "yandex_food": "Яндекс еда",
    "yandex_lavka": "Яндекс Лавка",
    "samokat": "Самокат",
    "kuper": "Купер",
    "ozon_fresh": "Озон Фреш",
}

# Preset preferences per meal type
MEAL_PRESETS = {
    "breakfast": [
        {"label": "Омлет", "value": "омлет"},
        {"label": "Сырники", "value": "сырники"},
        {"label": "Каша", "value": "каша"},
        {"label": "Творог", "value": "творог"},
        {"label": "Блинчики", "value": "блины"},
        {"label": "Запеканка", "value": "запеканка"},
        {"label": "Йогурт", "value": "йогурт"},
    ],
    "lunch": [
        {"label": "Суп", "value": "суп"},
        {"label": "Курица", "value": "курица"},
        {"label": "Говядина", "value": "говядина"},
        {"label": "Паста", "value": "паста"},
        {"label": "Рис с мясом", "value": "рис с мясом"},
        {"label": "Гречка", "value": "гречка с мясом"},
        {"label": "Борщ", "value": "борщ"},
    ],
    "dinner": [
        {"label": "Рыба", "value": "рыба запечённая"},
        {"label": "Лосось", "value": "лосось"},
        {"label": "Индейка", "value": "индейка"},
        {"label": "Салат", "value": "салат с белком"},
        {"label": "Греческий салат", "value": "греческий салат"},
        {"label": "Овощное рагу", "value": "овощное рагу"},
        {"label": "Форель", "value": "форель"},
    ],
    "snack": [
        {"label": "Творог", "value": "творог"},
        {"label": "Йогурт", "value": "йогурт"},
        {"label": "Сырники", "value": "сырники"},
        {"label": "Хумус", "value": "хумус"},
        {"label": "Салат овощной", "value": "салат овощной"},
    ],
}


class SingleSearchRequest(BaseModel):
    proteins: float
    fats: float
    carbs: float
    meal_type: str  # breakfast/lunch/dinner/snack
    preference: Optional[str] = None
    delivery_service: str = "vkusvill"
    city: Optional[str] = None


class FullDaySearchRequest(BaseModel):
    proteins: float
    fats: float
    carbs: float
    meal_count: int  # 2-5
    preferences: list[Optional[str]]  # one per meal
    delivery_service: str = "vkusvill"
    city: Optional[str] = None


def calc_calories(proteins: float, fats: float, carbs: float) -> float:
    return round(proteins * 4 + fats * 9 + carbs * 4, 1)


def split_kbju(P, F, C, K, ratio):
    return round(P * ratio, 1), round(F * ratio, 1), round(C * ratio, 1), round(K * ratio)


def validate_macros(p, f, c):
    if not (0 <= p <= 500):
        raise HTTPException(400, "Белки должны быть от 0 до 500 г")
    if not (0 <= f <= 500):
        raise HTTPException(400, "Жиры должны быть от 0 до 500 г")
    if not (0 <= c <= 800):
        raise HTTPException(400, "Углеводы должны быть от 0 до 800 г")


async def build_queries(preference, meal_type):
    pool = MEAL_TYPE_QUERIES.get(meal_type, [])
    query_pool = get_query_pool(preference)
    if preference and is_single_dish_request(preference):
        return [preference] + random.sample(pool, min(4, len(pool)))
    elif preference:
        return [preference] + random.sample(query_pool, min(5, len(query_pool))) + random.sample(pool, min(3, len(pool)))
    elif meal_type == "breakfast":
        return random.sample(pool, min(5, len(pool)))
    else:
        return random.sample(pool, min(5, len(pool))) + random.sample(query_pool, min(3, len(query_pool)))


async def do_single_search(P, F, C, K, preference, meal_type):
    queries = await build_queries(preference, meal_type)
    enriched = await fetch_enriched_items(queries, preference, meal_type=meal_type)
    selected = await run_gpt_selection(enriched, P, F, C, K, preference, count=5, meal_label=meal_type)
    return selected


async def do_single_combinations(P, F, C, K, preference, meal_type, disliked_ids: set | None = None, liked_ids: set | None = None):
    queries = await build_queries(preference, meal_type)
    max_cand = 25 if meal_type == "breakfast" else 35
    enriched = await fetch_enriched_items(
        queries, preference,
        disliked_ids=disliked_ids or set(),
        liked_ids=liked_ids or set(),
        meal_type=meal_type, max_candidates=max_cand,
    )
    combinations = await run_gpt_combinations(enriched, P, F, C, K, preference, meal_label=meal_type, liked_ids=liked_ids or set())
    return combinations


def calc_combo_total(items_out: list) -> dict:
    total = {"protein": 0.0, "fat": 0.0, "carbohydrates": 0.0, "calories": 0.0}
    for item in items_out:
        n = item.get("nutrition") or {}
        total["protein"] += n.get("protein", 0)
        total["fat"] += n.get("fat", 0)
        total["carbohydrates"] += n.get("carbohydrates", 0)
        total["calories"] += n.get("calories", 0)
    return {k: round(v, 1) for k, v in total.items()}


@router.get("/presets")
async def get_presets():
    return {"presets": MEAL_PRESETS}


@router.get("/services")
async def get_services():
    return {"services": [{"id": k, "name": v} for k, v in DELIVERY_SERVICES.items()]}


@router.post("/single")
async def search_single(
    data: SingleSearchRequest,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    validate_macros(data.proteins, data.fats, data.carbs)
    if data.meal_count if hasattr(data, "meal_count") else None:
        pass

    K = calc_calories(data.proteins, data.fats, data.carbs)
    P, F, C = data.proteins, data.fats, data.carbs

    disliked_ids: set = set()
    liked_ids: set = set()
    if user:
        disliked_ids = await get_disliked_dish_ids(db, user.id, data.meal_type)
        liked_ids = await get_liked_dish_ids(db, user.id, data.meal_type)

    raw_combinations = await do_single_combinations(P, F, C, K, data.preference, data.meal_type, disliked_ids=disliked_ids, liked_ids=liked_ids)

    if not any(raw_combinations):
        raise HTTPException(404, "Не удалось найти подходящие блюда. Попробуй изменить параметры.")

    combinations_out = []
    for combo in raw_combinations:
        n = max(len(combo), 1)
        items_out = [format_item_dict(item, calc_portion(item, K / n)) for item in combo]
        cart_url = ""
        if data.delivery_service == "vkusvill":
            try:
                cart_url = await create_cart([item["xml_id"] for item in combo])
            except Exception:
                pass
        combinations_out.append({
            "items": items_out,
            "total": calc_combo_total(items_out),
            "cart_url": cart_url,
        })

    result = {
        "combinations": combinations_out,
        "calories": K,
        "proteins": P,
        "fats": F,
        "carbs": C,
        "meal_type": data.meal_type,
        "delivery_service": data.delivery_service,
    }

    if user:
        history = SearchHistory(
            user_id=user.id,
            proteins=P, fats=F, carbs=C, calories=K,
            mode="single",
            meal_type=data.meal_type,
            preferences={"0": data.preference} if data.preference else None,
            delivery_service=data.delivery_service,
            city=data.city,
            results=result,
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        result["search_id"] = history.id

        try:
            await send_search_results(user.email, user.name, result)
        except Exception:
            pass

    return result


@router.delete("/seen/{meal_type}")
async def reset_seen(
    meal_type: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    if not user:
        raise HTTPException(401, "Требуется авторизация")
    await reset_seen_dishes(db, user.id, meal_type)
    return {"ok": True}


@router.post("/full-day")
async def search_full_day(
    data: FullDaySearchRequest,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    validate_macros(data.proteins, data.fats, data.carbs)
    if data.meal_count not in (2, 3, 4, 5):
        raise HTTPException(400, "Количество приёмов пищи: 2, 3, 4 или 5")

    K = calc_calories(data.proteins, data.fats, data.carbs)
    P, F, C = data.proteins, data.fats, data.carbs
    plan = MEAL_PLANS[data.meal_count]

    prefs = list(data.preferences) + [None] * data.meal_count
    prefs = prefs[:data.meal_count]

    label_to_meal_type = {
        "Завтрак": "breakfast", "Обед": "lunch", "Обед / ужин": "lunch",
        "Ужин": "dinner", "Перекус": "snack", "Перекус 1": "snack", "Перекус 2": "snack",
    }

    async def fetch_meal(label, ratio, meal_pool, pref):
        mp, mf, mc, mk = split_kbju(P, F, C, K, ratio)
        pool = get_query_pool(pref)
        meal_type = label_to_meal_type.get(label)
        try:
            if pref and is_single_dish_request(pref):
                queries = [pref] + random.sample(meal_pool, min(4, len(meal_pool)))
            elif pref:
                queries = [pref] + random.sample(pool, min(4, len(pool))) + random.sample(meal_pool, min(2, len(meal_pool)))
            else:
                queries = random.sample(meal_pool, min(4, len(meal_pool))) + random.sample(pool, min(3, len(pool)))
            enriched = await fetch_enriched_items(queries, pref, meal_type=meal_type, max_candidates=25)
            items = await run_gpt_selection(enriched, mp, mf, mc, mk, pref, count=2, meal_label=label)
        except Exception:
            items = []
        return label, mk, items

    meal_results = []
    for i, (label, ratio, meal_pool) in enumerate(plan):
        result = await fetch_meal(label, ratio, meal_pool, prefs[i])
        meal_results.append(result)

    meals = []
    all_xml_ids = []
    for label, target_k, items in meal_results:
        items_out = [format_item_dict(item, calc_portion(item, target_k / max(len(items), 1))) for item in items]
        all_xml_ids.extend([item["xml_id"] for item in items])
        meals.append({
            "label": label,
            "target_calories": target_k,
            "items": items_out,
        })

    cart_url = ""
    if data.delivery_service == "vkusvill" and all_xml_ids:
        try:
            cart_url = await create_cart(all_xml_ids)
        except Exception:
            pass

    result = {
        "meals": meals,
        "cart_url": cart_url,
        "calories": K,
        "proteins": P,
        "fats": F,
        "carbs": C,
        "meal_count": data.meal_count,
        "delivery_service": data.delivery_service,
    }

    if user:
        history = SearchHistory(
            user_id=user.id,
            proteins=P, fats=F, carbs=C, calories=K,
            mode="full",
            meal_count=data.meal_count,
            preferences={str(i): p for i, p in enumerate(prefs) if p},
            delivery_service=data.delivery_service,
            city=data.city,
            results=result,
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        result["search_id"] = history.id
        try:
            await send_search_results(user.email, user.name, result)
        except Exception:
            pass

    return result
