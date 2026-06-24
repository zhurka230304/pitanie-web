import os
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from datetime import timedelta
from models import TgUser, ClientProfile, MealPlan, MealLog, TrainerClient, ClientInvite, WeightLog
from routers.tg_auth import get_tg_user
from services.vkusvill import create_cart

router = APIRouter(prefix="/api/client", tags=["client"])

BOT_USERNAME = os.getenv("BOT_USERNAME", "pitanie_zhurka_bot")


class JoinBody(BaseModel):
    token: str  # invite token


class LogBody(BaseModel):
    plan_id: int
    ordered_items: list   # [{name, url, needed_g}]


@router.post("/join")
async def join_trainer(
    body: JoinBody,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    invite = (await db.execute(
        select(ClientInvite).where(ClientInvite.token == body.token)
    )).scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Ссылка недействительна")

    # link to trainer
    existing = (await db.execute(
        select(TrainerClient).where(TrainerClient.client_id == user.id)
    )).scalar_one_or_none()
    if not existing:
        db.add(TrainerClient(trainer_id=invite.trainer_id, client_id=user.id, invite_token=body.token))

    # apply pre-filled profile
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == user.id)
    )).scalar_one_or_none()
    if not profile:
        profile = ClientProfile(client_id=user.id)
        db.add(profile)
    if invite.birth_date:
        profile.birth_date = invite.birth_date

    invite.used = True
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def get_me(
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == user.id)
    )).scalar_one_or_none()

    trainer_link = (await db.execute(
        select(TrainerClient).where(TrainerClient.client_id == user.id)
    )).scalar_one_or_none()

    trainer = None
    if trainer_link:
        t = await db.get(TgUser, trainer_link.trainer_id)
        if t:
            trainer = {"first_name": t.first_name, "username": t.username}

    return {
        "id": user.id,
        "first_name": user.first_name,
        "username": user.username,
        "role": user.role,
        "bot_username": BOT_USERNAME,
        "trainer": trainer,
        "profile": {
            "kcal": profile.kcal,
            "protein": profile.protein,
            "fat": profile.fat,
            "carbs": profile.carbs,
            "goal": profile.goal,
            "restrictions": profile.restrictions,
        } if profile else None,
    }


async def _calc_streak(user_id: int, db: AsyncSession) -> int:
    """Стрик: сколько дней подряд (включая сегодня/вчера) клиент вёл дневник."""
    rows = (await db.execute(
        select(MealLog.log_date)
        .where(MealLog.client_id == user_id)
        .order_by(MealLog.log_date.desc())
        .limit(60)
    )).scalars().all()
    dates = sorted(set(rows), reverse=True)
    if not dates:
        return 0
    today = date.today()
    # стрик не сгорает, пока день не закончился: отсчёт от сегодня или вчера
    if dates[0] not in (today, today - timedelta(days=1)):
        return 0
    streak = 1
    for prev, cur in zip(dates, dates[1:]):
        if (prev - cur).days == 1:
            streak += 1
        else:
            break
    return streak


