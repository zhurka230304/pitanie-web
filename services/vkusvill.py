"""VkusVill MCP client — adapted from bot.py"""
import asyncio
import json
import re
import random
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

MCP_URL = "https://mcp001.vkusvill.ru/mcp"

_details_cache: dict = {}
_DB_CACHE_TTL_HOURS = 48

# ——— Filter lists (from bot.py) ———

BAD_KEYWORDS = [
    # «Не берём» по рекомендациям нутрициолога (карточки 5 и 7):
    # переработанное мясо, плавленый сыр, майонез, сладкая газировка,
    # творожная масса (сахар), каши быстрого приготовления
    "колбас", "сосиск", "сардель", "бекон", "салями", "ветчина",
    "плавленый сыр", "сыр плавлен", "сырный продукт",
    "майонез", "майонезн",
    "кока-кола", "кока кола", "coca-cola", "пепси", "газированный напиток",
    "сладкая газировка", "лимонад", "энергетик",
    "творожная масса", "творожный сырок", "глазированный сыр",
    "минутка", "быстрого приготовления", "не требует варки", "залей кипятком",
    "круассан", "выпечка сдобн", "сдоба",
    # Зоотовары: поиск «рагу» вытаскивает влажный корм для кошек
    "для кошек", "для собак", "для котят", "для щенков",
    "для животных", "корм влажный", "корм сухой", "лакомство для",
    # Ореховые смеси и семечки — снэк, а не приём пищи
    # (поиск «нут» вытаскивает «Смесь жареных орехов с нутом»)
    "смесь орех", "смесь жарен", "орехи жарен", "жареных орехов",
    "арахис", "фисташ", "семечки", "ядра подсолнечн",
    # Полуфабрикаты из теста — варить самому, не готовая еда
    "пельмен", "вареник", "манты", "хинкал", "равиоли",
    "замороженн", "зам.", "охл.", " вес",
    "котлета натуральная", "натуральная котлета",
    "полуфабрикат", "п/ф", "п.ф.", "сырой", "сырая", "сырые", "фарш",
    "шницель натуральный", "стейк сырой",
    "для тушения", "для запекания", "для жарки", "для гриля", "для варки",
    "в маринаде", "маринован",
    "продовольственная", "крупа ",
    "горох колотый", "горох лущёный",
    "фасоль сухая", "фасоль стручковая сырая",
    "чечевица красная", "чечевица зелёная", "чечевица тарелочная",
    "нут сухой", "слабосолён", "слабосоленая", "слабосоленый",
    "ломтики", "нарезка", "лук жарен", "лук карамелизир", "лук пассеров",
    "сухарики", "крутоны", "топпинг", "посыпка",
    "начинка для", "заправка для",
    "геркулес", "овсяные хлопья", "пшеничные хлопья", "ржаные хлопья",
    "кукурузные хлопья", "яйцо куриное", "яйца куриные",
    "яйцо перепелиное", "яйца перепелиные",
    "малосолен", "малосоленая", "малосоленый",
    "кусок филе",
    "басмати", "(пакетики)", "для сырников", "для творога",
    " с/м", "с/м,", "с/м ", " зам,", " зам.", "зам ", "очищ.", "без кожи", "без костей", " б/к",
    " натур.", " натур,", "натуральный кусок", "натуральная котлета",
    "соте из", "шашлык",
    "без панировки", "кусочки куриные", "кусочки индейки",
    "порционн",
    "консерв", "в собственном соку", "в томатном соусе", "в масле",
    "маргарин", "кулинарный жир", "гидрогенизир",
    "чипсы", "снэк", "сухофрукт", "орех жарен",
    "салатный натуральный",
    "тунец филе-кусочки", "тунец кусочки",
]

RAW_MEAT_PATTERNS = [
    r'^филе бедра', r'^бедро ', r'^голень ', r'^крыло ', r'^тушка ',
    r'^цыплёнок[\s]', r'^грудка[\s]', r'^шея ', r'^печень[\s]',
    r'^сердце[\s]', r'^желудки[\s]', r'^субпродукт',
    r'^яйцо ', r'^яйца ', r'^филе\-кусок', r'^филе кусок',
    r'^говядина[\s,]', r'^филе индейки', r'^филе курин',
    r'^вырезка[\s]', r'^антрекот[\s]', r'^карбонад[\s]', r'^поджарка из',
    r'^креветки[\s]', r'^мидии[\s]', r'^кальмар[\s]', r'^осьминог[\s]',
    r'^треска[\s]', r'^горбуша[\s]', r'^минтай[\s]',
    r'^семга[\s]', r'^сёмга[\s]', r'^лосось[\s]', r'^форель[\s]',
    r'^гребешки[\s]', r'^палтус[\s]', r'^судак[\s]', r'^щука[\s]',
    r'^мясо (куриное|говяжье|свиное|индейки)',
    r'^гуляш из цыплён', r'^гуляш из курин',
    r'^гуляш из говядин', r'^гуляш из свинин', r'^гуляш из индейк',
    r'^фарш', r'^стейк[\s]', r'^эскалоп[\s]',
    r'^филе грудки', r'^грудка индейки', r'^грудка курин',
    r'^филе лосос', r'^филе трески', r'^филе минтая', r'^филе судака',
    r'^индейка[\s,]', r'^курица[\s,]', r'^свинина[\s,]', r'^баранина[\s,]',
    r'^фасоль[\s,]', r'^нут[\s,]', r'^горох[\s,]', r'^чечевица[\s,]',
    r'^кукуруза[\s,]', r'^зелёный горошек', r'^зеленый горошек',
]

RAW_MEAT_SEARCH_PATTERNS = [
    r'из филе (грудки|бедра|индейки|курин|говядин|свинин|лосос|трески)',
    r'кусок (говядин|свинин|баранин|телятин)',
    # сырая птица для готовки: окорочок, бройлер, тушка цыплёнка
    r'окороч', r'бройлер', r'цыпл[её]нок\-', r'\-бройлер',
    r'филе окороч', r'бедро цыпл', r'голень цыпл',
]

GARNISH_WORDS = (
    "с картофел", "с пюре", "с рисом", "с гречк",
    "с овощами", "с макарон", "с капустой", "с соусом",
    "с тушёной", "с тушеной", "с запечён",
)
PROTEIN_ALONE_ROOTS = ("котлет", "тефтел", "биточ", "зраз", "ежик")

