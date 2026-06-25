"""Мил-преп планировщик на рецептах.

Все приёмы (завтрак/обед/ужин) — рецепты из гайдов с фото, масштабируются
под КБЖУ. Завтрак — быстрый, без духовки. Обед/ужин — batch-friendly
(хранятся, готовятся впрок), готовка в N варочных сессий в неделю.
Закупка — суммарные ингредиенты (сырые продукты) на неделю.
"""
import json, os, re, random, itertools

_DIR = os.path.dirname(__file__)
_RECIPES = json.load(open(os.path.join(_DIR, "data", "recipes.json")))["recipes"]
_COMP = json.load(open(os.path.join(_DIR, "data", "components.json")))["components"]

# Гарвардская тарелка: ½ овощи/фрукты, ¼ цельные злаки, ¼ белок.
# Докручиваем каждый приём недостающими компонентами.
_GARNISHES = [c for c in _COMP["carbs"]]
_VEGS = [c for c in _COMP["vegetables"] if c.get("batch", True)]
_FATS = [c for c in _COMP["fats"]]
# белок для обеда/ужина (солёные источники) и для завтрака (молочка/яйца)
_MAIN_PROTEINS = [c for c in _COMP["proteins"]
                  if c["name"] not in ("Греческий йогурт", "Творог 5%")]
_BRK_PROTEINS = [c for c in _COMP["proteins"]
                 if c["name"] in ("Яйца", "Творог 5%", "Греческий йогурт")]

# маркеры — что уже есть в блюде
_PROTEIN_MARK = ("котлет", "шницел", "фрикадел", "тефтел", "биточ", "грудк", "филе",
                 "стейк", "бефстроганов", "гуляш", "рыб", "лосось", "форел", "треск",
                 "минтай", "индейк", "курин", "куриц", "говядин", "свинин", "фарш",
                 "яйц", "яиц", "омлет", "скрэмбл", "творог", "йогурт", "сыр", "тофу",
                 "чечевиц", "нут", "фасол", "креветк", "тунец", "сырник")
_CARB_MARK = ("рис", "гречк", "булгур", "киноа", "картоф", "батат", "пюре", "паст",
              "макарон", "спагетти", "перлов", "кускус", "плов", "лапш", "нут",
              "чечевиц", "фасол", "хлеб", "лаваш", "тортиль", "рагу", "овсян",
              "геркулес", "хлоп", "мюсли", "гранол", "крупа", "блин", "оладь", "тост")
_VEG_MARK = ("овощ", "салат", "брокколи", "шпинат", "томат", "помидор", "огурец",
             "перец", "кабачок", "цукини", "капуст", "морков", "рагу", "грибами",
             "грибы", "шампиньон", "цветн", "руккол", "зелен", "баклажан", "горошек")
# сильные источники жира (не считаем следовое «масло» для жарки)
_FAT_MARK = ("авокадо", "орех", "миндал", "арахис", "тахин", "песто", "сыр", "фета",
             "семен", "семечк", "сливочн")


# роли компонентов тарелки для текста рекомендации / подписи / доли ккал
_ROLE = {"protein": "белок", "carb": "сложный углевод", "veg": "овощи", "fat": "полезный жир"}
_TAIL = {"protein": "белок", "carb": "гарнир", "veg": "овощи", "fat": "полезный жир"}
_SHARES = {"protein": 0.30, "carb": 0.20, "veg": 0.15, "fat": 0.13}


def _has(text, marks):
    return any(m in text for m in marks)


def _recipe_text(recipe):
    return (recipe.get("title", "") + " " +
            " ".join(i.get("name", "") for i in recipe.get("ingredients", []))).lower()


def _present(recipe):
    """Какие компоненты тарелки уже есть в блюде."""
    t = _recipe_text(recipe)
    return {"protein": _has(t, _PROTEIN_MARK), "carb": _has(t, _CARB_MARK),
            "veg": _has(t, _VEG_MARK), "fat": _has(t, _FAT_MARK)}


def _side_item(comp, kcal_target):
    per = comp["kbju"]
    name = re.sub(r"\s*\(.*?\)", "", comp["name"]).strip()
    g = max(40, min(220, round(kcal_target / (per["kcal"] / 100)))) if per["kcal"] else 100
    nutr = {x: round(per[x] * g / 100, 1) for x in ("kcal", "protein", "fat", "carbs")}
    return {"name": name, "amount": g, "unit": "г"}, nutr

