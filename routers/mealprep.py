from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from services.mealprep.planner import plan_week

router = APIRouter(prefix="/api/mealprep", tags=["mealprep"])


class PlanBody(BaseModel):
    kcal: float = 1950
    protein: float = 153
    fat: float = 68
    carbs: float = 160
    days: int = 7
    sessions: int = 2
    breakfast_max_time: int = 20


@router.post("/plan")
async def make_plan(body: PlanBody):
    target = {"kcal": body.kcal, "protein": body.protein, "fat": body.fat, "carbs": body.carbs}
    return plan_week(
        target,
        days=max(1, min(7, body.days)),
        sessions=max(1, min(3, body.sessions)),
        breakfast_max_time=body.breakfast_max_time,
    )
