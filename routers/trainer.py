import os
import random
import secrets
from datetime import datetime, timezone, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    TgUser, TrainerClient, ClientProfile, MealPlan, MealLog, ClientInvite,
    TrainerQuota, Subscription, WeightLog,
)
from routers.tg_auth import require_trainer

BOT_USERNAME = os.getenv("BOT_USERNAME", "pitanie_zhurka_bot")
APP_NAME = os.getenv("TG_APP_NAME", "app")
from services.vkusvill import (
    fetch_enriched_items, create_cart, format_item_dict, MEAL_TYPE_QUERIES,
)
from services.gpt import run_gpt_selection
from services.week_planner import generate_week
from services.food_groups import coverage_report
from services.notify import notify_plan_sent

router = APIRouter(prefix="/api/trainer", tags=["trainer"])

CART_BLOCK_DAYS = 3  # готовая еда живёт 2–4 дня — корзина собирается на каждые 3 дня

# Этап 2 (freemium): лимит подборов в месяц. День и неделя стоят одинаково
# по ресурсам — считаем 1 подбор за генерацию. Квота растёт с платящими
# клиентами, приведёнными тренером (revenue-share по канвасу).
# На стадии демо/тестов лимит фактически снят (большой дефолт); перед
# запуском freemium вернуть его строкой FREE_MONTHLY_SEARCHES=30 в .env.
FREE_MONTHLY_SEARCHES = int(os.getenv("FREE_MONTHLY_SEARCHES", "1000000"))
QUOTA_PER_PAYING_CLIENT = int(os.getenv("QUOTA_PER_PAYING_CLIENT", "10"))


async def _get_quota(trainer: TgUser, db: AsyncSession) -> tuple:
    """Вернуть (quota_row, limit), сбросив счётчик при смене календарного месяца."""
    now = datetime.now(timezone.utc)
    quota = (await db.execute(
        select(TrainerQuota).where(TrainerQuota.trainer_id == trainer.id)
    )).scalar_one_or_none()
    if not quota:
        quota = TrainerQuota(trainer_id=trainer.id, searches_this_month=0, quota_reset_at=now)
        db.add(quota)
        await db.flush()
    elif (quota.quota_reset_at.year, quota.quota_reset_at.month) != (now.year, now.month):
        quota.searches_this_month = 0
        quota.quota_reset_at = now

    paying = (await db.execute(
        select(Subscription).where(
            and_(Subscription.referred_trainer_id == trainer.id,
                 Subscription.status == "active")
        )
    )).scalars().all()
    limit = FREE_MONTHLY_SEARCHES + QUOTA_PER_PAYING_CLIENT * len(paying)
    return quota, limit


async def _require_quota(trainer: TgUser, db: AsyncSession) -> TrainerQuota:
    quota, limit = await _get_quota(trainer, db)
    if quota.searches_this_month >= limit:
        raise HTTPException(
            403,
            f"Лимит подборов на месяц исчерпан ({limit}). "
            f"Счётчик обновится 1 числа. Приглашайте клиентов с подпиской — лимит вырастет.",
        )
    return quota


async def _spend_quota(quota: TrainerQuota, db: AsyncSession):
    quota.searches_this_month += 1
    quota.updated_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/quota")
