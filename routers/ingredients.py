import os
import json
import asyncio
import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])

_PHOTOS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "data", "ingredient_photos.json")
_INGREDIENTS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "data", "ingredients.json")
_fetch_lock = asyncio.Lock()


def _load_cache() -> dict:
    try:
        with open(_PHOTOS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_PHOTOS_PATH, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


async def _fetch_one(client: httpx.AsyncClient, iid: str, query: str, key: str) -> tuple[str, str]:
    try:
        r = await client.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 1, "orientation": "squarish", "content_filter": "high"},
            headers={"Authorization": f"Client-ID {key}"},
            timeout=10,
        )
        results = r.json().get("results", [])
        if results:
            return iid, results[0]["urls"]["small"]
    except Exception:
        pass
    return iid, ""


@router.get("/photos")
async def get_photos():
    key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not key:
        return {}

    with open(_INGREDIENTS_PATH) as f:
        ingredients = json.load(f)

    cache = _load_cache()
    missing = [(i["id"], i["photo"]) for i in ingredients if not cache.get(i["id"])]

    if missing:
        async with _fetch_lock:
            cache = _load_cache()
            missing = [(i["id"], i["photo"]) for i in ingredients if not cache.get(i["id"])]
            if missing:
                batch = missing[:50]
                async with httpx.AsyncClient() as client:
                    results = []
                    for iid, q in batch:
                        result = await _fetch_one(client, iid, q, key)
                        results.append(result)
                        await asyncio.sleep(0.3)
                for iid, url in results:
                    if url:
                        cache[iid] = url
                _save_cache(cache)

    return cache