# в закупку не кладём (вода/специи/мелочь по вкусу)
_SHOP_EXCLUDE = (
    "вода", "соль", "перец молот", "перец чёрн", "перец чер", "специи", "ванилин",
    "разрыхлитель", "подсласт", "сахзам", "зелень", "укроп", "петрушк", "базилик",
    "орегано", "корица", "мускат", "гвоздика", "цедра", "паприка", "кинза", "мята",
    "лимонный сок", "сок лимона", "соевый соус", "горчиц", "песто", "уксус", "чеснок",
)
# канон: варианты названия -> единый продукт (порядок важен: специфичное выше)
_CANON = [
    (("сливки или молоко",), "Молоко"),
    (("кокосовое молоко", "молоко кокос"), "Кокосовое молоко"),
    (("соевое молоко", "молоко соев"), "Соевое молоко"),
    (("молок",), "Молоко"),
    (("сливк",), "Сливки"),
    (("греческий йогурт", "йогурт греческ"), "Греческий йогурт"),
    (("йогурт",), "Йогурт"),
    (("творожный сыр", "сыр творожн"), "Творожный сыр"),
    (("маскарпоне",), "Маскарпоне"),
    (("мягкий творог", "творог мягк"), "Мягкий творог"),
    (("творог",), "Творог"),
    (("фета",), "Фета"),
    (("панировочн", "сухари"), "Панировочные сухари"),
    (("мука",), "Мука"),
    (("куриное филе", "филе курин", "грудка курин", "куриная грудка"), "Куриное филе"),
    (("куриный фарш", "фарш куриный", "фарш курин"), "Куриный фарш"),
    (("филе индейки", "грудка индейки", "индейк"), "Филе индейки"),
    (("соевый фарш",), "Соевый фарш"),
    (("соевое мясо",), "Соевое мясо"),
    (("говяжий фарш", "фарш говяж", "говядин", "говяж"), "Говяжий фарш"),
    (("тофу",), "Тофу"),
    (("лосось", "сёмга", "семга", "форель"), "Лосось/форель"),
    (("треска", "минтай"), "Белая рыба"),
    (("тунец",), "Тунец"),
    (("креветк",), "Креветки"),
    (("яйц", "яиц"), "Яйца"),
    (("гречк",), "Гречка"),
    (("киноа",), "Киноа"),
    (("булгур",), "Булгур"),
    (("кускус",), "Кускус"),
    (("перлов",), "Перловка"),
    (("овсян", "геркулес", "хлоп"), "Овсяные хлопья"),
    (("паста", "макарон", "спагетти"), "Макароны"),
    (("рис",), "Рис"),
    (("лаваш",), "Лаваш"),
    (("тортиль",), "Тортилья"),
    (("хлебц",), "Хлебцы"),
    (("чечевиц",), "Чечевица"),
    (("нут",), "Нут"),
    (("фасоль", "фасол"), "Фасоль"),
    (("горошек",), "Зелёный горошек"),
    (("брокколи",), "Брокколи"),
    (("цветная капуст", "капуста цветн"), "Цветная капуста"),
    (("кабачок", "цукини"), "Кабачок"),
    (("баклажан",), "Баклажан"),
    (("болгарский перец", "перец болгар", "сладкий перец"), "Болгарский перец"),
    (("помидор", "томат", "черри"), "Помидоры"),
    (("огурец", "огурц"), "Огурцы"),
    (("морковь",), "Морковь"),
    (("свёкл", "свекл"), "Свёкла"),
    (("шпинат",), "Шпинат"),
    (("руккол",), "Руккола"),
    (("салат", "айсберг", "мангольд"), "Салат"),
    (("шампиньон", "грибы"), "Шампиньоны"),
    (("лук",), "Лук"),
    (("картофел", "батат"), "Картофель/батат"),
    (("авокадо",), "Авокадо"),
    (("оливковое масло", "масло оливк"), "Оливковое масло"),
    (("растительное масло", "масло раст", "подсолнечн"), "Растительное масло"),
    (("кокосовое масло", "масло кокос"), "Кокосовое масло"),
    (("масло",), "Сливочное масло"),
    (("миндал",), "Миндаль"),
    (("орех",), "Орехи"),
    (("семена чиа", "чиа", "семя чиа"), "Семена чиа"),
    (("семена тыкв", "тыквенны", "семя тыкв"), "Семена тыквы"),
    (("семена льн", "льнян", "семя льна"), "Семена льна"),
    (("мак",), "Мак"),
    (("семена", "семечк", "семя"), "Семена"),
    (("банан",), "Бананы"),
    (("яблок",), "Яблоки"),
    (("груш",), "Груши"),
    (("клубник", "малин", "черник", "голуб", "ягод", "смородин", "ежевик"), "Ягоды"),
    (("курага",), "Курага"),
    (("изюм",), "Изюм"),
    (("мёд", "мед"), "Мёд"),
    (("какао",), "Какао"),
    (("шоколад",), "Шоколад"),
    (("гранола", "мюсли"), "Гранола"),
    (("арахисов",), "Арахисовая паста"),
    (("сыр",), "Сыр"),
]


