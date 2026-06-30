"""B2C self-serve — человек без тренера сам собирает себе рацион."""
import os
import random
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt

from database import get_db
from models import SelfServeStore, User
from auth import hash_password, verify_password, create_token, SECRET_KEY, ALGORITHM
from services.week_planner import generate_week
from services.food_groups import coverage_report
from services.vkusvill import (
    create_cart, fetch_enriched_items, format_item_dict, MEAL_TYPE_QUERIES,
)
from services.gpt import run_gpt_selection
from services.inbody import extract_inbody, kbju_from_inbody

router = APIRouter(prefix="/api/self-serve", tags=["self-serve"])


def _tg_id(tg_user: dict):
    try:
        return int(tg_user["id"]) if tg_user and tg_user.get("id") else None
    except (ValueError, TypeError, KeyError):
        return None


def _uid_from_token(token: str):
    if not token:
        return None
    try:
        return int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])
    except Exception:
        return None


def _user_key(token: str, tg_user: dict):
    uid = _uid_from_token(token)
    if uid:
        return f"u{uid}"
    tid = _tg_id(tg_user)
    if tid:
        return f"tg{tid}"
    return None


ACTIVITY = {
    "sedentary": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725, "very_high": 1.9,
}


def _bmr(sex: str, weight: float, height: float, age: int) -> float:
    if sex == "male":
        return 66.5 + 13.75 * weight + 5.003 * height - 6.755 * age
    return 655 + 9.6 * weight + 1.8 * height - 4.7 * age


class KbjuBody(BaseModel):
    sex: str = "female"
    weight: float = 60
    height: float = 165
    age: int = 30
    activity: str = "moderate"
    goal: str = "loss"


@router.post("/kbju")
async def selfserve_kbju(b: KbjuBody):
    bmr = _bmr(b.sex, b.weight, b.height, b.age)
    norm = bmr * ACTIVITY.get(b.activity, 1.55)
    kcal = norm * (0.85 if b.goal == "loss" else 1.10 if b.goal == "gain" else 1.0)
    protein = round(b.weight * 1.8)
    fat = round(b.weight * 1.0)
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {
        "kcal": round(kcal), "protein": protein, "fat": fat, "carbs": carbs,
        "bmr": round(bmr), "maintenance": round(norm),
    }


class InbodyKbjuBody(BaseModel):
    weight: float = 0
    body_fat_pct: float | None = None
    muscle_mass: float | None = None
    bmr: float | None = None
    activity: str = "moderate"
    goal: str = "loss"


@router.post("/inbody")
async def api_inbody(file: UploadFile = File(...)):
    data = await file.read()
    return {"fields": await extract_inbody(data)}


@router.post("/inbody/kbju")
async def api_inbody_kbju(b: InbodyKbjuBody):
    return kbju_from_inbody(b.weight, b.body_fat_pct, b.muscle_mass, b.bmr, b.activity, b.goal)


class WeekBody(BaseModel):
    kcal: float = 1950
    protein: float = 150
    fat: float = 65
    carbs: float = 180
    meal_count: int = 3
    restrictions: str | None = None
    days_count: int = 7


def _scale_dish(dish, f):
    n = dish.get("nutrition")
    if n:
        for k in ("protein", "fat", "carbohydrates", "calories"):
            if isinstance(n.get(k), (int, float)):
                n[k] = round(n[k] * f, 1)
    if isinstance(dish.get("needed_g"), (int, float)):
        dish["needed_g"] = int(round(dish["needed_g"] * f))
    if isinstance(dish.get("portion"), (int, float)):
        dish["portion"] = round(dish["portion"] * f, 2)


_FRIENDLY = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0]
_FRIENDLY_LABEL = {0.25: "¼ упаковки", 0.33: "⅓ упаковки", 0.5: "половина упаковки",
                   0.67: "⅔ упаковки", 0.75: "¾ упаковки", 1.0: "вся упаковка"}


def _set_portion(dish, new_p):
    cur = dish.get("portion") or 1.0
    if cur > 0:
        _scale_dish(dish, new_p / cur)
    dish["portion"] = new_p
    dish["portion_label"] = _FRIENDLY_LABEL.get(new_p, "вся упаковка")


