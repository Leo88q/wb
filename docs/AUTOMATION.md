# Техническая документация

## Архитектура

```
CSV + папки фото  →  app/pipeline.py  →  SQLite (data/wb_resale.db)  →  REST API  →  веб-дашборд / расширение / Telegram
                          ↓                     ↓
                   photos.py, sizes.py,     bump.py, assistant.py,
                   descriptions.py,        research.py
                   pricing.py
```

Сервер — `http.server.ThreadingHTTPServer` из стандартной библиотеки, фронтенд —
обычный HTML/CSS/JS без сборки. Единственная внешняя зависимость — `Pillow`
(без неё фото копируются как есть).

## Запуск и переменные окружения

```bash
python3 run.py --port 8000 --host 0.0.0.0 --seed-demo
python3 run.py --reseed            # пересоздать демо-партию
```

| Переменная | Назначение |
|---|---|
| `LLM_API_URL` | OpenAI-совместимый базовый URL (к нему добавляется `/chat/completions`) |
| `LLM_API_KEY` | ключ; без него используется офлайн-генератор |
| `LLM_MODEL` | модель, по умолчанию `gpt-4o-mini` |
| `LLM_VISION` | `1` — распознавать вещь по первому фото (vision-модель) |
| `LLM_TIMEOUT` | таймаут запроса, сек (45) |
| `ASSISTANT_LLM` | `0` — отключить LLM в ассистенте, оставить правила |
| `WB_API_TOKEN` | токен Seller API (только для `app/wb_api.py`, не для ресейла) |
| `TELEGRAM_BOT_TOKEN` | токен бота из `tools/telegram_bot.py` |
| `WB_LOCAL_API` | адрес локального API для Telegram-бота |

## Формат CSV инвентаря

Разделитель `,` `;` `\t` или `|` определяется автоматически, кодировка UTF-8
(с BOM или без). Заголовки можно писать по-русски: `Артикул`, `Бренд`, `Цвет`,
`Размер`, `Состояние`, `Дефекты`, `Грудь`, `Талия`, `Бёдра`, `Длина`, `Цена` и т.д.

| Поле | Обязательно | Пример | Примечание |
|---|---|---|---|
| `vendor_code` | да | `DR-1001` | имя папки с фото, только A-Z 0-9 `-` `_` |
| `brand` | нет | `ZARA` | влияет на ценовой коэффициент |
| `type` | нет | `платье миди` | подставляется в заголовок и ключи |
| `color`, `material`, `print` | нет | `чёрный`, `вискоза` | честно, по бирке |
| `size` | нет | `44`, `M`, `44-46` | нормализуется в российский размер |
| `condition` | нет | `отличное` | новое с биркой / отличное / хорошее / удовлетворительное / есть дефекты |
| `defects` | нет | `затяжка на подоле` | обязательно указывайте — это снижает возвраты |
| `chest`, `waist`, `hips`, `length`, `sleeve`, `shoulders` | нет | `88`, `68`, `94`, `112` | замеры в см; если размера нет, он подберётся по груди/талии/бёдрам |
| `price` | нет | `1900` | если указана — она и ставится; рекомендация остаётся в `min/max` |
| `season`, `style`, `source`, `notes` | нет | `лето`, `повседневное` | дополнительные факты для описания |

## REST API

