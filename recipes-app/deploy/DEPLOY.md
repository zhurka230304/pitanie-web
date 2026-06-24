# Деплой recipes-app (приложение «Журка — Рецепты»)

Работает на порту 8001 (только localhost), наружу — через nginx по /recipes/.

## 1. Зависимости
    cd ~/pitanie-web/recipes-app
    pip install --user -r requirements.txt

## 2. systemd-сервис
    sudo cp deploy/recipes.service /etc/systemd/system/recipes.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now recipes
    systemctl status recipes        # должно быть active (running)

Проверка локально:
    curl http://127.0.0.1:8001/      # вернёт HTML

## 3. nginx
Открыть конфиг сайта (обычно /etc/nginx/sites-available/... или /etc/nginx/conf.d/...),
вставить содержимое deploy/nginx-recipes.conf ВНУТРЬ блока server для zhurka-pitanie.ru,
затем:
    sudo nginx -t && sudo systemctl reload nginx

Открыть: https://zhurka-pitanie.ru/recipes/

## Обновление после git pull
    cd ~/pitanie-web && git pull
    sudo systemctl restart recipes

## Доступ: обычный сайт + вход через Telegram
Приложение работает как обычный сайт — открывается в любом браузере без VPN и
без Telegram. Три режима:
- **Гость (без входа)** — профиль и история планов хранятся в браузере (localStorage).
- **Вход через Telegram на сайте** — кнопка «Войти через Telegram» (Telegram Login
  Widget). Данные синхронизируются между устройствами (хранятся на сервере по tg_id).
- **Внутри Telegram (Mini App)** — как раньше.

### Настройка входа через Telegram (необязательно)
Создай `recipes-app/.env`:
    BOT_TOKEN=токен_бота_из_BotFather
    BOT_USERNAME=имя_бота_без_@   # например ZhurkaRecipesBot
Затем в @BotFather: `/setdomain` → выбери бота → укажи домен `zhurka-pitanie.ru`
(Login Widget работает только на зарегистрированном домене по HTTPS).
После правки .env: `sudo systemctl restart recipes`

Если `BOT_USERNAME` не задан — кнопки входа не будет, сайт работает в гостевом режиме.

## Личный кабинет (профиль)
Профиль (КБЖУ/настройки) и история планов авторизованных пользователей хранятся
в SQLite `data/profiles.db` (в git не попадает), ключ — Telegram ID. У гостей всё
лежит в localStorage браузера.