def _fit_day(meals, target_kcal):
    dishes = [d for m in meals for d in m.get("dishes", [])
              if (d.get("nutrition") or {}).get("calories") and not d.get("fixed_portion")]
    if not dishes or target_kcal <= 0:
        return
    units = [d["nutrition"]["calories"] / (d.get("portion") or 1.0) for d in dishes]
    cur_total = sum(d["nutrition"]["calories"] for d in dishes)
    f = max(0.4, min(1.0, target_kcal / cur_total)) if cur_total else 1.0
    choice = [min(_FRIENDLY, key=lambda fr: abs(fr - (d.get("portion") or 1.0) * f))
              for d in dishes]

    def day_total():
        return sum(units[i] * choice[i] for i in range(len(dishes)))

    for _ in range(60):
        err = day_total() - target_kcal
        if abs(err) <= 80:
            break
        best = None
        for i in range(len(dishes)):
            ci = _FRIENDLY.index(choice[i])
            for nj in (ci - 1, ci + 1):
                if 0 <= nj < len(_FRIENDLY):
                    ne = abs(err + units[i] * (_FRIENDLY[nj] - choice[i]))
                    if best is None or ne < best[0]:
                        best = (ne, i, _FRIENDLY[nj])
        if best is None or best[0] >= abs(err):
            break
        choice[best[1]] = best[2]

    for i, d in enumerate(dishes):
        _set_portion(d, choice[i])


def _wide_meal_queries(meal_type: str) -> list[str]:
    return list(dict.fromkeys(MEAL_TYPE_QUERIES.get(meal_type, MEAL_TYPE_QUERIES["lunch"])))


_VARIETY_MARKERS = (
    ("творог", "творожное"), ("творож", "творожное"), ("сырник", "творожное"),
    ("запекан", "творожное"), ("йогурт", "творожное"),
    ("омлет", "яйца"), ("яйц", "яйца"), ("скрэмбл", "яйца"),
    ("куриц", "курица"), ("курин", "курица"), ("цыпл", "курица"),
    ("индейк", "индейка"), ("говядин", "говядина"), ("свинин", "свинина"),
    ("лосос", "рыба"), ("семг", "рыба"), ("сёмг", "рыба"), ("форел", "рыба"),
    ("треск", "рыба"), ("тунец", "рыба"), ("тунц", "рыба"), ("рыб", "рыба"),
    ("кревет", "морепродукты"), ("кальмар", "морепродукты"),
    ("фасол", "бобовые"), ("нут", "бобовые"), ("чечев", "бобовые"), ("фалафель", "бобовые"),
    ("суп", "суп"), ("борщ", "суп"), ("щи", "суп"), ("солян", "суп"),
    ("салат", "салат"), ("боул", "боул"), ("поке", "боул"),
    ("паста", "паста"), ("макарон", "паста"), ("спагет", "паста"), ("лапш", "паста"),
    ("рис", "рис"), ("плов", "рис"), ("греч", "гречка"), ("булгур", "булгур"),
    ("киноа", "киноа"), ("картоф", "картофель"), ("пюре", "картофель"),
    ("сэндвич", "сэндвич"), ("ролл", "ролл"), ("лаваш", "ролл"),
)

_VARIETY_LIMITS = {
    "творожное": 2, "яйца": 2, "курица": 3, "индейка": 2, "говядина": 2,
    "рыба": 3, "морепродукты": 2, "бобовые": 2, "суп": 2, "салат": 3,
    "боул": 2, "паста": 2, "рис": 2, "гречка": 2, "булгур": 2,
    "киноа": 2, "картофель": 2, "сэндвич": 2, "ролл": 2,
}


def _dish_name_key(name: str) -> str:
    return " ".join((name or "").lower().replace("ё", "е").split())


def _dish_variety_key(name: str) -> str:
    n = _dish_name_key(name)
    for marker, key in _VARIETY_MARKERS:
        if marker in n:
            return key
    return n.split(",")[0][:32] if n else "другое"


def _meal_type_key(meal: dict) -> str:
    raw = (meal.get("meal_type") or meal.get("meal_label") or "lunch").lower()
    return _MEAL_TYPE_MAP.get(raw, raw if raw in MEAL_TYPE_QUERIES else "lunch")


def _dish_kbju(dish: dict) -> dict:
    n = dish.get("nutrition") or {}
    return {
        "kcal": float(n.get("calories") or n.get("kcal") or 450),
        "protein": float(n.get("protein") or 25),
        "fat": float(n.get("fat") or 15),
        "carbs": float(n.get("carbohydrates") or n.get("carbs") or 45),
    }