CATEGORY_ROOTS = [
    "омлет", "салат",
    "суп", "каша", "рис", "гречк", "паста", "пицца", "блин", "хумус",
    "ролл", "сэндвич",
    # Белковые источники — не два одинаковых в одной комбинации
    "курин", "говяд", "индейк", "свинин", "лосос", "форел", "треск",
    # Крахмалистые гарниры — не два в одной комбинации
    "картофел",
]

# Aliases: map to a canonical category for deduplication
# All dairy products → "молочное" so no two dairy items appear in one combo
CATEGORY_ALIASES = {
    # Тип блюда важнее ингредиента — эти алиасы должны матчиться первыми
    # («оладьи на кефире» — это блин, а не молочное по слову «кефир»)
    "оладь": "блин",
    "панкейк": "блин",
    "творог": "молочное",
    "творожн": "молочное",   # творожный боул/масса/десерт — тоже молочное
    "запеканка": "молочное",
    "сырник": "молочное",
    "творожник": "молочное",
    "йогурт": "молочное",
    "кефир": "молочное",
    "ряженка": "молочное",
    "простокваш": "молочное",
    # Супы — все к одной категории
    "борщ": "суп",
    "щи": "суп",
    "солянк": "суп",
    "харчо": "суп",
    "щавелев": "суп",
    "рассольник": "суп",
    "окрошк": "суп",
    "уха": "суп",
    "похлёбк": "суп",
    "крем-суп": "суп",
    "суп-пюре": "суп",
    # Паста и макаронные изделия
    "макарон": "паста",
    "ризони": "паста",
    "спагетти": "паста",
    "лапша": "паста",
    "феттучини": "паста",
    "тальятелле": "паста",
    "тальятел": "паста",
    "фетучин": "паста",
    "пенне": "паста",
    "лингвин": "паста",
    "букатин": "паста",
}

# Блюда, неподходящие для конкретного приёма пищи
MEAL_TYPE_EXCLUDE = {
    "breakfast": [
        "рис с ", "гречка с мяс",
        "тефтел", "котлет", "фрикадел", "голубц", "биточ", "зраз",
        "жаркое", "шницел", "отбивн",
        "закуска", "сельдь под шубой", "под шубой",
        "шаурма", "хот-дог",
    ],
    "snack": [
        "борщ", "солянка", "суп", "щи", "харчо",
        "бефстроганов", "гуляш", "азу",
        "паста", "лазанья", "плов",
    ],
    # суп — лёгкое первое, не основа ужина (ужин должен быть сытным)
    "dinner": ["суп", "борщ", "щи", "солянк", "харчо", "уха", "похлёбк"],
}

EXCLUDE_BY_PREFERENCE = {
    "вегетариан": [
        "куриц", "курин", "цыплён", "цыплен", "говядин", "свинин",
        "индейк", "котлет", "фарш", "бройлер", "уток", "утин",
        "баранин", "телятин", "мяс", "бефстроганов", "тефтел",
        "фрикадел", "шницел", "чикен",
        "лосос", "форел", "треск", "тунец", "тунц",
        "сёмг", "семг", "горбуш", "минтай", "карп", "рыб",
        "креветк", "морепродукт", "кальмар", "осьминог", "мидий",
    ],
    "веган": [
        "куриц", "курин", "цыплён", "цыплен", "говядин", "свинин",
        "индейк", "котлет", "фарш", "бройлер", "уток", "утин",
        "баранин", "телятин", "мяс", "бефстроганов", "тефтел",
        "фрикадел", "шницел", "чикен",
        "лосос", "форел", "треск", "тунец", "тунц",
        "сёмг", "семг", "горбуш", "минтай", "карп", "рыб",
        "креветк", "морепродукт", "кальмар", "осьминог", "мидий",
        "творог", "сыр", "молок", "кефир", "йогурт", "сметан", "омлет", "яйц",
    ],
    "без мяса": [
        "куриц", "курин", "цыплён", "цыплен", "говядин", "свинин",
        "индейк", "котлет", "фарш", "бройлер", "уток", "утин",
        "баранин", "телятин", "мяс", "бефстроганов", "тефтел", "фрикадел", "шницел", "чикен",
    ],
    "без рыбы": [
        "лосос", "форел", "треск", "тунец", "тунц",
        "сёмг", "семг", "горбуш", "минтай", "карп", "рыб",
        "креветк", "морепродукт", "кальмар", "осьминог", "мидий",
    ],
    "без курицы": ["куриц", "курин", "цыплён", "цыплен", "бройлер", "чикен"],
}

POSITIVE_KEYWORDS = ("куриц", "курин", "говядин", "свинин", "индейк", "мяс", "рыб", "лосос", "форел", "треск")

VEGETARIAN_KEYS = ("вегетариан", "веган")
NO_MEAT_KEYS = ("без мяса",)

ALL_QUERIES = [
    "готовое мясо", "готовая рыба", "омлет", "салат с белком", "творог",
    "курица", "индейка", "рыба запечённая", "говядина", "котлеты",
    "лосось", "форель", "греческий салат", "суп", "каша с мясом",
    "паста с курицей", "рис с овощами", "тефтели", "бефстроганов", "треска",
]

VEGETARIAN_QUERIES = [
    "салат овощной", "рис с овощами", "паста с овощами", "омлет",
    "творог", "гречка с овощами", "фалафель", "хумус",
    "овощное рагу", "суп овощной", "греческий салат", "запечённые овощи",
    "лазанья овощная", "пицца с овощами", "сырники", "блины с творогом",
    "чечевица готовая", "нут готовый", "грибы тушёные", "капуста тушёная",
    "тыква запечённая", "баклажаны", "кабачки", "морковь по-корейски",
    "свёкла", "салат с сыром", "яйца", "творожная запеканка",
]

NO_MEAT_QUERIES = [
    "готовая рыба", "лосось", "форель", "треска", "омлет", "творог",
    "салат овощной", "греческий салат", "рис с овощами", "гречка",
    "паста с овощами", "сырники", "запечённые овощи", "суп овощной",
    "чечевица", "нут", "фалафель", "хумус", "яйца",
]

