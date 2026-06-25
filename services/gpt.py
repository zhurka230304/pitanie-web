"""YandexGPT selection service — adapted from bot.py"""
import asyncio
import re
import os
import random
from itertools import combinations as iter_combinations
from yandex_cloud_ml_sdk import YCloudML

from services.vkusvill import deduplicate_by_category, calc_portion, CATEGORY_ROOTS, CATEGORY_ALIASES

sdk = YCloudML(
    folder_id=os.getenv("YANDEX_FOLDER_ID", ""),
    auth=os.getenv("YANDEX_API_KEY", ""),
)

# Источники клетчатки — нужны в каждом основном приёме (скоринг комбинаций)
FIBER_MARKERS = (
    "овощ", "зелень", "шпинат", "брокколи", "огурец", "томат", "перец",
    "ягод", "черник", "клубник", "малин",
    "гречк", "овсян", "цельнозерн", "отруб",
    "фасол", "чечевиц", "нут", "бобов",
    "авокадо", "груш", "яблок",
)

# Правило тарелки: основной приём = белок + сложный углевод + овощи/фрукт
PLATE_CARB_MARKERS = (
    "рис", "гречк", "булгур", "киноа", "картоф", "пюре", "паст",
    "макарон", "перлов", "кускус", "плов", "лапш", "пшён", "пшен",
    "овсян", "хлеб", "лаваш", "тортиль", "гарнир", "спагетти",
    "оладь", "блин", "сырник", "каша",
)
PLATE_VEG_MARKERS = (
    "овощ", "салат", "брокколи", "шпинат", "томат", "помидор", "огурец",
    "перец", "кабачок", "цукини", "баклажан", "капуст", "морков", "свекл",
    "зелень", "руккол", "стручков", "фасоль стручк", "рагу", "грибами",
    "ягод", "фрукт", "яблок", "груш", "банан", "черник", "малин",
)

# Качество углеводов и жиров (нутрициолог): бонус сложным/полезным,
# штраф простым/рафинированным
WHOLE_GRAIN_MARKERS = (
    "бур", "дик", "гречк", "гречн", "овсян", "геркулес", "цельнозерн",
    "перлов", "булгур", "киноа", "кускус", "чечевиц", "фасол", "нут",
    "батат", "полб", "ячнев",
)
REFINED_CARB_MARKERS = (
    "белый хлеб", "батон", "булочк", "багет", "белый рис", "рис басмати",
    "лаваш пшеничн", "сахар", "сироп", "сгущ",
)
HEALTHY_FAT_MARKERS = (
    "лосос", "форел", "скумбри", "сельд", "тунец", "горбуш", "авокадо",
    "оливков", "орех", "миндал", "семен", "кунжут", "семг",
)

SINGLE_DISH_KEYWORDS = [
    "суп", "паста", "каша", "рис", "гречка", "омлет",
    "салат", "пицца", "блины", "сырники", "котлет",
    "курица", "рыба", "говядина", "индейка", "тефтел",
]


def is_single_dish_request(preference: str) -> bool:
    return any(kw in preference.lower().strip() for kw in SINGLE_DISH_KEYWORDS)


