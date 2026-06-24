"""Пищевые группы и недельные нормы покрытия микронутриентов.

VkusVill отдаёт только КБЖУ, поэтому микронутриенты закрываются косвенно —
через разнообразие пищевых групп за неделю (как это делают нутрициологи:
рыба 2 раза в неделю ≈ омега-3 и витамин D, бобовые ≈ железо и фолаты).

Считаем не граммы, а ДНИ недели, в которые группа присутствует в плане.
Переносы-половинок считаются честно: овощное блюдо, доеденное во вторник,
закрывает овощи вторника.
"""
import math
import re

# Группа -> маркеры в названии блюда (lowercase substring)
FOOD_GROUPS = {
    "Рыба и морепродукты": (
        "лосос", "форел", "треск", "тунец", "скумбри", "горбуш",
        "судак", "минтай", "сельд", "рыб", "креветк", "кальмар",
        "мидии", "морепродукт",
    ),
    "Овощи": (
        "овощ", "салат", "брокколи", "шпинат", "томат", "перец",
        "кабачок", "баклажан", "капуст", "морков", "свекл", "огурц",
        "зелень", "рагу", "винегрет", "тыкв", "борщ", "щи",
    ),
    "Кисломолочное": (
        "творог", "творожн", "йогурт", "кефир", "ряженк", "сырник",
    ),
    "Цельнозерновые": (
        "гречк", "гречн", "овсян", "булгур", "киноа", "перлов", "цельнозерн",
        "бурый рис", "дикий рис", "отруб", "полб", "геркулес", "пшённ", "пшенн",
        "ячнев",
    ),
    "Бобовые": (
        "чечевиц", "нут", "фасол", "хумус", "горох", "фалафел", "бобов",
        "лобио", "эдамаме", "тофу",
    ),
    "Красное мясо": (
        "говядин", "телятин", "бефстроганов", "гуляш", "азу",
    ),
    "Яйца": ("яйц", "омлет", "яичн"),
    "Ягоды и фрукты": (
        "ягод", "черник", "клубник", "малин", "яблок", "груш",
        "банан", "персик", "фрукт", "абрикос", "вишн",
    ),
}

# Норма за 7-дневную неделю: (минимум дней, максимум дней | None)
WEEKLY_NORMS = {
    "Рыба и морепродукты": (2, None),
    "Овощи": (7, None),
    "Кисломолочное": (4, None),
    "Цельнозерновые": (4, None),
    "Бобовые": (2, None),
    "Красное мясо": (1, 3),
    "Яйца": (2, 5),
    "Ягоды и фрукты": (3, None),
}

# Веса подобраны с учётом джиттера скоринга (uniform 0..0.5): бонус за
# дефицит должен перевешивать джиттер уже в начале недели, иначе слой
# не работает (наблюдалось: рыба 0/2, красное мясо 6/3 на реальном пуле)
DEFICIT_BONUS = 1.2    # макс. бонус за закрытие дефицитной группы
DEFICIT_FLOOR = 0.4    # доля бонуса, действующая с первого дня
EXCESS_PENALTY = 1.5   # штраф за группу, упёршуюся в потолок (мясо 4-й день)


def _marker_hit(marker: str, name_lower: str) -> bool:
    # Короткие маркеры («нут», «щи») ловят ложные срабатывания внутри слов
    # («минуты», «овощи») — для них требуем начало слова
    if len(marker) <= 3:
        return re.search(r"\b" + re.escape(marker), name_lower) is not None
    return marker in name_lower


def dish_groups(name: str) -> set:
    """Пищевые группы, которые закрывает блюдо (по названию)."""
    n = (name or "").lower()
    return {g for g, markers in FOOD_GROUPS.items() if any(_marker_hit(m, n) for m in markers)}


def combo_groups(combo: list) -> set:
    groups: set = set()
    for item in combo:
        groups |= dish_groups(item.get("name", ""))
    return groups


def scaled_norms(days_count: int) -> dict:
    """Нормы, пересчитанные на горизонт плана (для недели — как есть)."""
    result = {}
    for g, (mn, mx) in WEEKLY_NORMS.items():
        s_mn = max(1, math.ceil(mn * days_count / 7)) if mn else 0
        s_mx = max(1, math.ceil(mx * days_count / 7)) if mx else None
        result[g] = (s_mn, s_mx)
    return result


def make_coverage_adjust(group_days: dict, day_idx: int, days_count: int, norms: dict):
    """Замыкание для find_best_combinations(score_adjust=...).

    Мягкий слой поверх КБЖУ-скоринга: комбинации, закрывающие дефицитные
    группы, получают бонус (давление растёт к концу недели — знаменатель
    «оставшиеся дни» тает); группы на потолке — штраф.
    """
    days_left = max(days_count - day_idx, 1)

    def adjust(combo: list) -> float:
        adj = 0.0
        for g in combo_groups(combo):
            mn, mx = norms.get(g, (0, None))
            covered_days = group_days.get(g, set())
            if day_idx in covered_days:
                continue  # сегодня группа уже закрыта — ни бонуса, ни штрафа
            deficit = mn - len(covered_days)
            if deficit > 0:
                pressure = min(deficit / days_left, 1.0)
                adj -= DEFICIT_BONUS * (DEFICIT_FLOOR + (1 - DEFICIT_FLOOR) * pressure)
            if mx is not None and len(covered_days) >= mx:
                adj += EXCESS_PENALTY
        return adj

    return adjust


def coverage_report(week_days: list) -> list:
    """Отчёт «группа -> дней из нормы» по сгенерированной неделе."""
    n = len(week_days)
    norms = scaled_norms(n)
    group_days: dict = {g: set() for g in WEEKLY_NORMS}
    for d, day in enumerate(week_days):
        for meal in day.get("meals", []):
            for dish in meal.get("dishes", []):
                for g in dish_groups(dish.get("name", "")):
                    if g in group_days:
                        group_days[g].add(d)
    report = []
    for g, (mn, mx) in norms.items():
        days = len(group_days[g])
        ok = days >= mn and (mx is None or days <= mx)
        report.append({"group": g, "days": days, "min": mn, "max": mx, "ok": ok})
    return report
