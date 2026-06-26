import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, RedirectResponse
from dotenv import load_dotenv

load_dotenv()

from database import init_db
from auth import get_current_user
from models import User
from routers import auth, search, history, ratings, ingredients, tg_auth, trainer
from routers import client as client_router
from routers import mealprep
from routers import selfserve


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Журка — подбор блюд", lifespan=lifespan)

# Legacy web SPA routes
app.include_router(auth.router)
app.include_router(search.router)
app.include_router(history.router)
app.include_router(ratings.router)
app.include_router(ingredients.router)

# Telegram Mini App routes
app.include_router(tg_auth.router)
app.include_router(trainer.router)
app.include_router(client_router.router)
app.include_router(mealprep.router)

# B2C self-serve (App 1) — отдельный сайт для людей без тренера
app.include_router(selfserve.router)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/mealprep")
async def serve_mealprep():
    return FileResponse(
        "static/mealprep.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/meal-plan")
async def serve_selfserve():
    """App 1 (B2C self-serve) — персональный план питания на неделю."""
    return FileResponse(
        "static/selfserve.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/menu")
async def redirect_menu_to_meal_plan():
    """Старый адрес оставляем как редирект, чтобы внешние ссылки не ломались."""
    return RedirectResponse(url="/meal-plan", status_code=307)


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