async def run_gpt_selection(
    enriched_items: list,
    P: float, F: float, C: float, K: float,
    preference: str | None,
    count: int = 5,
    meal_label: str = "",
) -> list:
    if not enriched_items:
        return []

    gpt_text = ""
    for item in enriched_items:
        try:
            nv = item["nutrition_variants"]
            wg = item["weight_g"]
            cal_per_100 = float(nv[0]["calories"])
            cal_total = round(cal_per_100 * wg / 100, 1)
            needed_g = min(round((K / cal_total) * wg), wg) if cal_total > 0 else wg
            n = nv[0]
            k = needed_g / 100
            gpt_text += (
                f"- [{item['id']}] {item['name']}\n"
                f"  {needed_g}г | б{round(float(n['protein'])*k,1)} "
                f"ж{round(float(n['fat'])*k,1)} "
                f"у{round(float(n['carbohydrates'])*k,1)} "
                f"к{round(float(n['calories'])*k,1)}\n"
            )
        except (ValueError, TypeError):
            gpt_text += f"- [{item['id']}] {item['name']}\n"

    preference_hint = f"\nПредпочтение: «{preference}»." if preference else ""
    single_hint = ""
    if preference and is_single_dish_request(preference):
        single_hint = f" ВАЖНО: выбирай ТОЛЬКО блюда с «{preference}» в названии."

    model = sdk.models.completions("yandexgpt-lite")
    loop = asyncio.get_running_loop()
    gpt_result = await loop.run_in_executor(
        None,
        lambda: model.configure(temperature=0.3).run([
            {
                "role": "system",
                "text": (
                    "Ты помощник по здоровому питанию. Выбери блюда из списка максимально близко к целевым КБЖУ. "
                    "Отвечай ТОЛЬКО строкой: ВЫБРАННЫЕ_ID: id1,id2,id3 — ничего больше. "
                    "Выбирай разнообразные ГОТОВЫЕ блюда из натуральных ингредиентов. "
                    "Предпочитай: свежее приготовленное мясо/рыбу/птицу, овощные блюда, каши, творог. "
                    "Следи чтобы сумма КБЖУ всех выбранных блюд попадала в целевой коридор ±15%. "
                    "Не более одного: омлета, салата, супа, каши. "
                    "Творожные блюда — это одна категория: творог + сырники + запеканка творожная вместе не более одного блюда. "
                    "Не выбирай: сырое мясо, полуфабрикаты, замороженные, консервы, сыр куском, нарезки, соусы, "
                    "продукты с маргарином/трансжирами/гидрогенизированными жирами, "
                    "сладкую выпечку, пирожные, конфеты, снэки, чипсы. "
                    + (
                        (
                            f"Это ЗАВТРАК. Подбирай комбинацию готовых блюд точно под нутритивные цели.\n"
                            f"ЦЕЛЬ (сумма всех блюд): белок {P}г, жиры {F}г, углеводы {C}г, {K} ккал. Допуск ±15%.\n"
                            f"ВАЖНО: суммарные жиры комбинации должны быть {round(F*0.85,0):.0f}–{round(F*1.15,0):.0f}г. "
                            f"Не выбирай блюда с высоким содержанием жира если цель по жирам мала.\n"
                            "СТРУКТУРА: минимум одно блюдо класса А — цельная основа "
                            "(яйца/омлет, творог, каша, птица, рыба, бобовые). "
                            "Мучные блюда (блины, вафли, выпечка) — только как дополнение, не единственное. "
                            "НЕ выбирай: пасту, борщ, тяжёлое мясо, бефстроганов, гуляш. "
                            "Если идеала нет — выбери ближайшее к цели."
                        )
                        if meal_label in ("breakfast", "Завтрак") else
                        "Это ПЕРЕКУС. Выбирай лёгкое: творог, йогурт, сырники, хумус, лёгкий салат. "
                        "НЕ выбирай: супы, пасту, тяжёлые мясные блюда. "
                        if meal_label in ("snack", "Перекус", "Перекус 1", "Перекус 2") else
                        f"Это приём пищи: {meal_label}. Учитывай контекст. "
                        if meal_label else ""
                    )
                ),
            },
            {
                "role": "user",
                "text": (
                    f"КБЖУ: белки {P}г, жиры {F}г, углеводы {C}г, {K} ккал"
                    f"{preference_hint}{single_hint}\n\n"
                    f"Товары:\n{gpt_text}\n\n"
                    f"Выбери {count} подходящих блюд. "
                    f"Ответь ТОЛЬКО строкой ВЫБРАННЫЕ_ID: id1,id2,...,idN"
                ),
            },
        ]),
    )

    answer = gpt_result.alternatives[0].text.strip()
    ids_match = re.search(r"ВЫБРАННЫЕ_ID:\s*([\d,\s]+)", answer)
    selected_ids = []
    if ids_match:
        selected_ids = [int(i.strip()) for i in ids_match.group(1).split(",") if i.strip().isdigit()]

    if not selected_ids:
        selected_ids = [item["id"] for item in enriched_items[:count]]

    selected = [item for item in enriched_items if item["id"] in selected_ids][:count]
    print(f"[gpt] До deduplicate: {len(selected)} блюд")
    selected = deduplicate_by_category(selected)
    print(f"[gpt] После deduplicate: {len(selected)} блюд")

    if len(selected) < count:
        selected_ids_set = {item["id"] for item in selected}
        for item in enriched_items:
            if item["id"] not in selected_ids_set:
                selected.append(item)
                selected_ids_set.add(item["id"])
            if len(selected) >= count + 3:
                break
        selected = deduplicate_by_category(selected)[:count]

    return selected


