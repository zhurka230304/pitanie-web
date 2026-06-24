import hashlib
import hmac
import json
import os
from urllib.parse import parse_qs
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import TgUser

router = APIRouter(prefix="/api/tg", tags=["tg_auth"])

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")
ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


# ——— Dependencies (defined first so endpoints below can reference them) ———

async def get_tg_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> TgUser:
    if not credentials:
        raise HTTPException(status_code=401, detail="Не авторизован")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Невалидный токен")
    user = await db.get(TgUser, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


async def require_trainer(user: TgUser = Depends(get_tg_user)) -> TgUser:
    if user.role != "trainer":
        raise HTTPException(status_code=403, detail="Только для тренеров")
    return user


async def require_client(user: TgUser = Depends(get_tg_user)) -> TgUser:
    if user.role != "client":
        raise HTTPException(status_code=403, detail="Только для клиентов")
    return user


# ——— Helpers ———

def _validate_init_data(init_data: str) -> dict:
    parsed = dict(parse_qs(init_data, keep_blank_values=True))
    hash_val = parsed.pop("hash", [""])[0]
    data_check = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, hash_val):
        raise ValueError("Invalid initData signature")
    return json.loads(parsed.get("user", ["{}"])[0])


def _make_token(user: TgUser) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(
        {"sub": str(user.id), "role": user.role, "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


# ——— Endpoints ———

class InitDataBody(BaseModel):
    init_data: str


@router.post("/auth")
async def tg_auth(body: InitDataBody, db: AsyncSession = Depends(get_db)):
    if not body.init_data and os.getenv("DEV_MODE") == "1":
        tg_data = {"id": 0, "first_name": "Dev", "username": "dev"}
    else:
        try:
            tg_data = _validate_init_data(body.init_data)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid Telegram initData")

    telegram_id = tg_data.get("id")
    if not telegram_id:
        raise HTTPException(status_code=400, detail="No user id in initData")

    result = await db.execute(select(TgUser).where(TgUser.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    is_new = False
    if not user:
        is_new = True
        user = TgUser(
            telegram_id=telegram_id,
            username=tg_data.get("username"),
            first_name=tg_data.get("first_name", ""),
            last_name=tg_data.get("last_name"),
            role="pending",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return {
        "token": _make_token(user),
        "role": user.role,
        "new_user": is_new,
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
        },
    }


class SetRoleBody(BaseModel):
    role: str  # "trainer" | "client"


@router.post("/set-role")
async def set_role(
    body: SetRoleBody,
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in ("trainer", "client"):
        raise HTTPException(status_code=400, detail="Роль должна быть trainer или client")
    user = await db.get(TgUser, user.id)
    user.role = body.role
    await db.commit()
    return {"ok": True, "role": body.role}


@router.post("/logout")
async def logout(
    user: TgUser = Depends(get_tg_user),
    db: AsyncSession = Depends(get_db),
):
    """Выход из личного кабинета: роль сбрасывается на pending,
    при следующем входе пользователь снова попадёт на онбординг."""
    user = await db.get(TgUser, user.id)
    user.role = "pending"
    await db.commit()
    return {"ok": True}
