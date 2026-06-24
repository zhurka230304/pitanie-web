"""Мил-преп планировщик на рецептах.

Все приёмы (завтрак/обед/ужин) — рецепты из гайдов с фото, масштабируются
под КБЖУ. Завтрак — быстрый, без духовки. Обед/ужин — batch-friendly
(хранятся, готовятся впрок), готовка в N варочных сессий в неделю.
Закупка — суммарные ингредиенты (сырые продукты) на неделю.
"""
import json, os, itertools

_DIR = os.path.dirname(__file__)
_RECIPES = json.load(open(os.path.join(_DIR, "recipes.json")))["recipes"]

# вес единиц для пересчёта в граммы (список покупок)
_UNIT_G = {"г": 1, "гр": 1, "мл": 1, "зуб": 4, "ст.л.": 15, "ст. л.": 15,
           "ч.л.": 6, "ч. л.": 6, "саше": 1, "щепотка": 0.5, "горсть": 25,
           "веточка": 2, "долька": 15}


def _grams(ing):
    a = ing.get("amount"); u = (ing.get("unit") or "").lower(); nm = ing["name"].lower()
    if not isinstance(a, (int, float)):
        return 0
    if u in _UNIT_G:
        return a * _UNIT_G[u]
    if u == "шт":
        if "яйц" in nm: return a * 55
        if "лаваш" in nm or "тортиль" in nm: return a * 80
        if "банан" in nm: return a * 120
        if "яблок" in nm: return a * 180
        return a * 60
    return 0


def _scale(recipe, target_kcal, lo=0.6, hi=1.8):
    base = recipe["nutrition"]["kcal"] or target_kcal
    f = max(lo, min(hi, round(target_kcal / base, 2))) if base else 1.0
    nutr = {x: round((recipe["nutrition"].get(x) or 0) * f, 1) for x in ("kcal", "protein", "fat", "carbs")}
    ings = [{"name": i["name"],
             "amount": (round(i["amount"] * f, 1) if isinstance(i.get("amount"), (int, float)) else i.get("amount")),
             "unit": i["unit"]} for i in recipe["ingredients"]]
    return {"id": recipe["id"], "title": recipe["title"], "factor": f, "time_min": recipe.get("time_min"),
            "image": recipe.get("image"), "nutrition": nutr, "ingredients": ings,
            "steps": recipe.get("steps", []), "source": recipe.get("source"),
            "estimated": recipe.get("nutrition_estimated", False)}


def _pool(meal_types, only_quick=False, breakfast_max_time=99, batch=False):
    res = []
    for r in _RECIPES:
        if r["meal_type"] not in meal_types:
            continue
        if not (r.get("nutrition") and r["nutrition"].get("kcal")):
            continue
        if only_quick and not (r.get("no_bake") and r.get("time_min", 99) <= breakfast_max_time):
            continue
        if batch and not r.get("batch_friendly"):
            continue
        res.append(r)
    # приоритет рецептам с фото, затем по времени
    res.sort(key=lambda r: (0 if r.get("image") else 1, r.get("time_min") or 99))
    return res


def plan_week(target, days=7, sessions=2, breakfast_max_time=20,
              meal_split=(0.55, 0.45), restrictions=None):
    brk = _pool({"завтрак"}, only_quick=True, breakfast_max_time=breakfast_max_time)
    mains = _pool({"обед", "ужин"}, batch=True)
    if len(mains) < 4:  # мало batch+фото — расширяем любыми обед/ужин рецептами
        mains = _pool({"обед", "ужин"})
    if not brk: brk = _pool({"завтрак"})

    # фото-рецепты впереди; ужин берёт тот же пул со сдвигом, чтобы тоже с фото
    photo = [m for m in mains if m.get("image")]
    rest = [m for m in mains if not m.get("image")]
    mains = (photo + rest) or mains
    bc = itertools.cycle(brk)
    lc = itertools.cycle(mains)
    dc = itertools.cycle(mains[1:] + mains[:1] if len(mains) > 1 else mains)

    week, shopping = [], {}
    def add_shop(ings):
        for i in ings:
            g = _grams(i)
            if g > 0:
                shopping[i["name"]] = shopping.get(i["name"], 0) + g

    for d in range(days):
        rem = dict(target)
        meals = []
        for label, share, cyc in (("Обед", meal_split[0], lc), ("Ужин", meal_split[1], dc)):
            t = target["kcal"] * (1 - 0.27) * share
            m = _scale(next(cyc), t)
            m["label"] = label
            meals.append(m)
            add_shop(m["ingredients"])
        used_k = sum(m["nutrition"]["kcal"] for m in meals)
        b = _scale(next(bc), max(250, target["kcal"] - used_k))
        b["label"] = "Завтрак"
        add_shop(b["ingredients"])
        day_tot = {k: round(b["nutrition"][k] + sum(m["nutrition"][k] for m in meals)) for k in target}
        week.append({"breakfast": b, "meals": meals, "day_total": day_tot})

    block = -(-days // sessions)
    sess = [{"session": s + 1, "days": list(range(s * block + 1, min((s + 1) * block, days) + 1))}
            for s in range(sessions)]
    return {"target": target, "days": week, "cook_sessions": sess,
            "shopping_raw_g": {k: round(v) for k, v in sorted(shopping.items(), key=lambda x: -x[1])}}


if __name__ == "__main__":
    res = plan_week({"kcal": 1950, "protein": 153, "fat": 68, "carbs": 160})
    for d, day in enumerate(res["days"], 1):
        t = day["day_total"]
        print(f"Д{d} {t['kcal']}/{t['protein']}/{t['fat']}/{t['carbs']}")
        print(f"   завтрак: {day['breakfast']['title']} {'📷' if day['breakfast']['image'] else ''}")
        for m in day["meals"]:
            print(f"   {m['label']}: {m['title']} {'📷' if m['image'] else ''}")
