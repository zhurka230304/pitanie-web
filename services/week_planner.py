"""Недельный планировщик питания.

Принцип экономии ресурсов: ассортимент ВкусВилла за неделю не меняется,
поэтому пул кандидатов собирается ОДИН раз на каждый тип приёма пищи
(те же запросы, что и для дневного плана), GPT вызывается один раз на тип
(отбраковка некачественных позиций без дедупликации), а 7 дней строятся
локальным комбинатором — без дополнительных запросов к API.

Правило «без остатков»: блюда кладутся в план целыми упаковками.
Если упаковка слишком велика для приёма, разрешается порция 50% —
вторая половина автоматически ставится в тот же приём следующего дня
(in_cart=False: упаковка уже куплена и в корзину повторно не попадает).
"""
import asyncio
import random
import re
from datetime import date, timedelta

from services.gpt import (
    sdk, find_best_combinations, _calc_portion_ratio, _item_category,
    PLATE_CARB_MARKERS, PLATE_VEG_MARKERS,
)
from services.food_groups import dish_groups, make_coverage_adjust, scaled_norms
from services.vkusvill import (
    fetch_enriched_items, format_item_dict, MEAL_PLANS, MEAL_TYPE_QUERIES,
)

_LABEL_TO_TYPE = {
    "Завтрак": "breakfast",
    "Обед": "lunch",
    "Обед / ужин": "lunch",
    "Ужин": "dinner",
    "Перекус": "snack",
    "Перекус 1": "snack",
    "Перекус 2": "snack",
}

WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]  # date.weekday(): 0 = пн

POOL_QUERIES_PER_TYPE = 10   # поисковых запросов на тип приёма (на всю неделю)
POOL_MAX_CANDIDATES = 28     # кандидатов на обогащение КБЖУ
NO_REPEAT_DAYS = 2           # блюдо не повторяется ближайшие N дней
KCAL_CAP_RATIO = 1.25        # жёсткий потолок: приём не больше цели по ккал +25%
COVERAGE_MIN_DAYS = 3        # слой пищевых групп включается от этого горизонта

# Гарантия пула: без этих запросов бонусам за дефицитные группы нечего ловить
COVERAGE_QUERIES = {
    "breakfast": ["творог с ягодами", "овсянка с ягодами", "каша с фруктами",
                  "каша гречневая"],
    "lunch": ["рыба с гарниром", "треска запечённая", "чечевица", "фасоль с овощами",
              "фалафель", "овощное рагу", "гречка с курицей", "булгур с курицей",
              "суп чечевичный", "говядина тушёная с гарниром", "котлета с гарниром"],
    "dinner": ["нут", "чечевица с овощами", "лобио", "горбуша запечённая",
               "киноа с овощами", "фалафель"],
    "snack": ["хумус", "фалафель", "йогурт с ягодами", "фруктовый салат"],
}

# Свежие добавки: овощи/зелень к обеду и ужину без овощного блюда,
# фрукт к завтраку/перекусу, пока группа «Ягоды и фрукты» в дефиците
PRODUCE_VEG_QUERIES = ["огурцы", "томаты черри", "салат листовой", "зелень", "морковь"]
PRODUCE_FRUIT_QUERIES = ["бананы", "яблоки", "груши", "мандарины", "голубика"]
ADDON_KCAL_CAP = 1.2  # добавка не должна раздуть приём выше цели +20%

# Белковые добавки: готовая еда белком бедна, при спортивных целях
# (120-150г/день) приём добивается лёгким высокобелковым продуктом
PROTEIN_ADDON_QUERIES = [
    "творог 5%", "творог мягкий", "йогурт высокобелковый",
    "яйца варёные", "омлет белковый",
]
PROTEIN_ADDON_MIN_P100 = 9     # белка на 100г, меньше — не белковая добавка
PROTEIN_ADDON_MAX_K100 = 180   # ккал на 100г, больше — не «лёгкий» продукт

# Гарнир к обеду/ужину: мясо/рыба без углеводного гарнира — неполный приём
GARNISH_QUERIES = ["гречка отварная", "рис бурый", "булгур", "киноа отварная",
                   "картофель отварной", "овощи на пару"]
# Углеводный гарнир и овощи в тарелке — общие маркеры со скорингом (gpt.py)
CARB_MARKERS = PLATE_CARB_MARKERS
# чистый белок без гарнира — нуждается в гарнире
PROTEIN_MAIN_MARKERS = (
    "фрикадел", "котлет", "тефтел", "биточ", "грудк", "филе",
    "стейк", "бефстроганов", "гуляш", "рыба", "лосось", "форель",
    "треск", "индейк", "курин", "говядин", "шницел",
)