def _enriched_full_kcal(item: dict) -> float:
    try:
        n = item["nutrition_variants"][0]
        return float(n["calories"]) * float(item.get("weight_g") or 100) / 100
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0


async def _find_variety_replacement(meal: dict, dish: dict, restrictions: str | None, exclude_xml_ids: set[str]):
    meal_type = _meal_type_key(meal)
    selected_queries = _wide_meal_queries(meal_type)
    target = _dish_kbju(dish)
    enriched = await fetch_enriched_items(
        queries=selected_queries,
        preference=restrictions or None,
        meal_type=meal_type,
        max_candidates=80,
    )
    old_key = _dish_variety_key(dish.get("name", ""))
    candidates = [
        item for item in enriched
        if str(item.get("xml_id", "")) not in exclude_xml_ids
        and _dish_variety_key(item.get("name", "")) != old_key
    ]
    if not candidates:
        return None
    selected = await run_gpt_selection(
        enriched_items=candidates,
        P=target["protein"], F=target["fat"], C=target["carbs"], K=target["kcal"],
        preference=restrictions or None,
        count=min(6, len(candidates)),
        meal_label=meal.get("meal_label") or meal_type,
    )
    pool = selected or candidates
    item = min(pool, key=lambda it: abs(_enriched_full_kcal(it) - target["kcal"]))
    return format_item_dict(item, item.get("weight_g", 0))


async def improve_week_variety(days: list, restrictions: str | None = None, max_replacements: int = 6) -> list:
    """Мягкий редактор недели: заменяет очевидные повторы после первичного подбора."""
    used_xml_ids = {
        str(dish.get("xml_id"))
        for day in days for meal in day.get("meals", []) for dish in meal.get("dishes", [])
        if dish.get("xml_id")
    }
    exact_seen: set[str] = set()
    category_counts: dict[str, int] = {}
    replacements = 0

    for day in days:
        for meal in day.get("meals", []):
            for idx, dish in enumerate(meal.get("dishes", [])):
                name_key = _dish_name_key(dish.get("name", ""))
                category = _dish_variety_key(dish.get("name", ""))
                category_count = category_counts.get(category, 0)
                category_limit = _VARIETY_LIMITS.get(category, 2)
                should_replace = name_key in exact_seen or category_count >= category_limit
                if should_replace and replacements < max_replacements and not dish.get("carryover"):
                    try:
                        repl = await _find_variety_replacement(meal, dish, restrictions, used_xml_ids)
                    except Exception as e:
                        print(f"[variety] replacement failed: {e!r}")
                        repl = None
                    if repl:
                        meal["dishes"][idx] = repl
                        dish = repl
                        replacements += 1
                        used_xml_ids.add(str(repl.get("xml_id")))
                        name_key = _dish_name_key(repl.get("name", ""))
                        category = _dish_variety_key(repl.get("name", ""))
                exact_seen.add(name_key)
                category_counts[category] = category_counts.get(category, 0) + 1

    if replacements:
        print(f"[variety] replacements: {replacements}")
    return days


@router.post("/week")
async def selfserve_week(b: WeekBody):
    try:
        days = await generate_week(
            P=b.protein or 0, F=b.fat or 0, C=b.carbs or 0, K=b.kcal,
            restrictions=(b.restrictions or None),
            meal_count=max(2, min(5, b.meal_count)),
            start=date.today(),
            days_count=max(1, min(7, b.days_count)),
        )
        for day in days:
            _fit_day(day.get("meals", []), b.kcal)
        days = await improve_week_variety(days, b.restrictions or None)
        for day in days:
            _fit_day(day.get("meals", []), b.kcal)
        if not any(meal.get("dishes") for day in days for meal in day.get("meals", [])):
            raise HTTPException(404, "Не удалось найти подходящие блюда")
        return {"days": days, "coverage": coverage_report(days)}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[selfserve_week] failed: {e!r}")
        raise HTTPException(502, "Не удалось подобрать питание. Попробуйте ещё раз")


class CartBody(BaseModel):
    days: list = []
    xml_ids: list = []


