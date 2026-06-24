"""ВкусВилл MCP: поиск сырых ингредиентов и сборка корзины.

В отличие от приложения готовых блюд, тут ищем СЫРЫЕ продукты для рецептов
(куриное филе, гречка, брокколи), а не готовые блюда.
"""
import asyncio
import json
import logging

import httpx

MCP_URL = "https://mcp001.vkusvill.ru/mcp"

# нормализация названий ингредиентов из рецептов → поисковый запрос ВкусВилл
_QUERY_MAP = {
    "куриное филе": "филе куриное", "куриная грудка": "грудка куриная",
    "куриный фарш": "фарш куриный", "фарш куриный": "фарш куриный",
    "говяжий фарш": "фарш говяжий", "соевый фарш": "соевый фарш",
    "филе индейки": "филе индейки", "грудка индейки": "филе индейки",
    "филе трески": "треска", "филе лосося": "лосось", "сёмга": "сёмга", "семга": "сёмга",
    "форель": "форель", "креветки": "креветки", "тунец": "тунец консервированный",
    "консервированный тунец": "тунец консервированный",
    "яйцо": "яйца куриные", "яйца": "яйца куриные", "яичный белок": "яйца куриные",
    "творог 5%": "творог 5", "творог 2%": "творог 2", "творог": "творог",
    "греческий йогурт": "йогурт греческий", "йогурт": "йогурт натуральный",
    "молоко": "молоко", "кокосовое молоко": "молоко кокосовое", "соевое молоко": "молоко соевое",
    "сыр лёгкий": "сыр", "сыр": "сыр", "творожный сыр": "сыр творожный", "фета": "сыр фета",
    "гречка": "гречка", "гречневая крупа": "гречка", "бурый рис": "рис бурый", "рис": "рис",
    "киноа": "киноа", "булгур": "булгур", "кускус": "кускус", "перловка": "перловая крупа",
    "овсянка": "овсяные хлопья", "геркулес": "овсяные хлопья", "овсяные хлопья": "овсяные хлопья",
    "паста": "макароны", "макароны": "макароны", "лаваш": "лаваш", "тортилья": "тортилья",
    "хлебцы": "хлебцы", "мука": "мука", "овсяная мука": "мука овсяная", "рисовая мука": "мука рисовая",
    "чечевица": "чечевица", "нут": "нут", "фасоль": "фасоль", "зелёный горошек": "горошек зелёный",
    "тофу": "тофу", "соевое мясо": "соевое мясо",
    "брокколи": "брокколи", "цветная капуста": "капуста цветная", "шпинат": "шпинат",
    "помидор": "помидоры", "томаты": "помидоры", "черри": "помидоры черри",
    "огурец": "огурцы", "кабачок": "кабачок", "цукини": "цукини", "баклажан": "баклажан",
    "болгарский перец": "перец болгарский", "морковь": "морковь", "лук": "лук репчатый",
    "репчатый лук": "лук репчатый", "чеснок": "чеснок", "свёкла": "свёкла", "свекла": "свёкла",
    "руккола": "руккола", "салат": "салат", "грибы": "шампиньоны", "шампиньоны": "шампиньоны",
    "стручковая фасоль": "фасоль стручковая",
    "авокадо": "авокадо", "оливковое масло": "масло оливковое", "масло растительное": "масло подсолнечное",
    "орехи": "орехи", "миндаль": "миндаль", "семена тыквы": "семена тыквы", "семена чиа": "семена чиа",
    "банан": "бананы", "яблоко": "яблоки", "клубника": "клубника", "малина": "малина",
    "черника": "черника", "голубика": "голубика", "ягоды": "ягоды замороженные",
    "курага": "курага", "изюм": "изюм", "мёд": "мёд", "арахисовая паста": "паста арахисовая",
}

# что не кладём в корзину (специи/мелочь по вкусу)
_SKIP = ("соль", "перец", "специи", "ванилин", "разрыхлитель", "подсластитель", "сахзам",
         "вода", "по вкусу", "корица", "паприка", "орегано", "базилик", "зелень", "укроп",
         "петрушка", "кинза", "мята", "лимонный сок", "сок лимона", "цедра", "горчица",
         "соевый соус", "уксус", "соус песто", "мускатный орех", "гвоздика", "какао")


async def _mcp(params: dict, req_id: int) -> dict:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
                r = await client.post(MCP_URL, json={"jsonrpc": "2.0", "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "recipes", "version": "1.0"}, "capabilities": {}}, "id": 1},
                    headers=headers, timeout=30)
                sid = r.headers.get("mcp-session-id")
                nh = {**headers, "Mcp-Session-Id": sid} if sid else headers
                await client.post(MCP_URL, json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, headers=nh, timeout=30)
                r2 = await client.post(MCP_URL, json={"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": req_id}, headers=nh, timeout=30)
                res = r2.json()
                try:
                    await client.delete(MCP_URL, headers=nh, timeout=10)
                except Exception:
                    pass
                return res
        except Exception:
            if attempt < 2:
                await asyncio.sleep(1)
    return {}


async def search(query: str) -> list:
    res = await _mcp({"name": "vkusvill_products_search", "arguments": {"q": query, "sort": "popularity", "page": 1}}, 2)
    try:
        data = json.loads(res["result"]["content"][0]["text"])
        return data["data"]["items"] if data.get("ok") else []
    except Exception:
        return []


async def create_cart(xml_ids: list) -> str:
    products = [{"xml_id": x, "q": 1} for x in xml_ids]
    res = await _mcp({"name": "vkusvill_cart_link_create", "arguments": {"products": products}}, 4)
    try:
        data = json.loads(res["result"]["content"][0]["text"])
        d = data.get("data", {}) or {}
        return d.get("url") or d.get("link") or data.get("url") or data.get("link") or ""
    except Exception as e:
        logging.warning(f"create_cart error: {e!r}")
        return ""


def _query_for(name: str) -> str | None:
    n = name.lower()
    if any(s in n for s in _SKIP):
        return None
    for key in sorted(_QUERY_MAP, key=len, reverse=True):
        if key in n:
            return _QUERY_MAP[key]
    # запасной вариант — первое слово названия
    return n.split(",")[0].split("(")[0].strip()[:30] or None


async def match_ingredients(names: list) -> dict:
    """Имена ингредиентов → {found:[{name,product,xml_id,price}], not_found:[...]}."""
    found, not_found, seen_q = [], [], set()
    for name in names:
        q = _query_for(name)
        if not q or q in seen_q:
            continue
        seen_q.add(q)
        items = await search(q)
        if not items:
            not_found.append(name)
            continue
        it = items[0]
        found.append({"name": name, "query": q, "product": it.get("name"),
                      "xml_id": it.get("xml_id"), "price": it.get("price")})
        await asyncio.sleep(0.2)
    return {"found": found, "not_found": not_found}
