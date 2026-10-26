# Данные для бэктестинга

Первый этап проекта: **Binance Spot API → Pandas DataFrame → SQLite3**.
FastAPI принимает параметры исторической загрузки и возвращает статистику.
Движок, стратегии, индикаторы и C++ пока не реализованы.

## Быстрый запуск

Нужен Python 3.12 или новее. Команды из каталога `backtesting`, PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Если `python` отсутствует в PATH, используйте `py -3` в первой команде.
SQLite3 включён в Python: отдельный сервер или установка SQLite не нужны.
База `data/market_data.sqlite3` создаётся при старте приложения.

Документация с формами отправки запросов: <http://127.0.0.1:8000/docs>.
Здоровье сервиса и доступность БД: <http://127.0.0.1:8000/health>.
Для публичных свечей ключ Binance не нужен.

### Загрузить сутки часовых свечей

При запущенном сервере выполните в другом терминале:

```powershell
$body = @{
    symbol = 'BTCUSDT'
    interval = '1h'
    start = '2024-01-01T00:00:00Z'
    end = '2024-01-02T00:00:00Z'
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
    -Uri 'http://127.0.0.1:8000/candles/import' `
    -ContentType 'application/json' -Body $body
```

При наличии истории ожидаются 24 свечи: `fetched=24`, `prepared=24`,
`inserted=24`, `duplicates=0`, `excluded=0`.
Повторный запрос даст `inserted=0`, `duplicates=24`.
Существующие свечи при совпадении ключа не перезаписываются.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/candles?symbol=BTCUSDT&interval=1h&limit=10'
Invoke-RestMethod 'http://127.0.0.1:8000/datasets'
```

## API

| Метод | Адрес | Назначение |
| --- | --- | --- |
| `POST` | `/candles/import` | Скачать, обработать и сохранить свечи |
| `GET` | `/candles` | Прочитать сохранённые свечи в порядке времени |
| `GET` | `/datasets` | Пары/интервалы, количество свечей и крайние даты |
| `GET` | `/health` | Проверить БД |

Параметры импорта:

- `symbol`: например `BTCUSDT` или `ETHUSDT`; пробелы по краям и регистр нормализуются.
- `interval`: `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`,
  `8h`, `12h`, `1d`, `3d`, `1w`, `1M`. `1m` — минута, `1M` — месяц.
- `start`: дата ISO 8601 с часовым поясом, включительно по времени открытия.
- `end`: дата ISO 8601 с часовым поясом, исключительно; можно опустить для загрузки
  до серверного времени Binance. Будущая граница ограничивается временем Binance.

Время нормализуется к UTC с точностью до миллисекунд. Наивные даты без часового пояса,
даты до 1970 года и точность мельче миллисекунды отклоняются.
Сохраняются только свечи, открытые не раньше `start` и полностью закрывшиеся до `end`
и до снимка серверного времени Binance. Например, при `interval=1h` и `end=12:30`
свеча `12:00–12:59:59.999` исключается. Незакрытая текущая свеча не попадает в бэктест.

Счётчики ответа:

- `fetched`: число строк, полученных из Binance;
- `prepared`: число свечей после обработки;
- `inserted`: новые строки в БД;
- `duplicates`: подготовленные свечи, которые уже есть в БД;
- `excluded`: строки, исключённые обработкой;
- `start_ms`, `effective_end_ms`, `server_time_ms`: границы и снимок времени, Unix ms.

`GET /candles` требует `symbol` и `interval`; дополнительно принимает `start`, `end`,
`limit` (1–10000, по умолчанию 1000) и `after_open_time` (Unix ms).
Фильтры чтения применяются к времени **открытия**: `start <= open_time < end`.
Для следующей страницы передайте `next_after_open_time` ответа в `after_open_time`,
сохранив остальные фильтры. `next_after_open_time=null` означает последнюю страницу.

Ошибка входных параметров возвращает `422`, неверный символ Binance — `400`,
некорректный ответ/сетевая ошибка Binance — `502`, лимит объёма — `413`.
При Binance `429`/`418` импорт прекращается, API возвращает `503` с `Retry-After`.
Новые запросы нужно отправлять после этой задержки. Региональный запрет `451`
возвращается как `503` с объяснением. Данные при этих ошибках не дописываются.

## Что делает Pandas

Pandas сам не определяет, какие поля нужны: выбор явно задан в `app/processing.py`.
Из 12 полей Binance оставляем 7 полей для OHLCV-бэктеста:

