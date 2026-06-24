from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel

from database import get_db
from models import User, DishRating, ComboRating, DayRating
from auth import get_current_user, get_optional_user

router = APIRouter(prefix="/api/ratings", tags=["ratings"])


class DishRatingRequest(BaseModel):
    dish_xml_id: str
    dish_name: str
    meal_type: str
    rating: int  # 1, -1, or 0 (cancel)


class ComboRatingRequest(BaseModel):
    search_id: int
    meal_type: str
    combo_index: int
    dish_xml_ids: list
    rating: int


class DayRatingRequest(BaseModel):
    search_id: int
    rating: int


@router.post("/dish")
async def rate_dish(
    data: DishRatingRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if data.rating == 0:
        await db.execute(
            delete(DishRating).where(
                DishRating.user_id == user.id,
                DishRating.dish_xml_id == data.dish_xml_id,
                DishRating.meal_type == data.meal_type,
            )
        )
        await db.commit()
        return {"ok": True, "action": "deleted"}

    if data.rating not in (1, -1):
        raise HTTPException(400, "rating must be 1, -1, or 0")

    stmt = pg_insert(DishRating).values(
        user_id=user.id,
        dish_xml_id=data.dish_xml_id,
        dish_name=data.dish_name,
        meal_type=data.meal_type,
        rating=data.rating,
    ).on_conflict_do_update(
        index_elements=["user_id", "dish_xml_id", "meal_type"],
        set_={"rating": data.rating, "dish_name": data.dish_name},
    )
    await db.execute(stmt)
    await db.commit()
    return {"ok": True, "action": "saved"}


@router.post("/combo")
async def rate_combo(
    data: ComboRatingRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    db.add(ComboRating(
        user_id=user.id,
        search_id=data.search_id,
        meal_type=data.meal_type,
        combo_index=data.combo_index,
        dish_xml_ids=data.dish_xml_ids,
        rating=data.rating,
    ))
    await db.commit()
    return {"ok": True}


@router.post("/day")
async def rate_day(
    data: DayRatingRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    db.add(DayRating(
        user_id=user.id,
        search_id=data.search_id,
        rating=data.rating,
    ))
    await db.commit()
    return {"ok": True}


@router.get("/dishes")
async def get_dish_ratings(
    meal_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(DishRating).where(DishRating.user_id == user.id)
    if meal_type:
        q = q.where(DishRating.meal_type == meal_type)
    result = await db.execute(q)
    rows = result.scalars().all()
    liked = [{"xml_id": r.dish_xml_id, "name": r.dish_name, "meal_type": r.meal_type} for r in rows if r.rating == 1]
    disliked = [{"xml_id": r.dish_xml_id, "name": r.dish_name, "meal_type": r.meal_type} for r in rows if r.rating == -1]
    return {"liked": liked, "disliked": disliked}