MEAL_TYPE_QUERIES = {
    "breakfast": [
        # Яичные
        "омлет", "яичный омлет", "омлет с овощами",
        # Творожные
        "сырники", "творожная запеканка", "творог",
        # Каши
        "каша овсяная", "каша гречневая", "каша пшённая", "каша рисовая",
        # Молочное
        "йогурт", "йогурт высокобелковый",
        # Белковые
        "куриная грудка отварная", "индейка отварная", "грудка индейки",
        "рыба на пару", "лосось запечённый", "форель", "семга запеченная",
        # Комплексные блюда с белком и клетчаткой
        "боул", "поке боул", "боул с курицей", "боул с лососем",
        "салат с яйцом", "салат с тунцом",
        "салат с куриной грудкой", "салат с индейкой", "салат с яйцом и овощами",
        "сэндвич ролл", "ролл с курицей",
        "авокадо", "тост цельнозерновой",
        "яйца с овощами", "омлет с грибами", "омлет с томатами",
        "яйца пашот", "скрэмбл",
        "гречка с овощами", "рис с овощами",
        # Источники клетчатки
        "овощи с яйцом", "запечённые овощи с сыром",
        "овсянка с ягодами", "фрукты с творогом",
    ],
    "lunch": [
        "готовое блюдо",
        "горячее блюдо",
        "второе блюдо",
        "суп",
        "горячее с гарниром",
        "мясо с гарниром",
        "рыба с гарниром",
        "птица с гарниром",
        "салат",
        "обед готовый",
    ],
    "dinner": [
        "готовое блюдо",
        "горячее с гарниром",
        "рыба с гарниром",
        "птица с гарниром",
        "мясо с гарниром",
        "рыба запечённая",
        "индейка с овощами",
        "курица с овощами",
        "овощное рагу",
        "салат с белком",
    ],
    "snack": ["творог", "йогурт", "сырники", "хумус", "салат овощной"],
}

MEAL_PLANS = {
    2: [
        ("Завтрак", 0.50, MEAL_TYPE_QUERIES["breakfast"]),
        ("Обед / ужин", 0.50, MEAL_TYPE_QUERIES["lunch"]),
    ],
    3: [
        ("Завтрак", 0.30, MEAL_TYPE_QUERIES["breakfast"]),
        ("Обед", 0.40, MEAL_TYPE_QUERIES["lunch"]),
        ("Ужин", 0.30, MEAL_TYPE_QUERIES["dinner"]),
    ],
    4: [
        ("Завтрак", 0.25, MEAL_TYPE_QUERIES["breakfast"]),
        ("Обед", 0.35, MEAL_TYPE_QUERIES["lunch"]),
        ("Ужин", 0.30, MEAL_TYPE_QUERIES["dinner"]),
        ("Перекус", 0.10, MEAL_TYPE_QUERIES["snack"]),
    ],
    5: [
        ("Завтрак", 0.20, MEAL_TYPE_QUERIES["breakfast"]),
        ("Обед", 0.30, MEAL_TYPE_QUERIES["lunch"]),
        ("Ужин", 0.25, MEAL_TYPE_QUERIES["dinner"]),
        ("Перекус 1", 0.15, MEAL_TYPE_QUERIES["snack"]),
        ("Перекус 2", 0.10, MEAL_TYPE_QUERIES["snack"]),
    ],
}


# ——— MCP client ———

async def mcp_request(params: dict, req_id: int) -> dict:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }
                r = await client.post(
                    MCP_URL,
                    json={
                        "jsonrpc": "2.0", "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "clientInfo": {"name": "kbju-web", "version": "1.0"},
                            "capabilities": {},
                        },
                        "id": 1,
                    },
                    headers=headers, timeout=30,
                )
                session_id = r.headers.get("mcp-session-id")
                notify_headers = {**headers, "Mcp-Session-Id": session_id} if session_id else headers
                await client.post(
                    MCP_URL,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    headers=notify_headers, timeout=30,
                )
                r2 = await client.post(
                    MCP_URL,
                    json={"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": req_id},
                    headers=notify_headers, timeout=30,
                )
                result = r2.json()
                try:
                    await client.delete(MCP_URL, headers=notify_headers, timeout=10)
                except Exception:
                    pass
                return result
        except Exception:
            if attempt < 2:
                await asyncio.sleep(1)
    return {}


async def search_vkusvill(query: str, sort: str = "popularity", page: int = 1) -> list:
    result = await mcp_request({"name": "vkusvill_products_search", "arguments": {"q": query, "sort": sort, "page": page}}, 2)
    try:
        text = result["result"]["content"][0]["text"]
        data = json.loads(text)
        return data["data"]["items"] if data.get("ok") else []
    except Exception:
        return []


async def get_product_details(product_id: int) -> dict:
    if product_id in _details_cache:
        return _details_cache[product_id]

    # Try DB cache
    try:
        from database import AsyncSessionLocal
        from models import ProductDetailsCache
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_DB_CACHE_TTL_HOURS)
        async with AsyncSessionLocal() as db:
            row = await db.get(ProductDetailsCache, product_id)
            if row and row.updated_at >= cutoff:
                _details_cache[product_id] = row.data
                return row.data
    except Exception:
        pass

    # Fetch from MCP
    result = await mcp_request({"name": "vkusvill_product_details", "arguments": {"id": product_id}}, 3)
    try:
        text = result["result"]["content"][0]["text"]
        data = json.loads(text)
        details = data.get("data", {})
        if details:
            _details_cache[product_id] = details
            try:
                from database import AsyncSessionLocal
                from models import ProductDetailsCache
                async with AsyncSessionLocal() as db:
                    stmt = pg_insert(ProductDetailsCache).values(
                        product_id=product_id,
                        data=details,
                        updated_at=datetime.now(timezone.utc),
                    ).on_conflict_do_update(
                        index_elements=["product_id"],
                        set_={"data": details, "updated_at": datetime.now(timezone.utc)},
                    )
                    await db.execute(stmt)
                    await db.commit()
            except Exception:
                pass
        return details
    except Exception:
        return {}


