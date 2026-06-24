from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import User, SearchHistory, OrderHistory
from auth import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])


class OrderRequest(BaseModel):
    search_id: int
    ordered_items: list[dict]  # [{name, url, portion_g}]
    delivery_service: str
    notes: Optional[str] = None


@router.get("/")
async def get_history(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SearchHistory)
        .where(SearchHistory.user_id == user.id)
        .options(selectinload(SearchHistory.order))
        .order_by(desc(SearchHistory.created_at))
        .limit(limit)
        .offset(offset)
    )
    searches = result.scalars().all()
    return {
        "items": [
            {
                "id": s.id,
                "created_at": s.created_at.isoformat(),
                "proteins": s.proteins,
                "fats": s.fats,
                "carbs": s.carbs,
                "calories": s.calories,
                "mode": s.mode,
                "meal_type": s.meal_type,
                "meal_count": s.meal_count,
                "delivery_service": s.delivery_service,
                "city": s.city,
                "has_order": s.order is not None,
            }
            for s in searches
        ]
    }


@router.get("/{search_id}")
async def get_search_detail(
    search_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    search = await db.get(SearchHistory, search_id)
    if not search or search.user_id != user.id:
        raise HTTPException(404, "Не найдено")
    return search.results


@router.post("/order")
async def save_order(
    data: OrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    search = await db.get(SearchHistory, data.search_id)
    if not search or search.user_id != user.id:
        raise HTTPException(404, "Поиск не найден")

    order = OrderHistory(
        user_id=user.id,
        search_id=data.search_id,
        ordered_items=data.ordered_items,
        delivery_service=data.delivery_service,
        notes=data.notes,
    )
    db.add(order)
    await db.commit()
    return {"ok": True}


@router.get("/orders/all")
async def get_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(OrderHistory)
        .where(OrderHistory.user_id == user.id)
        .order_by(desc(OrderHistory.created_at))
        .limit(50)
    )
    orders = result.scalars().all()
    return {
        "items": [
            {
                "id": o.id,
                "created_at": o.created_at.isoformat(),
                "delivery_service": o.delivery_service,
                "ordered_items": o.ordered_items,
                "notes": o.notes,
            }
            for o in orders
        ]
    }