def _item_full_kbju(item: dict) -> tuple[float, float, float, float]:
    """Return (protein, fat, carbs, calories) for the full portion of an item."""
    try:
        nv = item["nutrition_variants"][0]
        wg = item["weight_g"] / 100
        return (
            float(nv["protein"]) * wg,
            float(nv["fat"]) * wg,
            float(nv["carbohydrates"]) * wg,
            float(nv["calories"]) * wg,
        )
    except (KeyError, IndexError, ValueError, TypeError):
        return (0.0, 0.0, 0.0, 0.0)


def _calc_portion_ratio(item: dict, K_target: float, n_items: int) -> float:
    """Return 0.5 or 1.0 — half or full portion based on caloric share."""
    try:
        nv = item["nutrition_variants"][0]
        wg = item["weight_g"] / 100
        cal_total = float(nv["calories"]) * wg
        if cal_total <= 0:
            return 1.0
        k_share = K_target / max(n_items, 1)
        ratio = k_share / cal_total
        if ratio <= 0.75:
            return 0.5
        return 1.0
    except Exception:
        return 1.0


def combo_portion_kbju(combo: list, K: float) -> tuple[float, float, float, float]:
    """КБЖУ комбинации с учётом реальных порций 0.5/1.0 (см. _calc_portion_ratio)."""
    n = len(combo)
    tp = tf = tc = tk = 0.0
    for item in combo:
        ratio = _calc_portion_ratio(item, K, n)
        try:
            nv = item["nutrition_variants"][0]
            wg = item["weight_g"] / 100 * ratio
            tp += float(nv["protein"]) * wg
            tf += float(nv["fat"]) * wg
            tc += float(nv["carbohydrates"]) * wg
            tk += float(nv["calories"]) * wg
        except Exception:
            pass
    return tp, tf, tc, tk


