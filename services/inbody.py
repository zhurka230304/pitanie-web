"""InBody: распознавание отчёта (Yandex Vision OCR + YandexGPT) и расчёт КБЖУ
по составу тела. Распознавание — best effort: при неудаче пользователь вводит
числа вручную (флоу «авто + подтверждение»)."""
import os
import re
import json
import base64
import asyncio

import httpx

ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725, "very_high": 1.9}
OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"


def kbju_from_inbody(weight, body_fat_pct=None, muscle_mass=None, bmr=None,
                     activity="moderate", goal="loss") -> dict:
    """КБЖУ по составу тела: измеренный BMR (точнее формулы), белок на сухую массу.
    goal: loss (-18%) / maintain / gain (+10%)."""
    w = float(weight or 0)
    if w <= 0:
        return {}
    if body_fat_pct:                       # сухая масса = вес − жир
        lean = w * (1 - float(body_fat_pct) / 100)
    elif muscle_mass:                      # SMM -> приблизительно вся безжировая масса
        lean = float(muscle_mass) * 1.9
    else:
        lean = w * 0.75
    lean = max(20.0, min(w, lean))
    base = float(bmr) if bmr else (370 + 21.6 * lean)    # Кетч-Макардл по сухой массе
    maint = base * ACTIVITY.get(activity, 1.55)
    kcal = maint * (0.82 if goal == "loss" else 1.10 if goal == "gain" else 1.0)
    protein = round(lean * 2.2)            # 2,2 г на кг сухой массы (сохранение мышц)
    fat = round(w * 0.9)
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {"kcal": round(kcal), "protein": protein, "fat": fat, "carbs": carbs,
            "lean": round(lean, 1), "maintenance": round(maint), "bmr_used": round(base)}


async def _ocr(image_bytes: bytes) -> str:
    """Распознать текст отчёта через Yandex Vision OCR. '' при недоступности."""
    key = os.getenv("YANDEX_API_KEY", "")
    folder = os.getenv("YANDEX_FOLDER_ID", "")
    if not key or not folder or not image_bytes:
        return ""
    body = {"mimeType": "image/jpeg", "languageCodes": ["ru", "en"], "model": "page",
            "content": base64.b64encode(image_bytes).decode()}
    headers = {"Authorization": f"Api-Key {key}", "x-folder-id": folder,
               "x-data-logging-enabled": "false", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(OCR_URL, json=body, headers=headers)
        if r.status_code != 200:
            print(f"[inbody] OCR HTTP {r.status_code}: {r.text[:400]}", flush=True)
            return ""
        data = r.json()
        text = ((data.get("result") or {}).get("textAnnotation") or {}).get("fullText", "") or ""
        if not text:
            print(f"[inbody] OCR пустой текст. Ответ: {str(data)[:400]}", flush=True)
        else:
            print(f"[inbody] OCR ок, символов: {len(text)}", flush=True)
        return text
    except Exception as e:
        print(f"[inbody] OCR исключение: {e!r}", flush=True)
        return ""


def _parse_with_gpt(text: str) -> dict:
    """Достать числа InBody из распознанного текста через YandexGPT. {} при неудаче."""
    try:
        from services.gpt import sdk
        model = sdk.models.completions("yandexgpt-lite")
        res = model.configure(temperature=0).run([
            {"role": "system", "text":
                "Ты извлекаешь числа из отчёта InBody. Верни ТОЛЬКО JSON без пояснений с полями: "
                "weight (вес тела, кг), body_fat_pct (процент жира PBF, %), "
                "muscle_mass (скелетная мышечная масса SMM, кг), bmr (базовый обмен / BMR, ккал). "
                "Если поля нет — поставь null. Числа без единиц измерения."},
            {"role": "user", "text": text[:4000]},
        ])
        out = res[0].text
        m = re.search(r"\{.*\}", out, re.S)
        parsed = json.loads(m.group(0)) if m else {}
        print(f"[inbody] GPT разобрал: {parsed}", flush=True)
        return parsed
    except Exception as e:
        print(f"[inbody] GPT parse исключение: {e!r}", flush=True)
        return {}


async def extract_inbody(image_bytes: bytes) -> dict:
    """Фото отчёта -> {weight, body_fat_pct, muscle_mass, bmr} (best effort)."""
    text = await _ocr(image_bytes)
    if not text:
        return {}
    loop = asyncio.get_running_loop()
    fields = await loop.run_in_executor(None, lambda: _parse_with_gpt(text))
    return {k: fields.get(k) for k in ("weight", "body_fat_pct", "muscle_mass", "bmr")}