async def create_cart(xml_ids: list) -> str:
    import logging
    products = [{"xml_id": xml_id, "q": 1} for xml_id in xml_ids]
    result = await mcp_request({"name": "vkusvill_cart_link_create", "arguments": {"products": products}}, 4)
    try:
        text = result["result"]["content"][0]["text"]
        data = json.loads(text)
        d = data.get("data", {}) or {}
        url = d.get("url") or d.get("link") or data.get("url") or data.get("link") or ""
        if not url:
            logging.warning(f"create_cart: empty url, raw response: {data}")
        return url
    except Exception as e:
        logging.warning(f"create_cart error: {e!r}, result keys: {list(result.keys()) if result else 'empty'}")
        return ""


async def find_shops(city_id: int = 0, region_id: int = 0) -> list:
    result = await mcp_request({
        "name": "vkusvill_shops",
        "arguments": {"id_city_filter": city_id, "id_region_filter": region_id, "page": 1}
    }, 5)
    try:
        text = result["result"]["content"][0]["text"]
        data = json.loads(text)
        return data.get("data", {}).get("items", [])
    except Exception:
        return []


# ——— Filters ———

def is_raw_meat(name: str) -> bool:
    n = name.lower().strip()
    return (
        any(re.match(p, n) for p in RAW_MEAT_PATTERNS) or
        any(re.search(p, n) for p in RAW_MEAT_SEARCH_PATTERNS)
    )


def is_bulk_by_name(name: str) -> bool:
    """Catch products with weight in name like 'Борщ 1 кг', 'Суп 500г'."""
    return bool(re.search(r'\d[\d,\.]*\s*кг', name.lower()))


def is_standalone_cheese(name: str) -> bool:
    return bool(re.match(r'^сыр[\s\"\'«]', name.lower()))


def is_standalone_sauce(name: str) -> bool:
    n = name.lower().strip()
    return bool(re.match(r'^соус[\s\"\'«]', n) or re.match(r'^заправка[\s\"\'«]', n))


# Words that clearly indicate a ready-to-eat dish
_READY_INDICATORS = (
    "суп", "борщ", "щи", "солянк", "харчо", "уха",
    "каша", "плов", "ризотто", "паэль",
    "паста", "спагетти", "тальятелле", "пенне", "фетучини", "лазань",
    "пицца", "пирог", "пирожок", "блин", "оладь", "панкейк",
    "омлет", "запеканк", "сырник", "творожник",
    "салат", "винегрет",
    "котлет", "тефтел", "биточ", "зраз", "фрикадел",
    "голубц", "долм",
    "рагу", "жаркое", "гуляш", "бефстроганов", "азу",
    "курица запечен", "индейка запечен", "рыба запечен",
    "жареная курица", "жареная рыба",
    "бургер", "сэндвич", "ролл", "шаурма", "wrap",
    "йогурт", "творог", "кефир", "ряженка", "простокваш",
    "мюсли", "гранол",
    "хумус", "фалафел",
    "овощи тушён", "овощи запечён", "рататуй",
    "тако", "буррито",
    "удон", "лапша удон", "по-тайски", "тайски", "по-корейски",
)


# Words in product name that strongly suggest it's NOT an ingredient
_READY_COOKING_WORDS = (
    "тушён", "тушен", "запечён", "запечен", "жарен",
    "варён", "варен", "копчён", "копчен",
    "готов", "отварн",
    "по-домашнему", "по домашнему", "домашн",
    "по-французски", "по-итальянски", "по-грузински",
    " с рисом", " с гречк", " с пюре", " с картофел",
    " с овощами", " с грибами", " с сыром",
)


_DESSERT_KEYWORDS = (
    "десерт", "торт", "пирожное", "конфет", "шоколад",
    "мороженое", "зефир", "мармелад", "карамель", "варень",
    "джем", "повидло", "сгущ", "глазур",
)


