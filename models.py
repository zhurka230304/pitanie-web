from datetime import datetime, timezone, date
from sqlalchemy import Integer, BigInteger, String, Float, DateTime, Date, ForeignKey, Text, JSON, Boolean, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    searches: Mapped[list["SearchHistory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    orders: Mapped[list["OrderHistory"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Input params
    proteins: Mapped[float] = mapped_column(Float, nullable=False)
    fats: Mapped[float] = mapped_column(Float, nullable=False)
    carbs: Mapped[float] = mapped_column(Float, nullable=False)
    calories: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # "single" or "full"
    meal_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # breakfast/lunch/dinner/snack
    meal_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    delivery_service: Mapped[str] = mapped_column(String(50), default="vkusvill")
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Results
    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="searches")
    order: Mapped["OrderHistory | None"] = relationship(back_populates="search", uselist=False)


class OrderHistory(Base):
    __tablename__ = "order_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    search_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_history.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ordered_items: Mapped[list] = mapped_column(JSON, nullable=False)  # [{name, url, portion_g}]
    delivery_service: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="orders")
    search: Mapped["SearchHistory"] = relationship(back_populates="order")


class ProductDetailsCache(Base):
    __tablename__ = "product_details_cache"

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DishRating(Base):
    __tablename__ = "dish_ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "dish_xml_id", "meal_type", name="uq_dish_rating"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    dish_xml_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dish_name: Mapped[str] = mapped_column(String(255), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 или -1
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ComboRating(Base):
    __tablename__ = "combo_ratings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    search_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_history.id"))
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    combo_index: Mapped[int] = mapped_column(Integer, nullable=False)
    dish_xml_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DayRating(Base):
    __tablename__ = "day_ratings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    search_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_history.id"))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ——— Telegram Mini App models ———

class TgUser(Base):
    __tablename__ = "tg_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="client")  # trainer | client
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile: Mapped["ClientProfile | None"] = relationship(
        back_populates="client", foreign_keys="ClientProfile.client_id", uselist=False)
    plans_as_client: Mapped[list["MealPlan"]] = relationship(
        back_populates="client", foreign_keys="MealPlan.client_id")


class TrainerClient(Base):
    __tablename__ = "trainer_clients"
    __table_args__ = (UniqueConstraint("trainer_id", "client_id", name="uq_trainer_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainer_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    invite_token: Mapped[str | None] = mapped_column(String(64), nullable=True)  # which invite was used
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ClientProfile(Base):
    __tablename__ = "client_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tg_users.id", ondelete="CASCADE"), unique=True, nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male | female
    activity: Mapped[float | None] = mapped_column(Float, nullable=True)  # коэффициент 1.2..1.9
    goal_formula: Mapped[str | None] = mapped_column(String(20), nullable=True)  # mifflin | harris
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs: Mapped[float | None] = mapped_column(Float, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    client: Mapped["TgUser"] = relationship(
        back_populates="profile", foreign_keys=[client_id])


class MealPlan(Base):
    __tablename__ = "meal_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    trainer_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft|sent|acknowledged
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # [{meal_label, meal_type, dishes: [{id, xml_id, name, url, image_url, needed_g, portion_hint, nutrition}]}]
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cart_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["TgUser"] = relationship(
        back_populates="plans_as_client", foreign_keys=[client_id])


class ClientInvite(Base):
    __tablename__ = "client_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    trainer_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs: Mapped[float | None] = mapped_column(Float, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MealLog(Base):
    __tablename__ = "meal_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("tg_users.id", ondelete="CASCADE"))
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("meal_plans.id", ondelete="SET NULL"), nullable=True)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    ordered_items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserSeenDish(Base):
    __tablename__ = "user_seen_dishes"
    __table_args__ = (
        UniqueConstraint("user_id", "dish_xml_id", "meal_type", name="uq_user_seen_dish"),
        Index("idx_user_seen_dishes_user_meal", "user_id", "meal_type", "shown_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    dish_xml_id: Mapped[str] = mapped_column(String(64), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    shown_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ——— Future: Subscriptions & Revenue-share ———

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tg_users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="free")  # free|active|expired|cancelled
    referred_trainer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tg_users.id", ondelete="SET NULL"), nullable=True)
    revenue_share_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_share_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TrainerQuota(Base):
    """Monthly search quota per trainer, grows with number of active paying clients."""
    __tablename__ = "trainer_quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tg_users.id", ondelete="CASCADE"), unique=True, nullable=False)
    searches_this_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quota_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WeightLog(Base):
    """Трекинг прогресса: вес клиента по дням (одна запись на день)."""
    __tablename__ = "weight_logs"
    __table_args__ = (
        UniqueConstraint("client_id", "log_date", name="uq_weight_client_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tg_users.id", ondelete="CASCADE"), nullable=False)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SelfServeStore(Base):
    """B2C self-serve (App 1): профиль и история планов пользователя без тренера.
    Ключ user_key: "u<id>" — вход по почте (User), "tg<id>" — Telegram Login Widget.
    Гости хранят данные в браузере и эту таблицу не используют. Профиль/планы —
    JSON, одна строка на пользователя (история — последние ~30 планов)."""
    __tablename__ = "selfserve_accounts"

    user_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plans: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