def _canon(name):
    """Свести ингредиент к единому продукту для закупки; None — не покупаем."""
    n = name.lower()
    n = re.sub(r"\(.*?\)", " ", n)                      # убрать (большой), (тесто)…
    for w in ("для подачи", "для обвалки", "для начинки", "для соуса",
              "начинка", "тесто", "по вкусу"):
        n = n.replace(w, " ")
    n = re.sub(r"\d+[.,]?\d*\s*%", " ", n)              # убрать проценты
    n = " ".join(n.split())
    if any(e in n for e in _SHOP_EXCLUDE):
        return None
    for subs, canon in _CANON:
        if any(s in n for s in subs):
            return canon
    return name.strip().capitalize()

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


def _round_amt(amount, unit):
    """Штучные ингредиенты — целыми числами (без «1,3 яйца»), остальное до 0,1."""
    if not isinstance(amount, (int, float)):
        return amount
    if (unit or "").lower() == "шт":
        return max(1, round(amount)) if amount > 0 else 0
    return round(amount, 1)


def _scale(recipe, target_kcal, lo=0.6, hi=1.8, single=None):
    """single — разовое блюдо (не на впрок): только уменьшаем порцию, не увеличиваем.
    Если None — определяем по флагу batch_friendly рецепта."""
    if single is None:
        single = not recipe.get("batch_friendly", False)
    if single:
        hi = min(hi, 1.0)
    base = recipe["nutrition"]["kcal"] or target_kcal
    f = max(lo, min(hi, round(target_kcal / base, 2))) if base else 1.0
    nutr = {x: round((recipe["nutrition"].get(x) or 0) * f, 1) for x in ("kcal", "protein", "fat", "carbs")}
    ings = [{"name": i["name"], "amount": _round_amt(
                (i["amount"] * f if isinstance(i.get("amount"), (int, float)) else i.get("amount")), i["unit"]),
             "unit": i["unit"]} for i in recipe["ingredients"]]
    return {"id": recipe["id"], "title": recipe["title"], "factor": f, "time_min": recipe.get("time_min"),
            "image": recipe.get("image"), "nutrition": nutr, "ingredients": ings,
            "steps": recipe.get("steps", []), "source": recipe.get("source"),
            "estimated": recipe.get("nutrition_estimated", False)}


def _apply_factor(meal, corr):
    for x in ("kcal", "protein", "fat", "carbs"):
        meal["nutrition"][x] = round(meal["nutrition"][x] * corr, 1)
    for i in meal["ingredients"]:
        if isinstance(i.get("amount"), (int, float)):
            i["amount"] = _round_amt(i["amount"] * corr, i.get("unit"))


def _assemble(raw, mt, want, pickers, lo=0.6, hi=1.8, single=None):
    """Собрать приём по гарвардской тарелке: дополнить недостающими компонентами.
    pickers — {'protein'/'carb'/'veg': callable()->компонент} (цикл или random)."""
    pres = _present(raw)
    sides, tail, notes = [], [], []
    add = {x: 0 for x in ("kcal", "protein", "fat", "carbs")}
    for typ in ("protein", "carb", "veg", "fat"):
        if typ not in want or pres[typ]:
            continue
        ing, n = _side_item(pickers[typ](), mt * _SHARES[typ])
        sides.append(ing); tail.append(_TAIL[typ])
        notes.append(f"{_ROLE[typ]} ({ing['name']})")
        for x in add:
            add[x] = round(add[x] + n[x], 1)
    m = _scale(raw, max(150, mt - add["kcal"]) if sides else mt, lo=lo, hi=hi, single=single)
    if sides:
        m["ingredients"] = m["ingredients"] + sides
        for x in add:
            m["nutrition"][x] = round(m["nutrition"][x] + add[x], 1)
        m["title"] = m["title"] + " + " + " и ".join(tail)
        m["note"] = "По принципу гарвардской тарелки блюдо дополнено: " + ", ".join(notes) + "."
    return m