def is_valid_breakfast_dish(item: dict) -> bool:
    """Return True if dish meets minimum nutritional standards for breakfast."""
    name = item.get("name", "?")
    try:
        nv = item["nutrition_variants"][0]
        portion_g = min(item["weight_g"], 200)
        k = portion_g / 100
        protein = float(nv["protein"]) * k
        carbs = float(nv["carbohydrates"]) * k
        sugar = float(nv.get("sugar", 0)) * k
        saturated_fat = float(nv.get("saturated_fat", 0)) * k
        fat = float(nv["fat"]) * k
    except (KeyError, IndexError, ValueError, TypeError):
        print(f"[breakfast_filter] NO_NUTRITION: {name}")
        return False

    if protein < 10:
        print(f"[breakfast_filter] LOW_PROTEIN({protein:.1f}g): {name}")
        return False
    if fat > 30:
        print(f"[breakfast_filter] HIGH_FAT({fat:.1f}g): {name}")
        return False
    if sugar > 15:
        print(f"[breakfast_filter] HIGH_SUGAR({sugar:.1f}g): {name}")
        return False
    if saturated_fat > 8:
        print(f"[breakfast_filter] HIGH_SAT_FAT({saturated_fat:.1f}g): {name}")
        return False

    name_lower = name.lower()
    if any(kw in name_lower for kw in _DESSERT_KEYWORDS):
        print(f"[breakfast_filter] DESSERT: {name}")
        return False

    _BREAKFAST_EXCLUDE_TYPES = (
        "борщ", "солянка", "харчо", "суп", "щи", "похлёбка",
        "бефстроганов", "гуляш", "азу", "рагу", "жаркое",
        "паста", "макарон", "лазанья", "плов",
    )
    if any(kw in name_lower for kw in _BREAKFAST_EXCLUDE_TYPES):
        print(f"[breakfast_filter] HEAVY_DISH: {name}")
        return False

    SUSHI_MARKERS = (
        "филадельфия", "philadelphia",
        "нигири", "онигири", "маки", "темпура",
        "суши", "sushi", "унаги", "такояки",
    )
    if any(kw in name_lower for kw in SUSHI_MARKERS):
        print(f"[breakfast_filter] SUSHI: {name}")
        return False
    if "ролл" in name_lower and "сэндвич" not in name_lower and "sandwich" not in name_lower:
        print(f"[breakfast_filter] SUSHI_ROLL: {name}")
        return False

    HEAVY_SALAD_KEYWORDS = (
        "цезарь", "caesar",
        "оливье",
        "столичный",
        "мимоза",
        "под шубой", "сельдь под",
        "с майонез",
        "крабовый салат",
        "дор-блю", "дорблю", "рокфор", "горгонзол",
        "сливочный соус", "соус из сыра",
        "нисуаз", "nicoise",
    )
    if any(kw in name_lower for kw in HEAVY_SALAD_KEYWORDS):
        print(f"[breakfast_filter] HEAVY_SALAD: {name}")
        return False

    PROCESSED_MEAT_KEYWORDS = (
        "ветчина", "колбас", "сосиск", "сардел",
        "бекон", "карбонат", "буженина", "балык",
    )
    if any(kw in name_lower for kw in PROCESSED_MEAT_KEYWORDS):
        print(f"[breakfast_filter] PROCESSED_MEAT: {name}")
        return False

    FAST_BREAD_KEYWORDS = (
        "покет", "хот-дог", "бургер", "шаурма",
        "чиабатта", "багет", "бриошь",
    )
    if any(kw in name_lower for kw in FAST_BREAD_KEYWORDS):
        print(f"[breakfast_filter] FAST_BREAD: {name}")
        return False

    FRIED_HEAVY_KEYWORDS = (
        "шницел", "отбивн", "жареный", "жареная",
        "в панировке", "в кляре",
        "бифштекс",
        "стейк",
        "антрекот",
        "ростбиф",
    )
    if any(kw in name_lower for kw in FRIED_HEAVY_KEYWORDS):
        print(f"[breakfast_filter] FRIED_HEAVY: {name}")
        return False

    MEATBALL_KEYWORDS = (
        "фрикадел", "тефтел", "биточ", "зраз",
    )
    if any(kw in name_lower for kw in MEATBALL_KEYWORDS):
        print(f"[breakfast_filter] MEATBALL: {name}")
        return False

    CUTLET_KEYWORDS = (
        "котлет", "биточ", "зраз",
    )
    if any(kw in name_lower for kw in CUTLET_KEYWORDS):
        print(f"[breakfast_filter] CUTLET: {name}")
        return False

    HEAVY_SIDE_KEYWORDS = (
        "с картофельным пюре", "с пюре",
    )
    if any(kw in name_lower for kw in HEAVY_SIDE_KEYWORDS):
        print(f"[breakfast_filter] HEAVY_SIDE: {name}")
        return False

    SPREAD_KEYWORDS = (
        "паштет", "намазка", "форшмак",
    )
    if any(kw in name_lower for kw in SPREAD_KEYWORDS):
        print(f"[breakfast_filter] SPREAD: {name}")
        return False

    PASTA_KEYWORDS = (
        "паста", "пенне", "спагетти", "тальятелле",
        "фетучини", "лазанья", "макарон",
    )
    if any(kw in name_lower for kw in PASTA_KEYWORDS):
        print(f"[breakfast_filter] PASTA: {name}")
        return False

    OFFAL_KEYWORDS = (
        "печень", "почки", "сердце", "желудки", "язык",
    )
    if any(kw in name_lower for kw in OFFAL_KEYWORDS):
        print(f"[breakfast_filter] OFFAL: {name}")
        return False

    CHILDREN_KEYWORDS = (
        "детская", "детский", "детское", "для детей", "baby", "малыш",
    )
    if any(kw in name_lower for kw in CHILDREN_KEYWORDS):
        print(f"[breakfast_filter] CHILDREN: {name}")
        return False

    SMOKED_KEYWORDS = (
        "копчён", "копчен", "копчёно", "копчено",
        "холодного копчения", "горячего копчения",
    )
    if any(kw in name_lower for kw in SMOKED_KEYWORDS):
        print(f"[breakfast_filter] SMOKED: {name}")
        return False

    return True


# ——— Свежие фрукты/овощи/зелень (добавки к приёмам, не готовые блюда) ———

FRESH_VEG_MARKERS = (
    "огурц", "огурец", "томат", "помидор", "черри",
    "перец сладк", "морков", "редис",
    "салат лист", "айсберг", "романо", "руккол", "шпинат",
    "зелень", "укроп", "петрушк", "кинза", "сельдерей",
    "овощи свеж", "свежие овощи",
)

FRESH_FRUIT_MARKERS = (
    "яблок", "банан", "груш", "апельсин", "мандарин", "киви",
    "хурма", "персик", "нектарин", "слива", "сливы", "виноград",
    "голубик", "черник", "клубник", "малин", "гранат",
    "грейпфрут", "помело", "абрикос", "черешн", "ягоды свеж",
)

PRODUCE_EXCLUDE = (
    "сок", "напиток", "смузи", "сушён", "сушен", "вялен", "цукат",
    "чипс", "заморож", "консерв", "маринован", "солён", "солен",
    "по-корейск", "корейск", "по-китайск", "чука", "кимчи", "пикантн",
    "пюре", "варень", "джем", "конфитюр", "в сиропе",
    "запечён", "запечен", "салат с", "йогурт", "творог", "десерт",
    "сливк", "сливочн",
    # снэки со «свежими» словами в названии вкуса:
    # «Попкорн со вкусом „Сметана и зелень“» ловился маркером «зелень»
    "со вкусом", "попкорн", "сухарик", "крекер", "печенье",
    "батончик", "сметан", "соус",
    # сладости с названиями фруктов — не свежий фрукт
    # («Желе фруктовое мандарин» ловилось маркером «мандарин»)
    "желе", "мармелад", "пастил", "зефир", "мусс", "суфле",
    "пудинг", "панна", "чизкейк", "торт", "пирожн", "конфет",
)

# Свежие овощи/фрукты низкокалорийны; плотность выше — это снэк
PRODUCE_KCAL_CAP_PER_100G = {"veg": 70, "fruit": 110}


def is_fresh_produce(name: str, kind: str, kcal_per_100g: float | None = None) -> bool:
    """kind: 'veg' | 'fruit'. Свежий продукт без обработки."""
    n = name.lower()
    if any(kw in n for kw in PRODUCE_EXCLUDE):
        return False
    markers = FRESH_VEG_MARKERS if kind == "veg" else FRESH_FRUIT_MARKERS
    if not any(kw in n for kw in markers):
        return False
    cap = PRODUCE_KCAL_CAP_PER_100G.get(kind)
    if kcal_per_100g is not None and cap and kcal_per_100g > cap:
        return False
    return True