| Поле | Pandas | SQLite | Значение |
| --- | --- | --- | --- |
| `open_time` | `int64` | `INTEGER` | Открытие свечи, Unix ms UTC |
| `close_time` | `int64` | `INTEGER` | Конец свечи, Unix ms UTC |
| `open` | `float64` | `REAL` | Цена открытия |
| `high` | `float64` | `REAL` | Максимум |
| `low` | `float64` | `REAL` | Минимум |
| `close` | `float64` | `REAL` | Цена закрытия |
| `volume` | `float64` | `REAL` | Объём в базовом активе, например BTC для BTCUSDT |

Удаляем служебное `ignore`, объём в котируемой валюте, число сделок и taker-объёмы.
Для будущих стратегий на потоке сделок схему нужно расширить отдельно.
Это свечные OHLCV-данные; стакан, funding, комиссии и сделки в этот этап не входят.

Проверяются типы, конечность чисел, положительные цены, неотрицательный объём,
согласованность OHLC и времени. Строки сортируются, дубли удаляются.
Пропуски в истории не заполняются искусственными свечами: `/datasets` показывает
крайние даты, но не гарантирует непрерывность истории.

`REAL` хранит IEEE-754 double: удобно для Pandas и будущего C++ `double`,
но десятичные цены представляются приближённо. Для точного денежного учёта
понадобятся целочисленные масштабы или десятичные значения.

## База и будущее чтение из C++

База — обычный файл SQLite3 со схемой `app/schema.sql`, версия `PRAGMA user_version=1`.
Таблица `candles` дополнительно содержит `symbol` и `interval`.
Составной первичный ключ `(symbol, interval, open_time)` защищает от дублей
и служит индексом для чтения одной серии по времени. SQL-ограничения проверяют OHLCV.
WAL позволяет читателям работать во время записи, `busy_timeout=5000` ждёт короткую блокировку.
Каждый импорт записывается одной транзакцией. При ошибке второй страницы Binance
или проверок первая страница тоже не попадает в БД.

Для будущего движка достаточно открыть файл библиотекой SQLite в режиме чтения
и выполнить параметризованный запрос:

```sql
SELECT open_time, close_time, open, high, low, close, volume
FROM candles
WHERE symbol = ? AND interval = ? AND open_time >= ? AND open_time < ?
ORDER BY open_time ASC;
```

В C++ время читается как `int64_t`, цены и объём — как `double`.
Реализации движка здесь пока нет. При копировании активной WAL-базы используйте
SQLite backup API либо остановите приложение перед копированием файла.

## Структура

```text
backtesting/
  app/
    main.py          # FastAPI, жизненный цикл, HTTP-ошибки
    schemas.py       # Входные и выходные модели
    config.py        # Настройки
    binance.py       # HTTP-клиент, страницы по 1000 свечей, повторы
    processing.py    # Pandas: выбор полей, типы и проверки
    service.py       # Связь загрузчика, обработки и хранилища
    database.py      # SQLite3, транзакции, чтение
    schema.sql       # SQL-схема
  data/              # SQLite-файл создаётся при старте
  tests/             # Изолированные тесты без доступа к Binance
  requirements.txt
  requirements-dev.txt
  pyproject.toml
```

По умолчанию публичный endpoint: `https://data-api.binance.vision`.
`BACKTEST_DATABASE_PATH` меняет путь БД; относительный путь считается от каталога проекта.
`BINANCE_BASE_URL` меняет host Binance. Пример для PowerShell:

```powershell
$env:BACKTEST_DATABASE_PATH = 'data/my_backtest.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

`.env.example` — образец переменных; автоматического чтения `.env` нет.

Импорт выполняется в текущем HTTP-запросе, блокирующие операции работают в пуле
потоков FastAPI. Лимит — 100000 сырых свечей за импорт, чтобы ограничить память.
Большую историю загружайте периодами. Повторы сетевых ошибок/`5xx`: до 3 попыток,
таймаут 20 секунд на сетевую операцию, пауза между страницами 0.1 секунды.
При перезапуске сервера незавершённый импорт нужно отправить заново.
Для длительных фоновых загрузок в дальнейшем можно добавить очередь задач.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Тесты используют `httpx.MockTransport` и настоящую SQLite во временном каталоге:
пагинация, очистка, закрытые свечи, HTTP-валидация, лимиты, повторная загрузка,
сохранение/чтение и откат при ошибках.

Проверено при реализации: 42 теста прошли, Ruff проверка и форматирование прошли.
Дополнительно через реальный Binance и FastAPI загружены BTCUSDT за 1 января 2024 UTC:
24 часовые и 1440 минутных свечей. Этот пример уже находится в локальной
`data/market_data.sqlite3`; повторный импорт не добавляет дублей.

Официальные источники контракта:
[Binance public market data](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md),
[Klines](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market#klines),
[Binance limits](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#limits).
