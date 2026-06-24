from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import UserSeenDish, DishRating


async def get_seen_dish_ids(db: AsyncSession, user_id: int, meal_type: str) -> set:
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    result = await db.execute(
        select(UserSeenDish.dish_xml_id).where(
            UserSeenDish.user_id == user_id,
            UserSeenDish.meal_type == meal_type,
            UserSeenDish.shown_at > cutoff,
        )
    )
    return {row[0] for row in result}


async def save_seen_dishes(db: AsyncSession, user_id: int, meal_type: str, xml_ids: list) -> None:
    if not xml_ids:
        return
    stmt = pg_insert(UserSeenDish).values([
        {"user_id": user_id, "dish_xml_id": xml_id, "meal_type": meal_type}
        for xml_id in xml_ids
    ]).on_conflict_do_nothing(index_elements=["user_id", "dish_xml_id", "meal_type"])
    await db.execute(stmt)
    await db.commit()


async def reset_seen_dishes(db: AsyncSession, user_id: int, meal_type: str) -> None:
    await db.execute(
        delete(UserSeenDish).where(
            UserSeenDish.user_id == user_id,
            UserSeenDish.meal_type == meal_type,
        )
    )
    await db.commit()


async def get_disliked_dish_ids(db: AsyncSession, user_id: int, meal_type: str) -> set:
    result = await db.execute(
        select(DishRating.dish_xml_id).where(
            DishRating.user_id == user_id,
            DishRating.meal_type == meal_type,
            DishRating.rating == -1,
        )
    )
    return {row[0] for row in result}


async def get_liked_dish_ids(db: AsyncSession, user_id: int, meal_type: str) -> set:
    result = await db.execute(
        select(DishRating.dish_xml_id).where(
            DishRating.user_id == user_id,
            DishRating.meal_type == meal_type,
            DishRating.rating == 1,
        )
    )
    return {row[0] for row in result}