SUSHI_MARKERS = (
    "филадельфия", "philadelphia",
    "нигири", "онигири", "маки", "темпура",
    "суши", "sushi", "унаги", "такояки",
)


def is_sushi(name_lower: str) -> bool:
    """Суши/роллы — не формат здорового обеда/ужина (белый рис, сливочный сыр).
    Сэндвич-роллы (лаваш) — не суши."""
    if any(kw in name_lower for kw in SUSHI_MARKERS):
        return True
    return "ролл" in name_lower and "сэндвич" not in name_lower and "sandwich" not in name_lower


# Перекус — только лёгкое (творог, йогурт, фрукт, овощи, сырники, хумус).
# Не рыба/мясо/супы/тяжёлые основные блюда.
_SNACK_REJECT = (
    "суп", "борщ", "щи", "солянк", "харчо", "уха",
    "рыба", "форел", "лосос", "треск", "горбуш", "скумбри", "тунец",
    "окороч", "бройлер", "котлет", "тефтел", "фрикадел", "биточ",
    "говядин", "свинин", "баранин", "бефстроганов", "гуляш", "плов",
    "паста", "макарон", "лазань", "ризотто", "рагу", "жаркое",
    "сэндвич", "ролл", "бургер", "шаурма", "пицца",
)


def is_valid_snack_dish(item: dict) -> bool:
    name_lower = item.get("name", "").lower()
    if any(kw in name_lower for kw in _SNACK_REJECT):
        print(f"[snack_filter] HEAVY: {item.get('name')}")
        return False
    try:
        kcal100 = float(item["nutrition_variants"][0]["calories"])
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    if kcal100 > 220:  # перекус — лёгкий
        print(f"[snack_filter] DENSE({kcal100}): {item.get('name')}")
        return False
    return True


def is_valid_lunch_dish(item: dict) -> bool:
    name = item.get("name", "?")
    name_lower = name.lower()
    try:
        nv = item["nutrition_variants"][0]
        portion_g = min(item["weight_g"], 400)
        k = portion_g / 100
        protein = float(nv["protein"]) * k
        fat = float(nv["fat"]) * k
        carbs = float(nv["carbohydrates"]) * k
        sugar = float(nv.get("sugar", 0)) * k
    except (KeyError, IndexError, ValueError, TypeError):
        print(f"[lunch_filter] NO_NUTRITION: {name}")
        return False

    if protein < 5:
        print(f"[lunch_filter] LOW_PROTEIN({protein:.1f}g): {name}")
        return False

    if fat > 40:
        print(f"[lunch_filter] HIGH_FAT({fat:.1f}g): {name}")
        return False

    if sugar > 15:
        print(f"[lunch_filter] HIGH_SUGAR({sugar:.1f}g): {name}")
        return False

    if protein > 40 and carbs < 1:
        print(f"[lunch_filter] RAW_MEAT: {name}")
        return False

    MAYO_SALAD_KEYWORDS = (
        "оливье", "под шубой", "столичный",
        "мимоза", "с майонез", "крабовый",
        "с ананас",
        "цезарь", "caesar",  # цезарь-заправка — жирная (майонез/сыр)
        # острые салаты на масле — закуска/приправа, не основа обеда
        "по-корейск", "корейск", "по-китайск", "чука", "кимчи",
        "маринован", "пикантн",
    )
    if any(kw in name_lower for kw in MAYO_SALAD_KEYWORDS):
        print(f"[lunch_filter] MAYO_SALAD: {name}")
        return False

    # жирные хлебные форматы для обеда (клаб-сэндвич/буррито обычно >18г жира)
    try:
        fat_g = float(nv["fat"]) * min(item["weight_g"], 400) / 100
    except (KeyError, ValueError, TypeError):
        fat_g = 0
    if fat_g > 18 and any(kw in name_lower for kw in ("сэндвич", "клаб", "буррито", "шаурма")):
        print(f"[lunch_filter] FATTY_BREAD({fat_g:.0f}g): {name}")
        return False

    # намазки/паштеты/закуски — не основа обеда/ужина (хумус — только перекус)
    SPREAD_KEYWORDS = (
        "хумус", "паштет", "намазка", "икра кабачков", "икра баклажан",
        "тапенад", "соус", "дип ", "конфи ",
    )
    if any(kw in name_lower for kw in SPREAD_KEYWORDS):
        print(f"[lunch_filter] SPREAD: {name}")
        return False

    if any(kw in name_lower for kw in _DESSERT_KEYWORDS):
        print(f"[lunch_filter] DESSERT: {name}")
        return False

    HEAVY_SAUCE_KEYWORDS = (
        "терияки",
        "в кисло-сладком соусе",
        "в устричном соусе",
        "в соусе унаги",
    )
    if any(kw in name_lower for kw in HEAVY_SAUCE_KEYWORDS):
        print(f"[lunch_filter] HEAVY_SAUCE: {name}")
        return False

    # «жарен» ловит все формы (жареный/жареных/обжаренный);
    # «фри» — только отдельным словом, иначе матчит «фрикадели»
    if any(kw in name_lower for kw in ("жарен", "во фритюре")) or re.search(r"\bфри\b", name_lower):
        print(f"[lunch_filter] FRIED: {name}")
        return False

    HEAVY_SOUP_KEYWORDS = (
        "том ям", "том-ям", "кокосов",
        "фо бо", "фо га", "рамен", "карри", "мисо", "харчо",
    )
    if any(kw in name_lower for kw in HEAVY_SOUP_KEYWORDS):
        print(f"[lunch_filter] HEAVY_SOUP: {name}")
        return False

    # крем-супы/сырные/сливочные супы жирные (сливки/сыр) — ж9-10/100г
    if (("крем-суп" in name_lower or "крем суп" in name_lower
         or "суп-пюре" in name_lower or "сырный суп" in name_lower
         or ("суп" in name_lower and ("сливочн" in name_lower or "сырн" in name_lower)))
            and fat > 12):
        print(f"[lunch_filter] CREAMY_SOUP({fat:.1f}g): {name}")
        return False

    if "запеканка" in name_lower and "картофел" in name_lower:
        print(f"[lunch_filter] STARCHY_CASSEROLE: {name}")
        return False

    if is_sushi(name_lower):
        print(f"[lunch_filter] SUSHI: {name}")
        return False

    print(f"[lunch_filter] PASS: {name}")
    return True