def _meal_has_carb(dishes: list) -> bool:
    text = " ".join((x.get("name") or "").lower() for x in dishes)
    return any(m in text for m in CARB_MARKERS)


def _meal_has_veg(dishes: list) -> bool:
    text = " ".join((x.get("name") or "").lower() for x in dishes)
    return any(m in text for m in PLATE_VEG_MARKERS)


def _meal_has_protein_main(dishes: list) -> bool:
    return any(
        any(m in (x.get("name") or "").lower() for m in PROTEIN_MAIN_MARKERS)
        for x in dishes
    )
PROTEIN_GAP_RATIO = 0.7        # добавляем, если белок приёма закрыт меньше чем на 70%
MAIN_MEAL_PROTEIN_MIN = 30     # цель белка на основной приём (нутрициолог: ≥30 г)


async def _gpt_quality_rank(items: list, meal_type: str) -> list:
    """Один вызов GPT на тип приёма: убрать некачественные позиции из пула.

    В отличие от run_gpt_selection НЕ дедуплицирует по категориям —
    для недели нужен широкий пул (несколько разных супов/салатов допустимы,
    они разойдутся по разным дням). При сбое возвращает исходный пул.
    """
    if len(items) <= 8:
        return items

    listing = ""
    for it in items:
        try:
            n = it["nutrition_variants"][0]
            listing += (
                f"- [{it['id']}] {it['name']} ({it['weight_g']}г, на 100г: "
                f"б{n['protein']} ж{n['fat']} у{n['carbohydrates']} к{n['calories']})\n"
            )
        except (KeyError, IndexError):
            listing += f"- [{it['id']}] {it['name']}\n"

    meal_names = {"breakfast": "завтрака", "lunch": "обеда", "dinner": "ужина", "snack": "перекуса"}

    model = sdk.models.completions("yandexgpt-lite")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: model.configure(temperature=0.2).run([
                {
                    "role": "system",
                    "text": (
                        "Ты помощник по здоровому питанию. Из списка готовых блюд исключи "
                        "неподходящие: полуфабрикаты, сырое мясо, десерты, сладкую выпечку, "
                        "снэки, соусы, продукты с маргарином/трансжирами. "
                        "Остальные верни, отсортировав от лучших к худшим по качеству. "
                        "Отвечай ТОЛЬКО строкой: ВЫБРАННЫЕ_ID: id1,id2,... — ничего больше."
                    ),
                },
                {
                    "role": "user",
                    "text": (
                        f"Блюда-кандидаты для {meal_names.get(meal_type, 'приёма пищи')} "
                        f"(план на неделю):\n{listing}\n"
                        f"Верни ID всех подходящих блюд строкой ВЫБРАННЫЕ_ID: ..."
                    ),
                },
            ]),
        )
        answer = result.alternatives[0].text.strip()
        m = re.search(r"ВЫБРАННЫЕ_ID:\s*([\d,\s]+)", answer)
        if not m:
            return items
        ids, seen = [], set()
        for x in m.group(1).split(","):
            x = x.strip()
            if x.isdigit() and int(x) not in seen:
                seen.add(int(x))
                ids.append(int(x))
        by_id = {it["id"]: it for it in items}
        kept = [by_id[i] for i in ids if i in by_id]
        # страховка: если GPT отбраковал слишком много — пул важнее ранжирования
        if len(kept) < max(8, len(items) // 3):
            return items
        return kept
    except Exception as e:
        print(f"[week_planner] GPT quality rank failed ({meal_type}): {e!r}")
        return items


def _pick_combo(pool, tP, tF, tC, tK, meal_label, used_today, last_used_day, day_idx,
                score_adjust=None, used_cats=None):
    """Лучшая комбинация целых упаковок под остаток цели приёма.

    Ограничения ослабляются постепенно: сначала без повторов категорий
    за день (чтобы паста не попадала и в обед, и в ужин), потом окно
    «без повторов блюд по дням», в конце — потолок по ккал. Лучше
    компромиссный приём, чем пустой.
    """
    used_cats = used_cats or set()
    fallback = None
    for cats_strict in (True, False):
        for window in (NO_REPEAT_DAYS, 1, 0):
            candidates = [
                it for it in pool
                if str(it["xml_id"]) not in used_today
                and day_idx - last_used_day.get(str(it["xml_id"]), -99) > window
                and (not cats_strict or _item_category(it["name"]) not in used_cats)
            ]
            if len(candidates) < 3 and (window > 0 or cats_strict):
                continue
            combos = find_best_combinations(
                candidates, max(tP, 1), max(tF, 1), max(tC, 1), max(tK, 1),
                n_combos=1, max_dishes=3, meal_label=meal_label,
                kcal_cap=max(tK, 1) * KCAL_CAP_RATIO,
                score_adjust=score_adjust,
            )
            if combos:
                return combos[0]
            if fallback is None:
                combos = find_best_combinations(
                    candidates, max(tP, 1), max(tF, 1), max(tC, 1), max(tK, 1),
                    n_combos=1, max_dishes=3, meal_label=meal_label,
                    score_adjust=score_adjust,
                )
                if combos:
                    fallback = combos[0]
    return fallback or []


async def generate_week(
    P: float, F: float, C: float, K: float,
    restrictions: str | None,
    meal_count: int,
    start: date,
    days_count: int = 7,
) -> list:
    """Вернуть список дней: [{plan_date, meals: [{meal_label, meal_type, kbju, dishes}]}]."""
    template = MEAL_PLANS.get(meal_count, MEAL_PLANS[3])
    slot_types = [_LABEL_TO_TYPE.get(label, "lunch") for label, _, _ in template]
    unique_types = sorted(set(slot_types))

    async def build_pool(mt: str):
        qpool = MEAL_TYPE_QUERIES[mt]
        queries = random.sample(qpool, min(POOL_QUERIES_PER_TYPE, len(qpool)))
        # покрывающие запросы (рыба, бобовые, ягоды) добавляются всегда
        if days_count >= COVERAGE_MIN_DAYS:
            for q in COVERAGE_QUERIES.get(mt, []):
                if q not in queries:
                    queries.append(q)
        enriched = await fetch_enriched_items(
            queries=queries,
            preference=restrictions or None,
            meal_type=mt,
            max_candidates=POOL_MAX_CANDIDATES,
        )
        ranked = await _gpt_quality_rank(enriched, mt)
        print(f"[week_planner] Пул {mt}: {len(ranked)} блюд")
        return mt, ranked

    async def build_produce_pool(kind: str, queries: list):
        enriched = await fetch_enriched_items(
            queries=queries,
            preference=restrictions or None,
            max_candidates=12,
            produce_kind=kind,
        )
        print(f"[week_planner] Пул produce/{kind}: {len(enriched)}")
        return f"produce_{kind}", enriched

    async def build_protein_pool():
        enriched = await fetch_enriched_items(
            queries=PROTEIN_ADDON_QUERIES,
            preference=restrictions or None,
            max_candidates=12,
        )
        filtered = []
        for it in enriched:
            try:
                nv = it["nutrition_variants"][0]
                if (float(nv["protein"]) >= PROTEIN_ADDON_MIN_P100
                        and float(nv["calories"]) <= PROTEIN_ADDON_MAX_K100):
                    filtered.append(it)
            except (KeyError, IndexError, ValueError, TypeError):
                pass
        print(f"[week_planner] Пул protein: {len(filtered)}")
        return "produce_protein", filtered

    async def build_garnish_pool():
        enriched = await fetch_enriched_items(
            queries=GARNISH_QUERIES,
            preference=restrictions or None,
            max_candidates=12,
        )
        # реальные гарниры (углевод/овощи в названии), нежирные:
        # «Каша гречневая сливочная» (ж17) — не гарнир, а жирное блюдо
        filtered = []
        for it in enriched:
            nl = it["name"].lower()
            if not any(m in nl for m in CARB_MARKERS):
                continue
            if any(w in nl for w in ("сливочн", "со сливками", "с маслом", "сырн", "жарен")):
                continue
            try:
                fat100 = float(it["nutrition_variants"][0]["fat"])
                if fat100 > 6:  # гарнир должен быть лёгким по жиру
                    continue
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            filtered.append(it)
        print(f"[week_planner] Пул garnish: {len(filtered)}")
        return "produce_garnish", filtered

    results = dict(await asyncio.gather(
        *[build_pool(mt) for mt in unique_types],
        build_produce_pool("veg", PRODUCE_VEG_QUERIES),
        build_produce_pool("fruit", PRODUCE_FRUIT_QUERIES),
        build_protein_pool(),
        build_garnish_pool(),
    ))
    produce = {
        "veg": results.pop("produce_veg", []),
        "fruit": results.pop("produce_fruit", []),
        "protein": results.pop("produce_protein", []),
        "garnish": results.pop("produce_garnish", []),
    }
    pools = results

    # Подстраховка: ужин нутритивно близок к обеду. Если пул ужина тонкий
    # (реальный ассортимент после фильтров), дополняем его блюдами обеда —
    # иначе ужин остаётся пустым или «просто творог».
    if "dinner" in pools and "lunch" in pools:
        if len(pools["dinner"]) < 12:
            seen = {it["id"] for it in pools["dinner"]}
            _soup = ("суп", "борщ", "щи", "солянк", "харчо", "уха", "похлёбк")
            for it in pools["lunch"]:
                if it["id"] in seen:
                    continue
                if any(s in it["name"].lower() for s in _soup):
                    continue  # суп не для ужина
                pools["dinner"].append(it)
                seen.add(it["id"])

    week = []
    last_used_day: dict = {}   # xml_id -> индекс дня последнего употребления
    pending: dict = {}         # (day_idx, slot_idx) -> [блюдо-перенос]

    # Покрытие пищевых групп (микронутриенты): group -> set(дней с группой).
    # Для короткого горизонта (день) слой отключён — нормы недельные.
    use_coverage = days_count >= COVERAGE_MIN_DAYS
    norms = scaled_norms(days_count)
    group_days: dict = {}

    def _register_groups(dish_name: str, day_idx: int):
        for g in dish_groups(dish_name):
            group_days.setdefault(g, set()).add(day_idx)

    for d in range(days_count):
        day_date = start + timedelta(days=d)
        used_today: set = set()
        used_cats_today: set = set()  # категории дня: паста не дважды в день

        def _register_cat(dish_name: str):
            cat = _item_category(dish_name)
            if cat:
                used_cats_today.add(cat)

        meals = []

        for si, (label, ratio, _q) in enumerate(template):
            slot_kbju = {
                "protein": round(P * ratio, 1),
                "fat": round(F * ratio, 1),
                "carbs": round(C * ratio, 1),
                "kcal": round(K * ratio),
            }
            tP, tF, tC, tK = P * ratio, F * ratio, C * ratio, K * ratio
            dishes = []

            # 1) сначала переносы — половинки, купленные ранее, «горят» по сроку
            for co in pending.pop((d, si), []):
                dishes.append(co)
                n = co.get("nutrition") or {}
                tP -= n.get("protein", 0)
                tF -= n.get("fat", 0)
                tC -= n.get("carbohydrates", 0)
                tK -= n.get("calories", 0)
                used_today.add(str(co["xml_id"]))
                last_used_day[str(co["xml_id"])] = d
                _register_groups(co.get("name", ""), d)
                _register_cat(co.get("name", ""))

            # 2) добиваем остаток цели из пула. Порция каждого блюда —
            # 0.5 или 1.0 упаковки, тем же правилом, что и в скоринге
            # комбинатора (_calc_portion_ratio), чтобы выбранная комбинация
            # и фактические порции совпадали
            if tK > K * 0.05:
                coverage_adjust = (
                    make_coverage_adjust(group_days, d, days_count, norms)
                    if use_coverage else None
                )
                combo = _pick_combo(
                    pools[slot_types[si]], tP, tF, tC, tK,
                    label, used_today, last_used_day, d,
                    score_adjust=coverage_adjust,
                    used_cats=used_cats_today,
                )
                for item in combo:
                    portion = _calc_portion_ratio(item, max(tK, 1), len(combo))
                    # куда переносим вторую половину: тот же приём завтра;
                    # в последний день — следующий приём сегодня;
                    # в последний приём последнего дня не делим вовсе
                    carry_slot = None
                    if portion == 0.5:
                        if d + 1 < days_count:
                            carry_slot = (d + 1, si)
                            carry_note = f"вторая половина упаковки (с {WEEKDAYS_RU[day_date.weekday()]})"
                        elif si + 1 < len(template) and slot_types[si + 1] != "snack":
                            # в последний день переносим в следующий приём,
                            # но не в перекус — тяжёлое блюдо там неуместно
                            carry_slot = (d, si + 1)
                            carry_note = f"вторая половина упаковки (с приёма «{label}»)"
                        else:
                            portion = 1.0
                    dish = format_item_dict(item, item["weight_g"] * portion)
                    dish["portion"] = portion
                    dish["in_cart"] = True
                    dishes.append(dish)
                    used_today.add(str(item["xml_id"]))
                    last_used_day[str(item["xml_id"])] = d
                    _register_groups(item.get("name", ""), d)
                    _register_cat(item.get("name", ""))
                    if carry_slot:
                        half = format_item_dict(item, item["weight_g"] * 0.5)
                        half["portion"] = 0.5
                        half["in_cart"] = False  # упаковка уже куплена
                        half["carryover"] = True
                        half["carryover_note"] = carry_note
                        pending.setdefault(carry_slot, []).append(half)

            # 2b) добивка калорий: если приём недобирает (>20% ниже цели) —
            # добавляем целые блюда из пула, ближайшие к недостаче (до 2).
            # Покрывает и пустой приём (тонкий пул ужина), и недобор половинок.
            def _meal_kcal():
                return sum((x.get("nutrition") or {}).get("calories", 0) for x in dishes)

            pool = pools[slot_types[si]]
            for _ in range(2):
                if _meal_kcal() >= slot_kbju["kcal"] * 0.8:
                    break
                best_fill = None
                for it in pool:
                    if str(it["xml_id"]) in used_today:
                        continue
                    if _item_category(it["name"]) in used_cats_today:
                        continue
                    try:
                        nv = it["nutrition_variants"][0]
                        full_k = float(nv["calories"]) * it["weight_g"] / 100
                    except (KeyError, IndexError, ValueError, TypeError):
                        continue
                    if _meal_kcal() + full_k > slot_kbju["kcal"] * KCAL_CAP_RATIO:
                        continue
                    err = abs((_meal_kcal() + full_k) - slot_kbju["kcal"])
                    # предпочитаем свежее: вчерашнее берём только при отсутствии выбора
                    if d - last_used_day.get(str(it["xml_id"]), -99) <= NO_REPEAT_DAYS:
                        err += 100000
                    if best_fill is None or err < best_fill[0]:
                        best_fill = (err, it)
                if not best_fill:
                    break
                it = best_fill[1]
                f_dish = format_item_dict(it, it["weight_g"])
                f_dish["portion"] = 1.0
                f_dish["in_cart"] = True
                dishes.append(f_dish)
                used_today.add(str(it["xml_id"]))
                last_used_day[str(it["xml_id"])] = d
                _register_groups(it.get("name", ""), d)
                _register_cat(it.get("name", ""))

            # 2c) гарнир (правило тарелки): обед/ужин без сложного углевода —
            # неполная тарелка, добавляем крупу. Несладкий завтрак (курица/
            # рыба) тоже, но творог/омлет с ягодами не трогаем.
            need_garnish = dishes and not _meal_has_carb(dishes) and (
                slot_types[si] in ("lunch", "dinner")
                or (slot_types[si] == "breakfast" and _meal_has_protein_main(dishes))
            )
            if need_garnish:
                meal_kcal = sum((x.get("nutrition") or {}).get("calories", 0) for x in dishes)
                g_candidates = [
                    it for it in produce.get("garnish", [])
                    if str(it["xml_id"]) not in used_today
                    and _item_category(it["name"]) not in used_cats_today
                ]
                best_g = None
                for it in g_candidates:
                    try:
                        full_k = float(it["nutrition_variants"][0]["calories"]) * it["weight_g"] / 100
                    except (KeyError, IndexError, ValueError, TypeError):
                        continue
                    # гарнир кладём целой упаковкой (без остатков)
                    if meal_kcal + full_k > slot_kbju["kcal"] * KCAL_CAP_RATIO:
                        continue
                    err = abs((meal_kcal + full_k) - slot_kbju["kcal"])
                    if d - last_used_day.get(str(it["xml_id"]), -99) <= NO_REPEAT_DAYS:
                        err += 100000
                    if best_g is None or err < best_g[0]:
                        best_g = (err, it)
                if best_g:
                    _, it = best_g
                    g_dish = format_item_dict(it, it["weight_g"])
                    g_dish["portion"] = 1.0
                    g_dish["in_cart"] = True
                    dishes.append(g_dish)
                    used_today.add(str(it["xml_id"]))
                    last_used_day[str(it["xml_id"])] = d
                    _register_groups(it.get("name", ""), d)
                    _register_cat(it.get("name", ""))

            # 3) белковая добавка (белок в приоритете): основной приём
            # целимся в ≥30 г белка (нутрициолог), перекус — мягко по цели
            if slot_types[si] in ("breakfast", "lunch", "dinner"):
                protein_floor = max(slot_kbju["protein"] * PROTEIN_GAP_RATIO, MAIN_MEAL_PROTEIN_MIN)
            else:
                protein_floor = slot_kbju["protein"] * PROTEIN_GAP_RATIO
            meal_p = sum((x.get("nutrition") or {}).get("protein", 0) for x in dishes)
            if meal_p < protein_floor:
                def _group_capped(it):
                    # добавка не должна пробивать потолки групп
                    # (омлет белковый каждый день = яйца 7/5)
                    for g in dish_groups(it.get("name", "")):
                        mn, mx = norms.get(g, (0, None))
                        covered = group_days.get(g, set())
                        if mx is not None and len(covered) >= mx and d not in covered:
                            return True
                    return False

                p_candidates = [
                    it for it in produce.get("protein", [])
                    if str(it["xml_id"]) not in used_today
                    and _item_category(it["name"]) not in used_cats_today
                    and not _group_capped(it)
                    and d - last_used_day.get(str(it["xml_id"]), -99) > 1
                ] or [
                    it for it in produce.get("protein", [])
                    if str(it["xml_id"]) not in used_today
                    and _item_category(it["name"]) not in used_cats_today
                    and not _group_capped(it)
                ]
                meal_kcal = sum((x.get("nutrition") or {}).get("calories", 0) for x in dishes)
                fitting = []
                for it in p_candidates:
                    p_dish = format_item_dict(it, it["weight_g"])
                    n = p_dish.get("nutrition") or {}
                    if meal_kcal + n.get("calories", 0) > slot_kbju["kcal"] * ADDON_KCAL_CAP:
                        continue
                    fitting.append((n.get("protein", 0), it, p_dish))
                # случайный из топ-3 по белку — иначе один и тот же продукт всю неделю
                fitting.sort(key=lambda x: -x[0])
                if fitting:
                    _, it, p_dish = random.choice(fitting[:3])
                    p_dish["portion"] = 1.0
                    p_dish["in_cart"] = True
                    dishes.append(p_dish)
                    used_today.add(str(it["xml_id"]))
                    last_used_day[str(it["xml_id"])] = d
                    _register_groups(it.get("name", ""), d)
                    _register_cat(it.get("name", ""))

            # 4) свежая добавка (правило тарелки): овощи к обеду/ужину без
            # овощей; фрукт/овощ к завтраку без растительного компонента;
            # фрукт к перекусу при дефиците группы
            mt = slot_types[si]
            addon_kind = None
            if mt in ("lunch", "dinner") and not _meal_has_veg(dishes):
                addon_kind = "veg"
            elif mt == "breakfast" and not _meal_has_veg(dishes):
                # на завтрак растительный компонент — обычно фрукт/ягоды
                addon_kind = "fruit"
            elif mt == "snack":
                mn_fruit = norms.get("Ягоды и фрукты", (0, None))[0]
                fruit_days = group_days.get("Ягоды и фрукты", set())
                if len(fruit_days) < mn_fruit and d not in fruit_days:
                    addon_kind = "fruit"

            if addon_kind:
                candidates = [
                    it for it in produce.get(addon_kind, [])
                    if str(it["xml_id"]) not in used_today
                    and d - last_used_day.get(str(it["xml_id"]), -99) > 1
                ] or [
                    it for it in produce.get(addon_kind, [])
                    if str(it["xml_id"]) not in used_today
                ]
                if candidates:
                    addon = random.choice(candidates)
                    a_dish = format_item_dict(addon, addon["weight_g"])
                    a_kcal = (a_dish.get("nutrition") or {}).get("calories", 0)
                    meal_kcal = sum((x.get("nutrition") or {}).get("calories", 0) for x in dishes)
                    # лёгкие овощи (до 60 ккал) добавляем всегда — они
                    # практически не влияют на ккал, а овощи нужны ежедневно
                    if (addon_kind == "veg" and a_kcal <= 60) or \
                       meal_kcal + a_kcal <= slot_kbju["kcal"] * ADDON_KCAL_CAP:
                        a_dish["portion"] = 1.0
                        a_dish["in_cart"] = True
                        dishes.append(a_dish)
                        used_today.add(str(addon["xml_id"]))
                        last_used_day[str(addon["xml_id"])] = d
                        _register_groups(addon.get("name", ""), d)

            meals.append({
                "meal_label": label,
                "meal_type": label.lower(),
                "kbju": slot_kbju,
                "dishes": dishes,
            })

        week.append({"plan_date": day_date.isoformat(), "meals": meals})

    return week