def _pool(meal_types, only_quick=False, breakfast_max_time=99, batch=False, allow_overnight=True):
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
        if not allow_overnight and r.get("overnight"):
            continue
        res.append(r)
    # приоритет рецептам с фото, затем по времени
    res.sort(key=lambda r: (0 if r.get("image") else 1, r.get("time_min") or 99))
    return res


def _shuffle_photo_first(lst):
    photo = [x for x in lst if x.get("image")]
    rest = [x for x in lst if not x.get("image")]
    random.shuffle(photo); random.shuffle(rest)
    return photo + rest


_FREQ_LABEL = {7: "каждый день", 5: "5 раз в неделю", 4: "через день",
               3: "3 раза в неделю", 2: "2 раза в неделю", 1: "раз в неделю"}


def _cook_days(freq, days=7):
    freq = max(1, min(days, int(freq)))
    if freq >= days:
        return list(range(1, days + 1))
    return sorted({round(i * days / freq) + 1 for i in range(freq)})


def _parse_exclude(exclude):
    """Строку «не ем» -> список стоп-слов (нижний регистр)."""
    if not exclude:
        return []
    return [w.strip().lower() for w in re.split(r"[,;\n]+", exclude) if w.strip()]


def _recipe_ok(recipe, ex):
    """Рецепт подходит, если не содержит исключённый продукт как заметный ингредиент.
    Специи/по вкусу/мелочь не считаем (чтобы «перец» не убирал блюда с чёрным перцем)."""
    if not ex:
        return True
    if any(e in recipe.get("title", "").lower() for e in ex):
        return False
    for i in recipe.get("ingredients", []):
        nm = (i.get("name") or "").lower()
        if not any(e in nm for e in ex):
            continue
        if _canon(i.get("name", "")) is None:
            continue
        a = i.get("amount"); u = (i.get("unit") or "").lower()
        if u == "шт" or (isinstance(a, (int, float)) and _grams(i) >= 20):
            return False
    return True


def _comp_ok(comp, ex):
    if not ex:
        return True
    n = comp["name"].lower()
    return not any(e in n for e in ex)