@router.get("/plan/today")
async def get_today_plan(
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    plan = (await db.execute(
        select(MealPlan).where(
            and_(MealPlan.client_id == user.id,
                 MealPlan.plan_date == today,
                 MealPlan.status.in_(("sent", "acknowledged")))
        )
    )).scalar_one_or_none()

    streak = await _calc_streak(user.id, db)

    # даты с планами от сегодня и дальше — клиент может заказать еду заранее
    upcoming = (await db.execute(
        select(MealPlan.plan_date)
        .where(and_(MealPlan.client_id == user.id,
                    MealPlan.plan_date >= today,
                    MealPlan.status.in_(("sent", "acknowledged"))))
        .order_by(MealPlan.plan_date.asc())
    )).scalars().all()
    upcoming_iso = [d.isoformat() for d in upcoming]

    if not plan:
        return {"plan": None, "streak": streak, "upcoming": upcoming_iso}

    log = (await db.execute(
        select(MealLog).where(
            and_(MealLog.client_id == user.id,
                 MealLog.plan_id == plan.id)
        )
    )).scalar_one_or_none()

    return {
        "plan": {
            "id": plan.id,
            "plan_date": plan.plan_date.isoformat(),
            "items": plan.items,
            "notes": plan.notes,
            "cart_url": plan.cart_url,
        },
        "logged": log.ordered_items if log else None,
        "streak": streak,
        "upcoming": upcoming_iso,
    }


@router.get("/plan/{plan_date}")
async def get_plan_by_date(
    plan_date: str,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    """План на конкретную дату (для просмотра и заказа будущих дней)."""
    try:
        target = date.fromisoformat(plan_date)
    except ValueError:
        raise HTTPException(400, "Дата в формате YYYY-MM-DD")

    plan = (await db.execute(
        select(MealPlan).where(
            and_(MealPlan.client_id == user.id,
                 MealPlan.plan_date == target,
                 MealPlan.status.in_(("sent", "acknowledged")))
        )
    )).scalar_one_or_none()
    if not plan:
        return {"plan": None}

    log = (await db.execute(
        select(MealLog).where(
            and_(MealLog.client_id == user.id,
                 MealLog.plan_id == plan.id)
        )
    )).scalar_one_or_none()

    return {
        "plan": {
            "id": plan.id,
            "plan_date": plan.plan_date.isoformat(),
            "items": plan.items,
            "notes": plan.notes,
            "cart_url": plan.cart_url,
        },
        "logged": log.ordered_items if log else None,
    }


@router.get("/plans")
async def get_plans(
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    plans = (await db.execute(
        select(MealPlan)
        .where(and_(MealPlan.client_id == user.id,
                    MealPlan.status.in_(("sent", "acknowledged"))))
        .order_by(MealPlan.plan_date.desc())
        .limit(30)
    )).scalars().all()

    return [
        {
            "id": p.id,
            "plan_date": p.plan_date.isoformat(),
            "status": p.status,
            "items": p.items,
            "cart_url": p.cart_url,
        }
        for p in plans
    ]


@router.post("/log")
async def log_order(
    body: LogBody,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(MealPlan, body.plan_id)
    if not plan or plan.client_id != user.id:
        raise HTTPException(404, "План не найден")

    existing = (await db.execute(
        select(MealLog).where(
            and_(MealLog.client_id == user.id,
                 MealLog.plan_id == body.plan_id)
        )
    )).scalar_one_or_none()

    if existing:
        existing.ordered_items = body.ordered_items
    else:
        db.add(MealLog(
            client_id=user.id,
            plan_id=body.plan_id,
            log_date=plan.plan_date,
            ordered_items=body.ordered_items,
        ))

    plan.status = "acknowledged"
    await db.commit()
    return {"ok": True}


class WeightBody(BaseModel):
    weight_kg: float


@router.post("/weight")
async def log_weight(
    body: WeightBody,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    if not (20 <= body.weight_kg <= 400):
        raise HTTPException(400, "Введите реальный вес в килограммах")
    today = date.today()
    existing = (await db.execute(
        select(WeightLog).where(
            and_(WeightLog.client_id == user.id, WeightLog.log_date == today)
        )
    )).scalar_one_or_none()
    if existing:
        existing.weight_kg = body.weight_kg
    else:
        db.add(WeightLog(client_id=user.id, log_date=today, weight_kg=body.weight_kg))

    # актуальный вес — в профиль, чтобы тренер пересчитывал КБЖУ по нему
    profile = (await db.execute(
        select(ClientProfile).where(ClientProfile.client_id == user.id)
    )).scalar_one_or_none()
    if not profile:
        profile = ClientProfile(client_id=user.id)
        db.add(profile)
    profile.weight_kg = body.weight_kg

    await db.commit()
    return {"ok": True}


@router.get("/weight")
async def weight_history(
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    logs = (await db.execute(
        select(WeightLog)
        .where(WeightLog.client_id == user.id)
        .order_by(WeightLog.log_date.desc())
        .limit(30)
    )).scalars().all()
    return [{"log_date": w.log_date.isoformat(), "weight_kg": w.weight_kg} for w in logs]


@router.post("/plans/{plan_id}/cart")
async def get_or_create_cart(
    plan_id: int,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(MealPlan, plan_id)
    if not plan or plan.client_id != user.id:
        raise HTTPException(404, "План не найден")

    if plan.cart_url:
        return {"cart_url": plan.cart_url}

    xml_ids, seen = [], set()
    for meal in plan.items:
        for dish in meal.get("dishes", []):
            xid = dish.get("xml_id")
            if xid and dish.get("in_cart", True) and str(xid) not in seen:
                seen.add(str(xid))
                xml_ids.append(xid)
    cart_url = await create_cart(xml_ids) if xml_ids else None
    if cart_url:
        plan.cart_url = cart_url
        await db.commit()

    if not cart_url:
        raise HTTPException(500, "Не удалось создать корзину")
    return {"cart_url": cart_url}