def _score_combo(
    combo: list, P: float, F: float, C: float, K: float,
    meal_label: str = "",
    liked_ids: set | None = None,
) -> float:
    """Lower = better fit to target КБЖУ. Weighted normalized deviation."""
    tp, tf, tc, tk = combo_portion_kbju(combo, K)

    # Початая упаковка — неудобство (хранить/доедать), штраф за каждую половинку
    half_count = sum(1 for item in combo if _calc_portion_ratio(item, K, len(combo)) == 0.5)

    score = (
        2.0 * abs(tp / max(P, 1) - 1) +
        1.5 * abs(tk / max(K, 1) - 1) +
        1.0 * abs(tc / max(C, 1) - 1) +
        1.4 * abs(tf / max(F, 1) - 1) +
        0.85 * half_count
    )

    # Макронутриенты закрываются В РАМКАХ приёма: сильный перебор жира
    # и провал белка не компенсируются другими приёмами дня
    if tf > max(F, 1) * 1.2:
        score += 1.0
    if tf > max(F, 1) * 1.5:
        score += 1.5
    if tf > max(F, 1) * 1.9:
        score += 2.0
    if tp < max(P, 1) * 0.45:
        score += 1.0
    # недобор калорий приёма — тоже не «здоровое питание» (ступенчато)
    if tk < max(K, 1) * 0.85:
        score += 0.75
    if tk < max(K, 1) * 0.7:
        score += 1.5
    if tk < max(K, 1) * 0.55:
        score += 1.5

    # Клетчатка нужна в каждом приёме: штраф за комбинацию без её
    # источников, небольшой бонус за 2+ (для всех основных приёмов)
    combo_names = " ".join(item["name"].lower() for item in combo)
    fiber_hits = sum(1 for m in FIBER_MARKERS if m in combo_names)
    if fiber_hits == 0:
        score += 1.0
    elif fiber_hits >= 2:
        score -= 0.3

    # Правило тарелки для завтрака/обеда/ужина: белок + углевод + овощи.
    # Неполная тарелка (одиночное блюдо без компонентов) сильно штрафуется,
    # чтобы комбинатор собирал полноценный приём из 2–3 блюд.
    # Белок в приоритете (нутрициолог): целимся в ≥30 г на основной приём.
    if meal_label in ("breakfast", "Завтрак", "lunch", "Обед", "Обед / ужин", "dinner", "Ужин"):
        has_carb = any(m in combo_names for m in PLATE_CARB_MARKERS)
        has_veg = any(m in combo_names for m in PLATE_VEG_MARKERS)
        has_protein = tp >= 28
        missing = (not has_carb) + (not has_veg) + (not has_protein)
        score += 1.3 * missing
        if tp < 20:           # совсем мало белка для основного приёма
            score += 1.0

        # качество углеводов/жиров: сложные крупы и полезные жиры в плюс,
        # рафинированное/простое — в минус
        if any(m in combo_names for m in WHOLE_GRAIN_MARKERS):
            score -= 0.4
        if any(m in combo_names for m in HEALTHY_FAT_MARKERS):
            score -= 0.4
        if any(m in combo_names for m in REFINED_CARB_MARKERS):
            score += 1.0

    if meal_label in ("breakfast", "Завтрак"):
        flour_base_count = sum(
            1 for item in combo
            if any(kw in item["name"].lower() for kw in ("ролл", "сэндвич", "лаваш", "тост", "хлеб"))
        )
        if flour_base_count > 1:
            score += 2.0
        score += 1.0 * abs(tf / max(F, 1) - 1)
        if tp > 0 and tc > tp * 3:
            score += 1.5
        if tc > 80:
            score += 2.0
        if tp < 20:
            score += 1.0

    if meal_label in ("lunch", "Обед", "Обед / ужин", "dinner", "Ужин"):
        potato_count = sum(
            1 for item in combo
            if any(kw in item["name"].lower() for kw in ("картофел", "пюре", "картошк"))
        )
        if potato_count > 1:
            score += 2.0

        has_vegetables = any(
            any(kw in item["name"].lower()
                for kw in ("овощ", "салат", "борщ", "щи",
                           "суп", "брокколи", "шпинат", "томат",
                           "перец", "кабачок", "баклажан"))
            for item in combo
        )
        if not has_vegetables:
            score += 1.5

        if tp < 25:
            score += 2.0

        if tc > 70:
            score += 1.5
        if len(combo) == 1 and tc > 50:
            score += 2.0

        if len(combo) == 1 and combo[0].get("weight_g", 0) > 500:
            score += 1.5

        has_pilaf = any("плов" in item["name"].lower() for item in combo)
        if has_pilaf and len(combo) == 1:
            score += 2.5

        starchy_count = sum(
            1 for item in combo
            if any(kw in item["name"].lower()
                   for kw in ("макарон", "паста", "картофел", "пюре", "запеканка картофел"))
        )
        if starchy_count > 1:
            score += 2.0

    if liked_ids:
        if any(str(item.get("xml_id", "")) in liked_ids for item in combo):
            score += 0.3  # slight penalty to prefer fresh dishes over previously liked ones

    score += random.uniform(0, 0.5)  # jitter for variety between identical searches

    return score


