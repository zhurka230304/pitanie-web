"""Расчёт КБЖУ по методике ksy_profood (Харрис-Бенедикт)."""

ACTIVITY = {
    "sedentary": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725, "very_high": 1.9,
}


def bmr(sex: str, weight: float, height: float, age: int) -> float:
    if sex == "male":
        return 66.5 + 13.75 * weight + 5.003 * height - 6.755 * age
    return 655 + 9.6 * weight + 1.8 * height - 4.7 * age


def calc_kbju(sex: str, weight: float, height: float, age: int,
              activity: str = "moderate", goal: str = "loss") -> dict:
    """goal: loss (−15%), maintain, gain (+10%)."""
    base = bmr(sex, weight, height, age)
    norm = base * ACTIVITY.get(activity, 1.55)
    if goal == "loss":
        kcal = norm * 0.85
    elif goal == "gain":
        kcal = norm * 1.10
    else:
        kcal = norm
    # БЖУ: белок 1.8 г/кг, жиры 1 г/кг, углеводы — остаток
    protein = round(weight * 1.8)
    fat = round(weight * 1.0)
    carbs = round((kcal - protein * 4 - fat * 9) / 4)
    return {
        "kcal": round(kcal),
        "protein": protein,
        "fat": fat,
        "carbs": max(0, carbs),
        "bmr": round(base),
        "maintenance": round(norm),
    }