Все ответы — JSON, CORS открыт (для расширения Chrome).

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/health` | статус, наличие LLM и Pillow |
| GET | `/api/stats` | счётчики, календарь, настройки |
| GET | `/api/settings` · POST | чтение/изменение настроек (интервал, лимит, шаг цены) |
| GET | `/api/listings?status=&q=&limit=` | список карточек |
| GET/POST | `/api/listings/<id>` | карточка / частичное обновление (PATCH тоже работает) |
| POST | `/api/listings/<id>/regenerate` | перегенерировать заголовок, описание, ключи |
| POST | `/api/pipeline/import` | `{csv, use_llm, vision, process_photos}` → импорт |
| POST | `/api/pipeline/photos` | переобработать папки `data/incoming/*` |
| POST | `/api/pipeline/upload-photos` | `{vendor_code, files:[{name,data(dataURL)}]}` |
| POST | `/api/pipeline/upload-ready` | `{groups:[{vendor_code, source_folder, append, files:[{name,data,original}]}]}` — уже сжатые браузером JPEG без повторного пережатия |
| GET | `/tools/prepare_photos.py`, `/prepare_photos.command` | скачать standalone-скрипт сжатия |
| POST | `/api/pipeline/demo` · `/clear-demo` | демо-партия 300 шт. |
| GET | `/api/export/json` · `/csv` · `/zip` | экспорт карточек и фото |
| GET | `/api/sizes/grid` | таблица размерного ряда |
| GET | `/api/bumps/queue` · `/tasks` | очередь и календарь |
| POST | `/api/bumps/generate` | создать задачи на день |
| POST | `/api/bumps/complete` · `/skip` · `/clear` | управление задачами |
| GET/POST | `/api/research` · `/research/import` | ресерч рынка и загрузка CSV конкурентов |
| POST | `/api/research/reprice` | пересчитать цены всех вещей по медиане рынка |
| POST | `/api/assistant` | `{message, listing_id, mode: seller\|buyer}` |
| POST | `/api/generate/preview` | пример описания без сохранения |
| POST | `/api/seller-api/test` | dry-run пример официального Seller API |

## Подготовка «тяжёлых» фото (>10 МБ)

Официальные требования WB: JPG/PNG/WEBP, минимум 700×900 px, качество ≥65%,
до 10 МБ на файл, до 30 фото на карточку, минимум 2 фото для показа в поиске.

`app/photos.py::compress_image()` — ядро:

1. `ImageOps.exif_transpose` → поворот по EXIF, приведение ICC к sRGB, RGB.
2. Ограничение 8000 px по стороне, вписывание в холст 3:4 (1200×1600 по
   умолчанию) без обрезки, поля `(250,250,250)`.
3. Адаптивное сжатие: качества `88 → 85 → … → 65`, затем уменьшение холста
   на 18% за шаг, но не ниже 700×900. Первый вариант, который уложился в
   `max_bytes` (по умолчанию 9.5 МБ), записывается на диск.
4. EXIF/GPS в итоговый файл не пишутся (приватность домашней съёмки).

`app/photos.py::process_folder()` дополнительно убирает дубли по MD5, нумерует
`01.jpg…30.jpg`, считает `total_bytes_before/after`, `max_photo_bytes` и пишет
`manifest.json`.

Два фронтенда для этого ядра:

* **Браузерный** (`web/app.js`, блок «⚡ Фото больше 10 МБ»): выбор папки через
  `webkitdirectory`, группировка по подпапкам или пачками по N, сжатие через
  `createImageBitmap` + `<canvas>.toBlob('image/jpeg', q)` с тем же адаптивным
  алгоритмом, отправка батчами по 4 файла в `/api/pipeline/upload-ready`.
  HEIC/RAW браузер может не прочитать — тогда лог предлагает скрипт для Mac.
* **Standalone CLI** (`tools/prepare_photos.py` + `prepare_photos.command`):
  работает на Mac/Windows/Linux без сервера, умеет HEIC (`pillow-heif` или macOS
  `sips`), создаёт `report.csv`, `inventory.csv`, `manifest.json`, `README.txt`.
  Путь по умолчанию — `/Users/zlata/Desktop/Одежда` → `/Users/zlata/Desktop/Одежда_WB`.

Автозависимости: `run.py` при первом запуске ставит Pillow в `.deps/`
(папка в `.gitignore`), `app/photos.py` и CLI подключают её автоматически.
Если Pillow недоступен — на macOS сценарий переключается на `sips`.

## Алгоритм «поднятия»

```
for each published listing:
    if next_bump_at <= today: candidate
sort candidates by last_bump_at (самые старые первыми)
take first N (bump_per_day, по умолчанию 15)
for each:
    variant += 1 → свежая формулировка описания
    if bump_auto_price_drop and bump_count >= 2:
        new_price = max(round(current * (1 - pct/100)), min_price)
        kind = "price_drop"
    create bump_task(due_at=today, kind, suggested_price, suggested_description, note)
```

После отметки «✓ Выполнено»: `last_bump_at = сегодня`, `next_bump_at = сегодня +
bump_interval_days` (по умолчанию 10), `bump_count += 1`, при желании цена и
описание карточки обновляются. Никаких скрытых запросов к WB не производится.

## Расширение Chrome

`extension/`: MV3, popup читает `/api/listings`, content script заполняет поля
формы на wildberries.ru. Селекторы — в `extension/selectors.json`
(шаблон `selectors.example.json`). Если вёрстка изменилась, поправьте массив
CSS-селекторов; расширение показывает список полей, которые не нашлись, и
копирует описание в буфер. Файловый input расширение не трогает — фото
скачиваются в `Загрузки/wb-resale/<артикул>/` и перетаскиваются вручную.

## Playwright-сценарий

`tools/autopublish.py` — опционально, **риск нарушения правил WB**.
Работает в постоянном профиле браузера (`.playwright-profile/`), использует
вашу сессию, не обходит капчу, не нажимает «Опубликовать» без вашего Enter.
По умолчанию `--dry-run`. Режимы: `publish` (форма создания) и `bump`
(редактирование объявления по `wb_url`).

```bash
pip install playwright && python -m playwright install chromium
python3 tools/autopublish.py --mode publish --limit 10 --dry-run
python3 tools/autopublish.py --mode bump --limit 15 --no-dry-run
```

## Официальный Seller API

`app/wb_api.py` — минимальный клиент с dry-run по умолчанию: родительские
категории, предметы, характеристики, создание/обновление карточек, медиа,
цены. **Это не API Ресейла.** Используйте только если продаёте новые/уценённые
товары как селлер с оформленным статусом. Создание карточек асинхронное,
до 100 карточек за запрос, лимит метода — около 10 запросов в минуту. [S4][S16]

## Безопасность

- Сервер слушает `0.0.0.0` только для локального превью; в продакшене
  привяжите его к `127.0.0.1` и не открывайте порт наружу.
- Токены не пишутся в БД и не логируются; `.env` в `.gitignore`.
- SQLite-файл и фото исключены из Git (`.gitignore`); в репозитории лежат
  только примеры CSV и код.
- Расширение и Playwright не собирают и не передают данные вашего аккаунта WB.

Ссылки [S4], [S16] — в [PLAN.md](PLAN.md#7-источники).