def plan_week(target, days=7, breakfast_max_time=20,
              breakfast_freq=7, lunch_freq=2, dinner_freq=2, snacks=0,
              snack_quick=True, overnight=True, exclude="", restrictions=None):
    snacks = max(0, min(2, int(snacks)))
    ex = _parse_exclude(exclude)
    flt = lambda lst: [r for r in lst if _recipe_ok(r, ex)]
    brk = _shuffle_photo_first(flt(
        _pool({"завтрак"}, only_quick=True, breakfast_max_time=breakfast_max_time, allow_overnight=overnight))
        or flt(_pool({"завтрак"}, allow_overnight=overnight))
        or _pool({"завтрак"}, allow_overnight=overnight))
    mains = flt(_pool({"обед", "ужин"}, batch=True, allow_overnight=overnight))
    if len(mains) < 4:
        mains = flt(_pool({"обед", "ужин"}, allow_overnight=overnight)) or \
            _pool({"обед", "ужин"}, allow_overnight=overnight)
    mains = _shuffle_photo_first(mains)
    snk_pool = _pool({"перекус"}, only_quick=snack_quick, breakfast_max_time=15, allow_overnight=overnight) \
        if snack_quick else _pool({"перекус"}, allow_overnight=overnight)
    snk = _shuffle_photo_first(flt(snk_pool) or flt(_pool({"перекус"}, allow_overnight=overnight)))
    # компоненты тарелки с учётом предпочтений
    garnishes = [c for c in _GARNISHES if _comp_ok(c, ex)] or _GARNISHES
    vegs = [c for c in _VEGS if _comp_ok(c, ex)] or _VEGS
    fats = [c for c in _FATS if _comp_ok(c, ex)] or _FATS
    mprot = [c for c in _MAIN_PROTEINS if _comp_ok(c, ex)] or _MAIN_PROTEINS
    bprot = [c for c in _BRK_PROTEINS if _comp_ok(c, ex)] or _BRK_PROTEINS
    bc = itertools.cycle(brk)
    lc = itertools.cycle(mains)
    dc = itertools.cycle(mains[len(mains) // 2:] + mains[:len(mains) // 2] if len(mains) > 1 else mains)
    sc = itertools.cycle(snk or brk)
    gc = itertools.cycle(random.sample(garnishes, len(garnishes)))
    vc = itertools.cycle(random.sample(vegs, len(vegs)))
    fc = itertools.cycle(random.sample(fats, len(fats)))
    pc = itertools.cycle(random.sample(mprot, len(mprot)))
    bpc = itertools.cycle(random.sample(bprot, len(bprot)))
    def _complete(raw, mt, want, lo=0.6, hi=1.8, proteins=None, single=None):
        prot = proteins if proteins is not None else pc
        pickers = {"carb": lambda: next(gc), "veg": lambda: next(vc),
                   "protein": lambda: next(prot), "fat": lambda: next(fc)}
        return _assemble(raw, mt, want, pickers, lo=lo, hi=hi, single=single)

    # доли калорий по приёмам (завтрак поглощает остаток для точных ккал)
    if snacks == 0:
        shares = {"Обед": 0.42, "Ужин": 0.33}
        snack_labels = []
    elif snacks == 1:
        shares = {"Обед": 0.38, "Ужин": 0.30, "Перекус": 0.10}
        snack_labels = ["Перекус"]
    else:
        shares = {"Обед": 0.34, "Ужин": 0.27, "Перекус 1": 0.09, "Перекус 2": 0.09}
        snack_labels = ["Перекус 1", "Перекус 2"]

    week, shopping = [], {}

    def add_shop(ings):
        for i in ings:
            g = _grams(i)
            if g <= 0:
                continue
            canon = _canon(i["name"])
            if canon:
                shopping[canon] = shopping.get(canon, 0) + g

    # Мил-преп: обед/ужин готовятся впрок. Одно блюдо переиспользуется до
    # следующей готовки, поэтому при «2 раза в неделю» в плане 2 разных обеда,
    # а не семь. Готовим блюдо один раз (с фикс. гарниром/овощами) и повторяем.
    def _rotation(cycle, freq, label):
        cook = set(_cook_days(freq, days))
        per_day, cur = {}, None
        for d in range(1, days + 1):
            if cur is None or d in cook:
                cur = _complete(next(cycle), target["kcal"] * shares[label],
                                want=("protein", "carb", "veg", "fat"), lo=0.5)
                cur["label"] = label
            per_day[d] = cur
        return per_day

    def _snack_rotation(cycle, freq, label):
        cook = set(_cook_days(freq, days))
        per_day, cur = {}, None
        for d in range(1, days + 1):
            if cur is None or d in cook:
                cur = _scale(next(cycle), target["kcal"] * shares[label])
                cur["label"] = label
            per_day[d] = cur
        return per_day

    lunch_by_day = _rotation(lc, lunch_freq, "Обед")
    dinner_by_day = _rotation(dc, dinner_freq, "Ужин")
    # быстрый перекус (без готовки) — каждый день разный; «подольше» — готовим впрок 2 р/нед
    snack_freq = days if snack_quick else 2
    snack_rot = {lab: _snack_rotation(sc, snack_freq, lab) for lab in snack_labels}

    # завтрак: блюдо и белковая добавка фиксируются на блок готовки, порция — по дню
    cook_b = set(_cook_days(breakfast_freq, days))
    brk_raw, brk_prot, cur_r, cur_p = {}, {}, None, None
    for d in range(1, days + 1):
        if cur_r is None or d in cook_b:
            cur_r, cur_p = next(bc), next(bpc)
        brk_raw[d], brk_prot[d] = cur_r, cur_p

    for d in range(1, days + 1):
        meals = [lunch_by_day[d], dinner_by_day[d]] + [snack_rot[lab][d] for lab in snack_labels]
        used_k = sum(m["nutrition"]["kcal"] for m in meals)
        # завтрак — «поглотитель» остатка калорий (фикс. блюдо/добавка на блок,
        # порция по дню); точность ±100 держим только за счёт завтрака,
        # т.к. обед/ужин — общие объекты блока и их трогать нельзя
        prot = brk_prot[d]
        pickers = {"protein": (lambda c=prot: c), "carb": (lambda: None), "veg": (lambda: None)}
        desired_b = max(150, target["kcal"] - used_k)
        b = _assemble(brk_raw[d], desired_b, ("protein",), pickers, lo=0.35, hi=3.0, single=False)
        b["label"] = "Завтрак"
        # точная подгонка: домножаем завтрак, чтобы он точно «добил» остаток калорий
        cur = b["nutrition"]["kcal"]
        if cur:
            _apply_factor(b, max(0.25, min(4.0, desired_b / cur)))
        for m in meals:
            add_shop(m["ingredients"])
        add_shop(b["ingredients"])
        day_tot = {k: round(b["nutrition"][k] + sum(m["nutrition"][k] for m in meals)) for k in target}
        week.append({"breakfast": b, "meals": meals, "day_total": day_tot})

    # регулярность готовки по каждому приёму
    schedule = [
        {"meal": "🍳 Завтрак", "label": _FREQ_LABEL.get(breakfast_freq, f"{breakfast_freq} р/нед"),
         "days": _cook_days(breakfast_freq, days)},
        {"meal": "🥗 Обед", "label": _FREQ_LABEL.get(lunch_freq, f"{lunch_freq} р/нед"),
         "days": _cook_days(lunch_freq, days)},
        {"meal": "🍽 Ужин", "label": _FREQ_LABEL.get(dinner_freq, f"{dinner_freq} р/нед"),
         "days": _cook_days(dinner_freq, days)},
    ]
    if snacks:
        schedule.append({"meal": "🍎 Перекусы", "label": f"{snacks} в день · обычно без готовки", "days": []})

    shop = {k: round(v) for k, v in sorted(shopping.items(), key=lambda x: -x[1])}
    return {"target": target, "days": week, "cook_schedule": schedule, "shopping_raw_g": shop}


def build_meal(kind, kcal, exclude="", avoid_id=None, overnight=True,
               breakfast_max_time=20, snack_quick=True):
    """Собрать одно блюдо для замены («не понравилось»). kind: breakfast/main/snack."""
    ex = _parse_exclude(exclude)
    garnishes = [c for c in _GARNISHES if _comp_ok(c, ex)] or _GARNISHES
    vegs = [c for c in _VEGS if _comp_ok(c, ex)] or _VEGS
    fats = [c for c in _FATS if _comp_ok(c, ex)] or _FATS
    if kind == "breakfast":
        pool = (_pool({"завтрак"}, only_quick=True, breakfast_max_time=breakfast_max_time, allow_overnight=overnight)
                or _pool({"завтрак"}, allow_overnight=overnight))
        want = ("protein",)
        prot = [c for c in _BRK_PROTEINS if _comp_ok(c, ex)] or _BRK_PROTEINS
        lo, hi, single = 0.35, 3.0, False
    elif kind == "snack":
        pool = (_pool({"перекус"}, only_quick=snack_quick, breakfast_max_time=15, allow_overnight=overnight)
                if snack_quick else _pool({"перекус"}, allow_overnight=overnight)) \
            or _pool({"перекус"}, allow_overnight=overnight)
        want, prot, (lo, hi, single) = (), _MAIN_PROTEINS, (0.6, 1.8, None)
    else:
        pool = _pool({"обед", "ужин"}, batch=True, allow_overnight=overnight)
        if len(pool) < 4:
            pool = _pool({"обед", "ужин"}, allow_overnight=overnight)
        want = ("protein", "carb", "veg", "fat")
        prot = [c for c in _MAIN_PROTEINS if _comp_ok(c, ex)] or _MAIN_PROTEINS
        lo, hi, single = 0.6, 1.8, None
    ok = [r for r in pool if _recipe_ok(r, ex)]
    cand = [r for r in ok if str(r.get("id")) != str(avoid_id)] or ok or pool
    raw = random.choice(cand)
    pickers = {"carb": lambda: random.choice(garnishes), "veg": lambda: random.choice(vegs),
               "protein": lambda: random.choice(prot), "fat": lambda: random.choice(fats)}
    return _assemble(raw, kcal, want, pickers, lo=lo, hi=hi, single=single)


def aggregate_shopping(ingredients):
    """Сумма сырых продуктов (граммы) по списку ингредиентов всех блюд."""
    shopping = {}
    for i in ingredients:
        g = _grams(i)
        if g <= 0:
            continue
        canon = _canon(i.get("name", ""))
        if canon:
            shopping[canon] = shopping.get(canon, 0) + g
    return {k: round(v) for k, v in sorted(shopping.items(), key=lambda x: -x[1])}


if __name__ == "__main__":
    res = plan_week({"kcal": 1950, "protein": 153, "fat": 68, "carbs": 160})
    for d, day in enumerate(res["days"], 1):
        t = day["day_total"]
        print(f"Д{d} {t['kcal']}/{t['protein']}/{t['fat']}/{t['carbs']}")
        print(f"   завтрак: {day['breakfast']['title']} {'📷' if day['breakfast']['image'] else ''}")
        for m in day["meals"]:
            print(f"   {m['label']}: {m['title']} {'📷' if m['image'] else ''}")