@router.post("/cart")
async def selfserve_cart(b: CartBody):
    xml_ids, seen = [], set()
    if b.xml_ids:
        for x in b.xml_ids:
            if x and str(x) not in seen:
                seen.add(str(x))
                xml_ids.append(x)
    else:
        for day in (b.days or []):
            for meal in day.get("meals", []):
                for dish in meal.get("dishes", []):
                    xid = dish.get("xml_id")
                    if xid and dish.get("in_cart", True) and str(xid) not in seen:
                        seen.add(str(xid))
                        xml_ids.append(xid)
    url = await create_cart(xml_ids) if xml_ids else ""
    return {"cart_url": url, "count": len(xml_ids)}


class ReplaceDishBody(BaseModel):
    meal_label: str
    meal_type: str = "lunch"
    kbju: dict = {}
    restrictions: str | None = None
    exclude_xml_ids: list = []


_MEAL_TYPE_MAP = {
    "завтрак": "breakfast",
    "обед": "lunch",
    "обед / ужин": "lunch",
    "ужин": "dinner",
    "перекус": "snack",
    "перекус 1": "snack",
    "перекус 2": "snack",
}


def _item_full_kcal(item: dict) -> float:
    nutr = item.get("nutrition_per_100g") or {}
    kcal_100 = nutr.get("calories", 0) or 0
    weight = item.get("weight_g") or 100
    return float(kcal_100) * float(weight) / 100


@router.post("/replace-dish")
async def selfserve_replace_dish(b: ReplaceDishBody):
    meal_type_key = _MEAL_TYPE_MAP.get((b.meal_type or "").lower(), b.meal_type or "lunch")
    if meal_type_key not in MEAL_TYPE_QUERIES:
        meal_type_key = "lunch"

    selected_queries = _wide_meal_queries(meal_type_key)
    target = b.kbju or {}
    kcal = float(target.get("kcal") or target.get("calories") or 500)
    protein = float(target.get("protein") or 30)
    fat = float(target.get("fat") or 15)
    carbs = float(target.get("carbs") or target.get("carbohydrates") or 50)
    exclude_set = {str(x) for x in (b.exclude_xml_ids or [])}

    enriched = await fetch_enriched_items(
        queries=selected_queries,
        preference=b.restrictions or None,
        meal_type=b.meal_type,
        max_candidates=80,
    )
    enriched = [i for i in enriched if str(i.get("xml_id", "")) not in exclude_set]
    if not enriched:
        raise HTTPException(404, "Не удалось найти замену, попробуйте ещё раз")

    selected = await run_gpt_selection(
        enriched_items=enriched,
        P=protein, F=fat, C=carbs, K=kcal,
        preference=b.restrictions or None,
        count=5,
        meal_label=b.meal_label,
    )
    if not selected:
        raise HTTPException(404, "Не удалось найти замену, попробуйте ещё раз")

    item = min(selected, key=lambda it: abs(_item_full_kcal(it) - kcal))
    return {"dish": format_item_dict(item, item.get("weight_g", 0))}


class RegisterBody(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


class ForgotPasswordBody(BaseModel):
    email: str
    return_url: str = ""


class ResetPasswordBody(BaseModel):
    token: str
    password: str


def _reset_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "purpose": "password_reset",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=2)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _send_password_reset_email(email: str, reset_link: str) -> bool:
    host = os.getenv("SMTP_HOST")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")
    if not host or not password or not sender:
        return False

    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER") or sender
    msg = EmailMessage()
    msg["Subject"] = "Восстановление пароля Журка"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "Здравствуйте\n\n"
        "Чтобы восстановить пароль в Журке, откройте ссылку:\n"
        f"{reset_link}\n\n"
        "Ссылка действует 2 часа\n"
    )

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(msg)
    return True