# Форма блюда важнее ингредиента: «Щи с говядиной» — это суп, а не говядина.
# Тип крупы важнее общего «каша»: «Каша гречневая» и «Гречка отварная» —
# одна категория (два гречневых блюда в приёме недопустимы).
# Эти типы определяют категорию раньше белковых/крахмальных корней.
_DISH_FORM_MARKERS = (
    ("суп", "суп"), ("борщ", "суп"), ("щи", "суп"), ("солянк", "суп"),
    ("харчо", "суп"), ("уха", "суп"), ("рассольник", "суп"),
    ("похлёбк", "суп"), ("щавелев", "суп"), ("окрошк", "суп"),
    ("крем-суп", "суп"), ("суп-пюре", "суп"),
    ("салат", "салат"), ("паста", "паста"), ("пицца", "пицца"),
    # крупы — по виду зерна, не по слову «каша»
    ("гречк", "гречка"), ("гречн", "гречка"),
    ("овсян", "овсянка"), ("геркулес", "овсянка"),
    ("перлов", "перловка"), ("булгур", "булгур"),
    ("киноа", "киноа"), ("кускус", "кускус"),
)


def _item_category(name: str) -> str | None:
    n = name.lower()
    # 1) форма блюда (суп/салат/...) имеет приоритет над ингредиентом
    form = next((cat for kw, cat in _DISH_FORM_MARKERS if kw in n), None)
    if form:
        return form
    # 2) корни (белок/крахмал/тип), затем алиасы
    cat = next((r for r in CATEGORY_ROOTS if r in n), None)
    if cat is None:
        cat = next((CATEGORY_ALIASES[a] for a in CATEGORY_ALIASES if a in n), None)
    return cat


def _combo_has_duplicate_category(combo: list) -> bool:
    seen: set = set()
    for item in combo:
        cat = _item_category(item["name"])
        if cat and cat in seen:
            return True
        if cat:
            seen.add(cat)
    return False


def _combo_meets_breakfast_minimum(combo: list, K: float = 0.0) -> bool:
    """Combination total must have ≥10g protein and ≥15g carbs.

    Клетчатка проверяется не здесь, а штрафом в _score_combo —
    жёсткий запрет оставлял бы приём пустым на бедном пуле.
    """
    total_p = total_c = 0.0
    n = len(combo)
    for item in combo:
        ratio = _calc_portion_ratio(item, K, n) if K > 0 else 1.0
        try:
            nv = item["nutrition_variants"][0]
            wg = item["weight_g"] / 100 * ratio
            total_p += float(nv["protein"]) * wg
            total_c += float(nv["carbohydrates"]) * wg
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    return total_p >= 10 and total_c >= 15


def _combo_meets_lunch_minimum(combo: list, K: float = 0.0) -> bool:
    total_p = total_c = 0.0
    n = len(combo)
    for item in combo:
        ratio = _calc_portion_ratio(item, K, n) if K > 0 else 1.0
        try:
            nv = item["nutrition_variants"][0]
            wg = item["weight_g"] / 100 * ratio
            total_p += float(nv["protein"]) * wg
            total_c += float(nv["carbohydrates"]) * wg
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    if total_p < 20:
        return False
    if total_c > 65:
        return False

    has_soup = any(
        any(kw in item["name"].lower()
            for kw in ("суп", "борщ", "щи", "солянк",
                       "рассольник", "уха", "похлёбк"))
        for item in combo
    )
    has_pasta = any(
        any(kw in item["name"].lower()
            for kw in ("макарон", "паста", "тальятелле",
                       "ризони", "спагетти", "лапша"))
        for item in combo
    )
    if has_soup and has_pasta:
        return False

    return True