def is_ready_meal(name: str) -> bool:
    """Return True if the product name suggests it is a ready-to-eat dish."""
    n = name.lower()
    return (
        any(ind in n for ind in _READY_INDICATORS) or
        any(cw in n for cw in _READY_COOKING_WORDS)
    )


def is_protein_without_garnish(name: str) -> bool:
    n = name.lower()
    if not any(root in n for root in PROTEIN_ALONE_ROOTS):
        return False
    return not any(g in n for g in GARNISH_WORDS)


def apply_preference_filter(items: list, preference: str) -> list:
    pref_lower = preference.lower()
    if any(k in pref_lower for k in POSITIVE_KEYWORDS):
        return items
    for key, banned_roots in EXCLUDE_BY_PREFERENCE.items():
        if key in pref_lower:
            items = [i for i in items if not any(r.lower() in i["name"].lower() for r in banned_roots)]
    return items


def deduplicate_by_category(items: list) -> list:
    seen: set = set()
    result = []
    for item in items:
        name_lower = item["name"].lower()
        category = next((r for r in CATEGORY_ROOTS if r in name_lower), None)
        if category is None:
            category = next(
                (CATEGORY_ALIASES[alias] for alias in CATEGORY_ALIASES if alias in name_lower),
                None,
            )
        if category and category in seen:
            continue
        if category:
            seen.add(category)
        result.append(item)
    return result


# ——— Ingredient checks ———

_BAD_INGREDIENT_KEYWORDS = [
    "гидрогениз", "маргарин", "спред", "кулинарный жир",
    "заменитель молочного жира", "растительный жир частично",
    "е621", "е 621", "e621",
    "е110", "е 110", "e110",
    "е122", "е 122", "e122",
    "е124", "е 124", "e124",
    "е129", "е 129", "e129",
]


def parse_ingredients(properties: list) -> str:
    """Extract ingredient list text from product properties."""
    for prop in properties:
        name = prop.get("name", "").lower()
        if "состав" in name or "ингредиент" in name:
            return prop.get("value", "")
    return ""


def has_bad_ingredients(ingredients_text: str) -> bool:
    """Return True if ingredients contain trans fats or banned additives."""
    text = ingredients_text.lower()
    return any(kw in text for kw in _BAD_INGREDIENT_KEYWORDS)


# ——— Nutrition parsing ———

def parse_nutrition(properties: list) -> list:
    for prop in properties:
        if "Пищевая" in prop.get("name", ""):
            value = prop.get("value", "")
            blocks = re.split(r'(?<=ккал)[\.;\s]*', value)
            results = []
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                protein = re.search(r"белки\s*([\d,\.]+)", block)
                fat = re.search(r"жиры\s*([\d,\.]+)", block)
                carbs = re.search(r"углеводы\s*([\d,\.]+)", block)
                calories = re.search(r"([\d,\.]+)\s*ккал", block)
                if not (protein and calories):
                    continue
                producer_match = re.match(r"^(.+?)(?=белки)", block)
                producer = producer_match.group(1).strip(" :;") if producer_match else ""
                if len(producer) < 3:
                    producer = ""
                results.append({
                    "producer": producer,
                    "protein": protein.group(1).replace(",", "."),
                    "fat": fat.group(1).replace(",", ".") if fat else "0",
                    "carbohydrates": carbs.group(1).replace(",", ".") if carbs else "0",
                    "calories": calories.group(1).replace(",", "."),
                })
            return results if results else []
    return []


# ——— Portion calculations ———

def calc_portion(item: dict, K: float, n_items: int = 1) -> float:
    nv = item.get("nutrition_variants", [])
    wg = item.get("weight_g", 0)
    if not nv or wg == 0:
        return float(wg)
    try:
        cal_per_100 = float(nv[0]["calories"])
        cal_total = cal_per_100 * wg / 100
        if cal_total <= 0:
            return float(wg)
        k_share = K / max(n_items, 1)
        ratio = min(k_share / cal_total, 1.0)
        if ratio <= 0.75:
            ratio = 0.5
        return float(min(round(ratio * wg), wg))
    except (ValueError, TypeError):
        return float(wg)


def portion_hint(needed_g: float, total_g: float) -> str:
    if total_g == 0:
        return ""
    ratio = needed_g / total_g
    if ratio >= 0.85:
        return "вся порция"
    elif ratio >= 0.6:
        return "примерно 2/3 порции"
    elif ratio >= 0.4:
        return "примерно половина порции"
    elif ratio >= 0.28:
        return "примерно 1/3 порции"
    else:
        return "примерно 1/4 порции"


def format_item_dict(item: dict, needed_g: float) -> dict:
    """Return item as dict suitable for JSON response."""
    nv = item.get("nutrition_variants", [])
    result = {
        "id": item["id"],
        "xml_id": item["xml_id"],
        "name": item["name"],
        "url": item["url"],
        "weight_g": item.get("weight_g", 0),
        "needed_g": int(needed_g),
        "portion_hint": portion_hint(needed_g, item.get("weight_g", 0)),
        "image_url": item.get("image_url", ""),
        "price": item.get("price", None),
        "nutrition": None,
    }
    if nv and needed_g > 0:
        try:
            n = nv[0]
            k = needed_g / 100
            result["nutrition"] = {
                "protein": round(float(n["protein"]) * k, 1),
                "fat": round(float(n["fat"]) * k, 1),
                "carbohydrates": round(float(n["carbohydrates"]) * k, 1),
                "calories": round(float(n["calories"]) * k, 1),
                "per_100g": {
                    "protein": float(n["protein"]),
                    "fat": float(n["fat"]),
                    "carbohydrates": float(n["carbohydrates"]),
                    "calories": float(n["calories"]),
                }
            }
        except (ValueError, TypeError):
            pass
    return result


# ——— Main fetch pipeline ———

def get_query_pool(preference: str | None) -> list:
    if not preference:
        return ALL_QUERIES
    pref_lower = preference.lower()
    if any(k in pref_lower for k in VEGETARIAN_KEYS):
        return VEGETARIAN_QUERIES
    if any(k in pref_lower for k in NO_MEAT_KEYS):
        return NO_MEAT_QUERIES
    return ALL_QUERIES