@router.post("/register")
async def selfserve_register(b: RegisterBody, db: AsyncSession = Depends(get_db)):
    email = (b.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Введите корректную почту")
    if len(b.password) < 6:
        raise HTTPException(400, "Пароль не короче 6 символов")
    exists = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "Эта почта уже зарегистрирована — войдите")
    user = User(email=email, name=(b.name.strip() or email.split("@")[0]),
                hashed_password=hash_password(b.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"token": create_token(user.id), "name": user.name, "email": user.email}


@router.post("/login")
async def selfserve_login(b: LoginBody, db: AsyncSession = Depends(get_db)):
    email = (b.email or "").strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(b.password, user.hashed_password):
        raise HTTPException(401, "Неверная почта или пароль")
    return {"token": create_token(user.id), "name": user.name, "email": user.email}


@router.post("/forgot-password")
async def selfserve_forgot_password(b: ForgotPasswordBody, db: AsyncSession = Depends(get_db)):
    email = (b.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Введите корректную почту")

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    sent = False
    if user:
        base_url = (b.return_url or os.getenv("PASSWORD_RESET_URL") or os.getenv("PUBLIC_BASE_URL") or "https://zhurka-pitanie.ru/meal-plan").strip()
        sep = "&" if "?" in base_url else "?"
        reset_link = f"{base_url}{sep}{urlencode({'reset_token': _reset_token(user)})}"
        sent = _send_password_reset_email(email, reset_link)
    return {"ok": True, "sent": sent}


@router.post("/reset-password")
async def selfserve_reset_password(b: ResetPasswordBody, db: AsyncSession = Depends(get_db)):
    if len(b.password) < 6:
        raise HTTPException(400, "Пароль не короче 6 символов")
    try:
        payload = jwt.decode(b.token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(400, "Ссылка недействительна или устарела")
    if payload.get("purpose") != "password_reset":
        raise HTTPException(400, "Ссылка недействительна")
    user_id = int(payload.get("sub"))
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    user.hashed_password = hash_password(b.password)
    await db.commit()
    return {"ok": True}


class AuthBody(BaseModel):
    tg_user: dict = {}
    token: str = ""


class SaveProfileBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    data: dict = {}


class SavePlanBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    plan: dict = {}


class GetPlanBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    id: int = 0


@router.get("/config")
async def selfserve_config():
    return {"bot_username": os.getenv("BOT_USERNAME", "")}


async def _get_store(db: AsyncSession, key: str):
    return (await db.execute(
        select(SelfServeStore).where(SelfServeStore.user_key == key)
    )).scalar_one_or_none()


def _disp_name(b):
    return b.tg_user.get("first_name") if b.tg_user else None


@router.post("/profile/get")
async def profile_get(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "profile": None}
    user = None
    uid = _uid_from_token(b.token)
    if uid:
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    s = await _get_store(db, key)
    return {"authorized": True,
            "name": (user.name if user else (s.name if s else _disp_name(b))),
            "email": (user.email if user else None),
            "profile": (s.profile if s else None)}


@router.post("/profile/save")
async def profile_save(b: SaveProfileBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    s = await _get_store(db, key)
    if s:
        s.profile = b.data
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), profile=b.data, plans=[]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/save")
async def plan_save(b: SavePlanBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    now = datetime.now()
    entry = {"id": int(now.timestamp()), "created_at": now.strftime("%Y-%m-%d %H:%M"),
             "target": (b.plan or {}).get("target", {}), "plan": b.plan}
    s = await _get_store(db, key)
    if s:
        plans = list(s.plans or [])
        plans.insert(0, entry)
        s.plans = plans[:30]
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), profile=None, plans=[entry]))
    await db.commit()
    return {"ok": True}


@router.post("/plan/history")
async def plan_history(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "plans": []}
    s = await _get_store(db, key)
    out = [{"id": p.get("id"), "created_at": p.get("created_at"), "target": p.get("target", {})}
           for p in (s.plans if s and s.plans else [])]
    return {"authorized": True, "plans": out}


@router.post("/plan/get")
async def plan_get(b: GetPlanBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"plan": None}
    s = await _get_store(db, key)
    for p in (s.plans if s and s.plans else []):
        if p.get("id") == b.id:
            return {"plan": p.get("plan")}
    return {"plan": None}


class TrackBody(BaseModel):
    tg_user: dict = {}
    token: str = ""
    tracking: dict = {}


@router.post("/track/get")
async def track_get(b: AuthBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"authorized": False, "tracking": {}}
    s = await _get_store(db, key)
    return {"authorized": True, "tracking": (s.tracking if s and s.tracking else {})}


@router.post("/track/save")
async def track_save(b: TrackBody, db: AsyncSession = Depends(get_db)):
    key = _user_key(b.token, b.tg_user)
    if not key:
        return {"ok": False, "authorized": False}
    s = await _get_store(db, key)
    if s:
        s.tracking = b.tracking
        s.updated_at = datetime.now(timezone.utc)
    else:
        db.add(SelfServeStore(user_key=key, name=_disp_name(b), tracking=b.tracking, plans=[]))
    await db.commit()
    return {"ok": True}