def find_best_combinations(
    pool: list, P: float, F: float, C: float, K: float,
    n_combos: int = 2, max_dishes: int = 4,
    meal_label: str = "",
    liked_ids: set | None = None,
    kcal_cap: float | None = None,
    score_adjust=None,
) -> list:
    """Enumerate all subsets of size 1..max_dishes, return n_combos non-overlapping best.

    kcal_cap — жёсткий потолок суммарной калорийности комбинации
    (с учётом реальных порций 0.5/1.0); выше потолка — отбрасываются до скоринга.
    score_adjust — опциональный callable(combo) -> float, добавляется к скору
    (используется недельным планировщиком для покрытия пищевых групп).
    """
    is_breakfast = meal_label in ("breakfast", "Завтрак")
    # ужин проходит те же структурные проверки, что обед
    # (минимум белка, потолок углеводов) — иначе ужином становится что угодно
    is_lunch = meal_label in ("lunch", "Обед", "Обед / ужин", "dinner", "Ужин")
    scored = []
    for size in range(1, min(max_dishes, len(pool)) + 1):
        for combo in iter_combinations(pool, size):
            if kcal_cap is not None:
                if combo_portion_kbju(list(combo), K)[3] > kcal_cap:
                    continue
            if _combo_has_duplicate_category(list(combo)):
                continue
            if is_breakfast and not _combo_meets_breakfast_minimum(list(combo), K):
                continue
            if is_lunch and not _combo_meets_lunch_minimum(list(combo), K):
                continue
            combo_score = _score_combo(list(combo), P, F, C, K, meal_label, liked_ids)
            if score_adjust is not None:
                combo_score += score_adjust(list(combo))
            scored.append((combo_score, list(combo)))
    scored.sort(key=lambda x: x[0])

    def _combo_categories(combo: list) -> set:
        return {c for item in combo if (c := _item_category(item["name"]))}

    result: list = []
    used_ids: set = set()
    used_categories: set = set()
    for _, combo in scored:
        if len(result) >= n_combos:
            break
        ids = {item["id"] for item in combo}
        cats = _combo_categories(combo)
        if not ids & used_ids and not cats & used_categories:
            result.append(combo)
            used_ids |= ids
            used_categories |= cats

    # Fallback 1: relax category check, but keep strict dish non-overlap
    if len(result) < n_combos:
        all_used_ids = {i["id"] for r in result for i in r}
        selected_sets = [frozenset(i["id"] for i in r) for r in result]
        for _, combo in scored:
            if len(result) >= n_combos:
                break
            combo_set = frozenset(i["id"] for i in combo)
            if combo_set not in selected_sets and not (combo_set & all_used_ids):
                result.append(combo)
                selected_sets.append(combo_set)
                all_used_ids |= combo_set

    return result[:n_combos]


async def run_gpt_combinations(
    enriched_items: list,
    P: float, F: float, C: float, K: float,
    preference: str | None,
    meal_label: str = "",
    liked_ids: set | None = None,
) -> list:
    """GPT picks a qualitative pool, Python finds best 2 combos by КБЖУ fit."""
    if not enriched_items:
        return []

    print(f"[gpt_combinations] На вход: {len(enriched_items)} блюд (meal_label={meal_label!r})")

    # Step 1: GPT ranks/selects qualitative candidates
    if len(enriched_items) <= 10:
        # Too few — skip GPT, pass everything to combinator
        print(f"[gpt_combinations] Мало кандидатов ({len(enriched_items)}) — GPT пропущен, берём все")
        pool = enriched_items
    elif len(enriched_items) >= 15:
        # Enough items — ask GPT to RANK (return all sorted by quality, no filtering)
        print(f"[gpt_combinations] Много кандидатов ({len(enriched_items)}) — GPT ранжирует без отсева")
        pool = await run_gpt_selection(
            enriched_items, P, F, C, K, preference,
            count=len(enriched_items), meal_label=meal_label,
        )
        if not pool:
            pool = enriched_items
    else:
        pool = await run_gpt_selection(
            enriched_items, P, F, C, K, preference,
            count=min(24, len(enriched_items)), meal_label=meal_label,
        )
        if not pool:
            pool = enriched_items[:12]

    print(f"[gpt_combinations] GPT выбрал: {len(pool)} блюд -> комбинатор")

    # Step 2: Python finds the 2 best non-overlapping combinations
    result = find_best_combinations(pool, P, F, C, K, n_combos=2, max_dishes=min(len(pool), 6), meal_label=meal_label, liked_ids=liked_ids)
    print(f"[gpt_combinations] Комбинатор вернул: {len(result)} комбинаций")
    return result