async def get_quota(
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    quota, limit = await _get_quota(trainer, db)
    await db.commit()
    return {"used": quota.searches_this_month, "limit": limit}


# ——— Pydantic schemas ———

class ProfileUpdate(BaseModel):
    birth_date: Optional[date] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    sex: Optional[str] = None
    activity: Optional[float] = None
    goal_formula: Optional[str] = None
    kcal: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbs: Optional[float] = None
    goal: Optional[str] = None
    restrictions: Optional[str] = None


class PlanSaveBody(BaseModel):
    client_id: int
    plan_date: str          # YYYY-MM-DD
    items: list             # [{meal_label, meal_type, dishes:[...]}]
    notes: Optional[str] = None
    cart_url: Optional[str] = None


class WeekSaveBody(BaseModel):
    client_id: int
    days: list              # [{plan_date, meals: [{meal_label, meal_type, kbju, dishes}]}]
    notes: Optional[str] = None


class SendManyBody(BaseModel):
    plan_ids: list


class CoverageBody(BaseModel):
    days: list              # [{plan_date, meals}] — формат search-week


class CreateInviteBody(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    birth_date: Optional[date] = None  # YYYY-MM-DD


class AddClientBody(BaseModel):
    telegram_id: int


class ReplaceDishBody(BaseModel):
    meal_label: str
    meal_type: str          # e.g. "завтрак", "обед", "ужин", "перекус"
    kbju: dict              # {kcal, protein, fat, carbs}
    exclude_xml_ids: list = []


_MEAL_TYPE_MAP = {
    "завтрак": "breakfast",
    "обед": "lunch",
    "обед / ужин": "lunch",
    "ужин": "dinner",
    "перекус": "snack",
    "перекус 1": "snack",
    "перекус 2": "snack",
}


# ——— Helpers ———

async def _get_my_client(trainer: TgUser, client_id: int, db: AsyncSession) -> TgUser:
    link = await db.execute(
        select(TrainerClient).where(
            and_(TrainerClient.trainer_id == trainer.id,
                 TrainerClient.client_id == client_id)
        )
    )
    if not link.scalar_one_or_none():
        raise HTTPException(404, "Клиент не найден")
    client = await db.get(TgUser, client_id)
    if not client:
        raise HTTPException(404, "Пользователь не найден")
    return client


# ——— Endpoints ———

@router.post("/invites")
async def create_invite(
    body: CreateInviteBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    token = secrets.token_urlsafe(12)
    invite = ClientInvite(
        token=token,
        trainer_id=trainer.id,
        first_name=body.first_name,
        last_name=body.last_name,
        birth_date=body.birth_date,
    )
    db.add(invite)
    await db.commit()
    link = f"https://t.me/{BOT_USERNAME}/{APP_NAME}?startapp=invite_{token}"
    return {"link": link, "token": token}


@router.get("/clients")
async def list_clients(
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    links = (await db.execute(
        select(TrainerClient).where(TrainerClient.trainer_id == trainer.id)
    )).scalars().all()

    result = []
    today = date.today()
    for link in links:
        client = await db.get(TgUser, link.client_id)
        if not client:
            continue
        profile = (await db.execute(
            select(ClientProfile).where(ClientProfile.client_id == client.id)
        )).scalar_one_or_none()

        last_plan = (await db.execute(
            select(MealPlan)
            .where(MealPlan.client_id == client.id)
            .order_by(MealPlan.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        result.append({
            "id": client.id,
            "first_name": client.first_name,
            "username": client.username,
            "has_profile": profile is not None,
            "last_plan_date": last_plan.plan_date.isoformat() if last_plan else None,
            "last_plan_status": last_plan.status if last_plan else None,
            "needs_plan": (
                last_plan is None or
                last_plan.plan_date < today or
                last_plan.status == "draft"
            ),
        })
    return result


@router.post("/clients")
async def add_client(
    body: AddClientBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = (await db.execute(
        select(TgUser).where(TgUser.telegram_id == body.telegram_id)
    )).scalar_one_or_none()

    if not client:
        raise HTTPException(404, "Пользователь с таким Telegram ID не найден — он должен сначала открыть приложение")

    existing = (await db.execute(
        select(TrainerClient).where(
            and_(TrainerClient.trainer_id == trainer.id,
                 TrainerClient.client_id == client.id)
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Клиент уже добавлен")

    db.add(TrainerClient(trainer_id=trainer.id, client_id=client.id))
    await db.commit()
    return {"ok": True, "client_id": client.id}


@router.get("/clients/{client_id}")
async def get_client(
    client_id: int,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_my_client(trainer, client_id, db)
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == client.id)
    )).scalar_one_or_none()

    plans = (await db.execute(
        select(MealPlan)
        .where(MealPlan.client_id == client.id)
        .order_by(MealPlan.plan_date.desc())
        .limit(10)
    )).scalars().all()

    return {
        "id": client.id,
        "first_name": client.first_name,
        "username": client.username,
        "profile": {
            "birth_date": profile.birth_date.isoformat() if profile.birth_date else None,
            "weight_kg": profile.weight_kg,
            "height_cm": profile.height_cm,
            "sex": profile.sex,
            "activity": profile.activity,
            "goal_formula": profile.goal_formula,
            "kcal": profile.kcal,
            "protein": profile.protein,
            "fat": profile.fat,
            "carbs": profile.carbs,
            "goal": profile.goal,
            "restrictions": profile.restrictions,
        } if profile else None,
        "plans": [
            {
                "id": p.id,
                "plan_date": p.plan_date.isoformat(),
                "status": p.status,
                "created_at": p.created_at.isoformat(),
            }
            for p in plans
        ],
    }


@router.put("/clients/{client_id}/profile")
async def update_profile(
    client_id: int,
    body: ProfileUpdate,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_my_client(trainer, client_id, db)
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == client.id)
    )).scalar_one_or_none()

    if not profile:
        profile = ClientProfile(client_id=client.id)
        db.add(profile)

    for field in ("birth_date", "weight_kg", "height_cm", "sex", "activity",
                  "goal_formula", "kcal", "protein", "fat", "carbs", "goal", "restrictions"):
        val = getattr(body, field)
        if val is not None:
            setattr(profile, field, val)
    profile.updated_at = datetime.now(timezone.utc)

    await db.commit()
    return {"ok": True}


@router.post("/clients/{client_id}/search")
async def search_for_client(
    client_id: int,
    meal_count: int = Query(3, ge=2, le=5),
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_my_client(trainer, client_id, db)
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == client.id)
    )).scalar_one_or_none()

    if not profile or not profile.kcal:
        raise HTTPException(400, "Сначала заполните КБЖУ профиль клиента")

    quota = await _require_quota(trainer, db)

    # Тот же движок, что и для недели: комбинатор подбирает набор ЦЕЛЫХ
    # упаковок под КБЖУ приёма (раньше каждое блюдо масштабировалось на всю
    # калорийность приёма — приём из 3 блюд выходил в ~3 раза больше цели).
    days = await generate_week(
        P=profile.protein or 0,
        F=profile.fat or 0,
        C=profile.carbs or 0,
        K=profile.kcal,
        restrictions=profile.restrictions or None,
        meal_count=meal_count,
        start=date.today(),
        days_count=1,
    )
    await _spend_quota(quota, db)
    return {"meals": days[0]["meals"]}


@router.post("/clients/{client_id}/search-week")
async def search_week_for_client(
    client_id: int,
    meal_count: int = Query(3, ge=2, le=5),
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_my_client(trainer, client_id, db)
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == client.id)
    )).scalar_one_or_none()

    if not profile or not profile.kcal:
        raise HTTPException(400, "Сначала заполните КБЖУ профиль клиента")

    quota = await _require_quota(trainer, db)

    days = await generate_week(
        P=profile.protein or 0,
        F=profile.fat or 0,
        C=profile.carbs or 0,
        K=profile.kcal,
        restrictions=profile.restrictions or None,
        meal_count=meal_count,
        start=date.today(),
    )
    await _spend_quota(quota, db)
    return {"days": days, "coverage": coverage_report(days)}


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: int,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(MealPlan, plan_id)
    if not plan or plan.trainer_id != trainer.id:
        raise HTTPException(404, "План не найден")
    return {
        "id": plan.id,
        "plan_date": plan.plan_date.isoformat(),
        "status": plan.status,
        "items": plan.items,
        "notes": plan.notes,
        "cart_url": plan.cart_url,
    }


def _sum_kbju_from_items(items: list) -> dict:
    """Суммарные целевые КБЖУ плана (по kbju приёмов)."""
    t = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}
    for meal in items or []:
        kb = meal.get("kbju") or {}
        t["kcal"] += kb.get("kcal", 0)
        t["protein"] += kb.get("protein", 0)
        t["fat"] += kb.get("fat", 0)
        t["carbs"] += kb.get("carbs", 0)
    return {k: round(v) for k, v in t.items()}


@router.get("/clients/{client_id}/logs")
async def client_logs(
    client_id: int,
    limit: int = Query(14, ge=1, le=60),
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    """Дневник клиента для тренера: что съедено по дням против целей плана."""
    await _get_my_client(trainer, client_id, db)
    logs = (await db.execute(
        select(MealLog)
        .where(MealLog.client_id == client_id)
        .order_by(MealLog.log_date.desc())
        .limit(limit)
    )).scalars().all()

    result = []
    for log in logs:
        eaten = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
        items = []
        for it in log.ordered_items or []:
            n = it.get("nutrition") or {}
            eaten["kcal"] += n.get("calories", 0) or 0
            eaten["protein"] += n.get("protein", 0) or 0
            eaten["fat"] += n.get("fat", 0) or 0
            eaten["carbs"] += n.get("carbohydrates", 0) or 0
            items.append({"name": it.get("name"), "needed_g": it.get("needed_g")})

        target = None
        if log.plan_id:
            plan = await db.get(MealPlan, log.plan_id)
            if plan:
                target = _sum_kbju_from_items(plan.items)

        result.append({
            "log_date": log.log_date.isoformat(),
            "eaten": {k: round(v) for k, v in eaten.items()},
            "target": target,
            "items": items,
        })
    return result


@router.get("/clients/{client_id}/weight")
async def client_weight_history(
    client_id: int,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_client(trainer, client_id, db)
    logs = (await db.execute(
        select(WeightLog)
        .where(WeightLog.client_id == client_id)
        .order_by(WeightLog.log_date.desc())
        .limit(30)
    )).scalars().all()
    return [{"log_date": w.log_date.isoformat(), "weight_kg": w.weight_kg} for w in logs]


@router.post("/plans/coverage")
async def recalc_coverage(
    body: CoverageBody,
    trainer: TgUser = Depends(require_trainer),
):
    """Пересчёт покрытия пищевых групп после ручной замены блюда (чистая функция)."""
    return {"coverage": coverage_report(body.days)}


@router.post("/plans/week")
async def save_week(
    body: WeekSaveBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_client(trainer, body.client_id, db)
    days = body.days
    if not days:
        raise HTTPException(400, "Пустая неделя")

    # Корзины по блокам срока годности. Упаковка попадает в заказ один раз:
    # переносы-половинки помечены in_cart=False ещё в планировщике.
    block_carts = []
    for bstart in range(0, len(days), CART_BLOCK_DAYS):
        xml_ids, seen = [], set()
        for day in days[bstart:bstart + CART_BLOCK_DAYS]:
            for meal in day.get("meals", []):
                for dish in meal.get("dishes", []):
                    xid = dish.get("xml_id")
                    if xid and dish.get("in_cart", True) and str(xid) not in seen:
                        seen.add(str(xid))
                        xml_ids.append(xid)
        block_carts.append(await create_cart(xml_ids) if xml_ids else None)

    plan_ids = []
    for i, day in enumerate(days):
        plan_date = date.fromisoformat(day["plan_date"])
        items = day.get("meals", [])
        cart_url = block_carts[i // CART_BLOCK_DAYS]

        existing = (await db.execute(
            select(MealPlan).where(
                and_(MealPlan.client_id == body.client_id,
                     MealPlan.plan_date == plan_date)
            )
        )).scalar_one_or_none()

        if existing:
            existing.items = items
            existing.notes = body.notes
            existing.cart_url = cart_url
            existing.status = "draft"
            plan = existing
        else:
            plan = MealPlan(
                client_id=body.client_id,
                trainer_id=trainer.id,
                plan_date=plan_date,
                items=items,
                notes=body.notes,
                cart_url=cart_url,
                status="draft",
            )
            db.add(plan)
        await db.flush()
        plan_ids.append(plan.id)

    await db.commit()
    return {"ok": True, "plan_ids": plan_ids}


@router.post("/plans/send-many")
async def send_plans(
    body: SendManyBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    sent = 0
    by_client: dict = {}
    for pid in body.plan_ids:
        plan = await db.get(MealPlan, pid)
        if plan and plan.trainer_id == trainer.id:
            plan.status = "sent"
            plan.sent_at = datetime.now(timezone.utc)
            sent += 1
            by_client.setdefault(plan.client_id, []).append(plan.plan_date)
    await db.commit()

    notified = False
    for client_id, dates in by_client.items():
        client = await db.get(TgUser, client_id)
        if client and client.telegram_id:
            notified = await notify_plan_sent(client.telegram_id, dates) or notified

    return {"ok": True, "sent": sent, "notified": notified}


@router.post("/plans")
async def save_plan(
    body: PlanSaveBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_client(trainer, body.client_id, db)

    # Auto-generate VkusVill cart from all dishes in the plan.
    # Переносы-половинок (in_cart=False) и дубли упаковок не заказываем повторно
    xml_ids, seen = [], set()
    for meal in body.items:
        for dish in meal.get("dishes", []):
            xid = dish.get("xml_id")
            if xid and dish.get("in_cart", True) and str(xid) not in seen:
                seen.add(str(xid))
                xml_ids.append(xid)
    cart_url = await create_cart(xml_ids) if xml_ids else None

    plan_date = date.fromisoformat(body.plan_date)
    existing = (await db.execute(
        select(MealPlan).where(
            and_(MealPlan.client_id == body.client_id,
                 MealPlan.plan_date == plan_date)
        )
    )).scalar_one_or_none()

    if existing:
        existing.items = body.items
        existing.notes = body.notes
        existing.cart_url = cart_url
        existing.status = "draft"
        plan = existing
    else:
        plan = MealPlan(
            client_id=body.client_id,
            trainer_id=trainer.id,
            plan_date=plan_date,
            items=body.items,
            notes=body.notes,
            cart_url=cart_url,
            status="draft",
        )
        db.add(plan)

    await db.commit()
    await db.refresh(plan)
    return {"ok": True, "plan_id": plan.id, "cart_url": plan.cart_url}


@router.post("/plans/{plan_id}/send")
async def send_plan(
    plan_id: int,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(MealPlan, plan_id)
    if not plan or plan.trainer_id != trainer.id:
        raise HTTPException(404, "План не найден")

    plan.status = "sent"
    plan.sent_at = datetime.now(timezone.utc)
    await db.commit()

    notified = False
    client = await db.get(TgUser, plan.client_id)
    if client and client.telegram_id:
        notified = await notify_plan_sent(client.telegram_id, [plan.plan_date])

    return {"ok": True, "notified": notified}


@router.post("/clients/{client_id}/replace-dish")
async def replace_dish(
    client_id: int,
    body: ReplaceDishBody,
    trainer: TgUser = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_my_client(trainer, client_id, db)
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == client.id)
    )).scalar_one_or_none()
    restrictions = (profile.restrictions or "") if profile else ""

    meal_type_key = _MEAL_TYPE_MAP.get(body.meal_type, "lunch")
    queries = MEAL_TYPE_QUERIES.get(meal_type_key, MEAL_TYPE_QUERIES["lunch"])
    selected_queries = random.sample(queries, min(6, len(queries)))

    K = body.kbju.get("kcal", 500)
    P = body.kbju.get("protein", 30)
    F = body.kbju.get("fat", 15)
    C = body.kbju.get("carbs", 50)

    exclude_set = {str(x) for x in body.exclude_xml_ids}

    enriched = await fetch_enriched_items(
        queries=selected_queries,
        preference=restrictions or None,
        meal_type=body.meal_type,
    )
    enriched = [i for i in enriched if str(i.get("xml_id", "")) not in exclude_set]

    # GPT отбирает качественные кандидаты, затем берём целую упаковку,
    # ближайшую по калорийности к заменяемому блюду (без обрезания порции)
    selected = await run_gpt_selection(
        enriched_items=enriched,
        P=P, F=F, C=C, K=K,
        preference=restrictions or None,
        count=5,
        meal_label=body.meal_label,
    )

    if not selected:
        raise HTTPException(404, "Не удалось найти замену, попробуйте ещё раз")

    def _full_kcal(it: dict) -> float:
        try:
            nv = it["nutrition_variants"][0]
            return float(nv["calories"]) * it.get("weight_g", 0) / 100
        except (KeyError, IndexError, ValueError, TypeError):
            return 0.0

    item = min(selected, key=lambda it: abs(_full_kcal(it) - K))
    return {"dish": format_item_dict(item, item.get("weight_g", 0))}