async def fetch_enriched_items(
    queries: list,
    preference: str | None,
    disliked_ids: set | None = None,
    liked_ids: set | None = None,
    meal_type: str | None = None,
    max_candidates: int = 35,
    produce_kind: str | None = None,  # 'veg' | 'fruit': свежие продукты вместо готовых блюд
) -> list:
    if disliked_ids is None:
        disliked_ids = set()
    if liked_ids is None:
        liked_ids = set()

    # For lunch: randomise sort/page so each search returns different products
    _LUNCH_SORTS = ["popularity", "rating", "new"]
    def _search_params(meal_t):
        if meal_t == "lunch":
            sort = random.choice(_LUNCH_SORTS)
            page = random.choices([1, 2], weights=[6, 4])[0]
            return {"sort": sort, "page": page}
        return {"sort": "popularity", "page": 1}

    search_results = []
    search_batch_size = 5
    for i in range(0, len(queries), search_batch_size):
        batch = queries[i:i + search_batch_size]
        batch_results = await asyncio.gather(*[
            search_vkusvill(q, **_search_params(meal_type)) for q in batch
        ])
        search_results.extend(batch_results)
        if i + search_batch_size < len(queries):
            await asyncio.sleep(0.5)
    all_items = [item for sublist in search_results for item in sublist]

    unique: dict = {}
    for item in all_items:
        if item["id"] not in unique:
            unique[item["id"]] = item
    all_unique = list(unique.values())
    not_disliked = [i for i in all_unique if str(i.get("xml_id", "")) not in disliked_ids]
    candidates = random.sample(not_disliked, min(max_candidates, len(not_disliked)))

    # Pause after search phase to let MCP server recover before detail requests
    await asyncio.sleep(1.0)

    # Sequential detail fetching — avoids overloading MCP; DB cache makes this fast after first run
    details_list = []
    for item in candidates:
        details = await get_product_details(item["id"])
        details_list.append(details)
        await asyncio.sleep(0.1)

    enriched = []
    for item, details in zip(candidates, details_list):
        clean_name = re.sub(r"&[a-zA-Z]+;", " ", item["name"]).strip()

        properties = details.get("properties", [])
        nutrition_variants = parse_nutrition(properties)
        weight = details.get("weight", {})
        weight_g = int(weight.get("value", 0) * 1000) if weight else 0

        if not nutrition_variants or weight_g == 0:
            print(f"[fetch_filter] NO_NUTRITION: {clean_name}")
            continue

        ingredients_text = parse_ingredients(properties)
        if ingredients_text and has_bad_ingredients(ingredients_text):
            print(f"[fetch_filter] BAD_INGREDIENTS: {clean_name}")
            continue

        if any(kw.lower() in clean_name.lower() for kw in BAD_KEYWORDS):
            print(f"[fetch_filter] BAD_KEYWORD: {clean_name}")
            continue
        if is_standalone_cheese(clean_name) or is_standalone_sauce(clean_name):
            print(f"[fetch_filter] CHEESE_SAUCE: {clean_name}")
            continue
        if is_bulk_by_name(clean_name):
            print(f"[fetch_filter] BULK: {clean_name}")
            continue
        if produce_kind:
            # свежие фрукты/овощи: белый список вместо проверки «готовое блюдо»
            try:
                kcal100 = float(nutrition_variants[0]["calories"])
            except (KeyError, IndexError, ValueError, TypeError):
                kcal100 = None
            if not is_fresh_produce(clean_name, produce_kind, kcal100):
                print(f"[fetch_filter] NOT_PRODUCE: {clean_name}")
                continue
        else:
            if is_raw_meat(clean_name) or is_protein_without_garnish(clean_name):
                print(f"[fetch_filter] RAW_MEAT: {clean_name}")
                continue
            if not is_ready_meal(clean_name):
                print(f"[fetch_filter] NOT_READY: {clean_name}")
                continue

        print(f"[fetch_filter] PASS: {clean_name}")

        images = item.get("images") or details.get("images") or []
        image_url = images[0]["medium"] if images and "medium" in images[0] else ""

        raw_price = item.get("price") or details.get("price")
        price = raw_price["current"] if isinstance(raw_price, dict) else raw_price

        enriched.append({
            "id": item["id"],
            "xml_id": item["xml_id"],
            "name": clean_name,
            "url": item["url"],
            "image_url": image_url,
            "price": price,
            "nutrition_variants": nutrition_variants,
            "weight_g": weight_g,
        })

    print(f"[fetch] После базовой фильтрации: {len(enriched)} блюд (meal_type={meal_type})")

    if preference:
        before = len(enriched)
        enriched = apply_preference_filter(enriched, preference)
        print(f"[fetch] После preference-фильтра ({preference!r}): {len(enriched)} блюд (было {before})")

    if meal_type == "breakfast":
        before = len(enriched)
        enriched = [item for item in enriched if is_valid_breakfast_dish(item)]
        print(f"[fetch] После is_valid_breakfast_dish: {len(enriched)} блюд (было {before})")
    elif meal_type in ("lunch", "dinner"):
        before = len(enriched)
        enriched = [item for item in enriched if is_valid_lunch_dish(item)]
        print(f"[fetch] После is_valid_lunch_dish ({meal_type}): {len(enriched)} блюд (было {before})")
    elif meal_type == "snack":
        before = len(enriched)
        enriched = [item for item in enriched if is_valid_snack_dish(item)]
        print(f"[fetch] После is_valid_snack_dish: {len(enriched)} блюд (было {before})")

    # Стоп-лист по типу приёма применяется ВСЕГДА (раньше из-за elif
    # пропускался для завтрака/обеда — фрикадели проходили на завтрак)
    if meal_type and meal_type in MEAL_TYPE_EXCLUDE:
        before = len(enriched)
        banned = MEAL_TYPE_EXCLUDE[meal_type]
        enriched = [
            item for item in enriched
            if not any(kw in item["name"].lower() for kw in banned)
        ]
        print(f"[fetch] После meal_type_exclude ({meal_type}): {len(enriched)} блюд (было {before})")

    return enriched
