import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

from database import get_db
from models import User
from auth import hash_password, verify_password, create_token
from services.email import send_welcome

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


@router.post("/register", response_model=AuthResponse)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не менее 6 символов")
    if len(data.name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Введите имя")

    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")

    user = User(
        email=data.email,
        name=data.name.strip(),
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_token(user.id)
    try:
        await send_welcome(user.email, user.name)
    except Exception as e:
        logger.error(f"Welcome email failed for {user.email}: {e}")
    return AuthResponse(token=token, user={"id": user.id, "name": user.name, "email": user.email})


@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    token = create_token(user.id)
    return AuthResponse(token=token, user={"id": user.id, "name": user.name, "email": user.email})


@router.get("/me")
async def me(db: AsyncSession = Depends(get_db)):
    # Called with token via dependency in main.py
    pass
