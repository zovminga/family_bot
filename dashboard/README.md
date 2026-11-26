# Дашборд семейных трат

Автономный HTML-дашборд по данным из той же Google-таблицы (`FamilyExpenses`),
куда пишет Telegram-бот. Сборка (`python -m family_bot.dashboard`, код —
`family_bot/dashboard/build_dashboard.py`) тянет данные через
`gspread` (или из CSV), конвертирует валюты в базовую (`BASE_CURRENCY` из `.env`, без неё — EUR),
группирует комментарии в темы (по умолчанию — правила, опционально LLM:
Claude или OpenAI) и собирает один самодостаточный HTML-файл с вшитыми данными.

Агенты: общий вход — [`../AGENTS.md`](../AGENTS.md); полная спека дашборда —
[`SPEC.md`](SPEC.md).

> Нужно авто-обновление (по кнопке / раз в месяц) с классификацией через
> Claude? См. раздел [«Авто-обновление»](#авто-обновление) ниже.

> Полная спецификация и формулы расчётов — в [`SPEC.md`](SPEC.md).

## Что внутри

- **Первый экран**: этот месяц, «обычно», прогноз и топ категорий против среднего за 3 месяца (SPEC §4.0).
- **KPI**: всего, средний чек, топ-категория.
- **Инсайты** (авто): главная статья, что выросло/упало (MoM), регулярные траты, кандидат на сокращение.
- **📅 Монитор «Расходы по месяцам»** — теплокарта статья×месяц с трендом; группировка Категории / Темы / Кто. Главный блок для мониторинга.
- **💡 Оптимизация «На чём можно сэкономить»** — разбивка трат на 🔒 обязательные / ⚙️ базовые / 💸 необязательные, среднее/мес и потенциал экономии.
- **Графики**: категории, люди, валюты, темы. У каждой категории свой цвет (SPEC §4).
- **Таблица операций** с фильтрами и сортировкой.
- **Свёрнуто внизу** («Ещё графики»): динамика, состав по месяцам, средний чек, дни недели, explorer группировки (SPEC §4.8).
- **Тема** тёмная или светлая — по настройке системы. Подсказки `i` открываются наведением или нажатием.
- **Исключение категорий** из анализа (чипы) и **переключатель валют** (база ↔ по каждой).

## Запуск

```bash
cd family_bot
uv sync    # see README.md; or: pip install -r dashboard/requirements.txt
```

Нужны переменные окружения (берутся из `.env` в корне репозитория):

```bash
GOOGLE_CREDS_PATH=secrets/creds.json   # service account для Google Sheets
SHEET_NAME=FamilyExpenses
ANTHROPIC_API_KEY=sk-ant-...           # для группировки комментариев через Claude
OPENAI_API_KEY=sk-...                  # опционально, если используешь --provider openai
BOT_TOKEN=...                          # для --publish (отправка дашборда в Telegram)
DASHBOARD_CHAT_ID=<your_telegram_id>   # чат, куда слать дашборд после сборки
BASE_CURRENCY=EUR                      # базовая валюта (ISO), её же бот ставит по умолчанию
DASHBOARD_FROM_MONTH=2024-01           # необязательно: скрыть месяцы раньше этого
DASHBOARD_SPENDER_ALIASES=ann=Анна,annie=Анна   # необязательно: одно имя в разных написаниях
DASHBOARD_FILES_DIR=/etc/secrets      # необязательно: папка с theme_keywords.json и manual_adjustments.csv
```

Свои магазины, кафе и места для группировки по темам — в
`dashboard/theme_keywords.json` (файл в `.gitignore`). Если задан
`DASHBOARD_FILES_DIR`, файл берётся из этой папки. Формат:
`{"Кофе": ["corner cafe"], "Супермаркет и продукты": ["мой магазин"]}`. Темы —
только из `THEME_RULES` в `build_dashboard.py`; слова дописываются к словам темы.

1. Положи service-account JSON в `secrets/creds.json` в корне репозитория
   (папка `secrets/` в `.gitignore`, в публичный репозиторий не попадёт).
2. Убедись, что таблица расшарена на email service-account'а.
3. Собери дашборд:

```bash
python -m family_bot.dashboard                    # база и первый месяц из .env, темы по правилам
python -m family_bot.dashboard --base EUR         # другая базовая валюта
python -m family_bot.dashboard --llm              # темы через Claude (провайдер по умолчанию)
python -m family_bot.dashboard --llm --provider openai   # темы через OpenAI
python -m family_bot.dashboard --from ""          # показать ВСЕ периоды
python -m family_bot.dashboard --from 2024-07     # с конкретного месяца
```

### Провайдеры классификации комментариев

| Режим | Флаг | Ключ | Модель по умолчанию |
|---|---|---|---|
| Правила (по ключевым словам) | _(без `--llm`)_ | — | — |
| Claude (Anthropic) | `--llm` или `--llm --provider anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` (реальный id API; legacy, см. `docs/STATUS.md`) |
| OpenAI | `--llm --provider openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |

Результаты LLM кэшируются **отдельно по провайдеру** в
`output/llm_cache_<provider>.json` — повторные сборки отправляют в модель только
новые комментарии. Модель можно переопределить флагом `--model`.

## Авто-обновление и доставка в Telegram

Архитектура: **дашборд собирает сам бот** (на Render) — 1-го числа каждого
месяца в `MONTHLY_SUMMARY_HOUR` (по умолчанию 9:00, зона `TZ_NAME`) и по команде
`/build_dashboard`. Сборка идёт в отдельном потоке, бот в это время отвечает.
После сборки бот отправляет HTML в чат `DASHBOARD_CHAT_ID` и сохраняет его
`file_id` в лист `Meta`. По кнопке «📈 Дашборд» и `/dashboard` бот
пере-отправляет последний файл по `file_id`.

Поток: собрать (Sheets + Claude) → `sendDocument` в чат `DASHBOARD_CHAT_ID` →
записать `file_id` в `Meta!B1` → бот читает `Meta!B1` и отдаёт файл по запросу.

Бот берёт настройки из своего окружения: `BASE_CURRENCY`, `DASHBOARD_FROM_MONTH`,
`DASHBOARD_SPENDER_ALIASES`, `DASHBOARD_FILES_DIR`. Темы — через Claude, если
задан `ANTHROPIC_API_KEY`, иначе по правилам. Кэш тем (`output/llm_cache_*.json`)
на Render живёт до следующего деплоя, поэтому сборка после деплоя заново
отправляет в Claude все комментарии. Сбой сборки — короткое сообщение в
`DASHBOARD_CHAT_ID`, подробности — в логе бота (Render → Logs).

`run.sh` на компьютере делает ту же сборку и отправку. Он нужен для пробной
сборки и для установки без бота на сервере. Не ставь агент `launchd` (ниже)
вместе с ботом на Render: 1-го числа придут два файла.

**По требованию** (одной командой):

```bash
cd family_bot/dashboard
./run.sh                 # собрать сейчас (Sheets + Claude) и отправить в Telegram
./run.sh --from ""       # то же, но со всей историей
./run.sh --provider openai   # сменить провайдера классификации
./run.sh --trial --csv sample.csv   # пробная сборка: без Claude и без Telegram
```

`run.sh` всегда добавляет `--llm --provider anthropic --publish` (Claude +
отправка в Telegram + запись `file_id`). Исключение — первый аргумент `--trial`:
тогда темы по правилам и без отправки.

Окружение: `run.sh` держит изолированный `venv/` на Python 3.12 и ставит в него
`requirements.txt` (экспорт `uv.lock`). Сам пакет `family_bot` в venv не
ставится: `run.sh` добавляет корень репозитория в `PYTHONPATH` и вызывает
`python -m family_bot.dashboard`. Нужен [`uv`](https://docs.astral.sh/uv/):
он создаёт venv и сам скачивает Python 3.12, если его нет на машине. В
`venv/.deps-stamp` записаны версия Python и SHA-256 `requirements.txt`. Если
они не совпадают с текущими, `run.sh` удаляет `venv/` и создаёт заново. Так
следующий запуск (в т.ч. по расписанию) подхватывает новые зависимости.

**Раз в месяц** (через `launchd` — нативный планировщик macOS, догоняет
пропущенный запуск при пробуждении, в отличие от cron):

```bash
cd family_bot/dashboard
./schedule.sh install     # поставить агент: 1-е число месяца, 09:00
./schedule.sh status      # проверить, загружен ли агент
./schedule.sh run         # прогнать сборку прямо сейчас через launchd
./schedule.sh uninstall   # удалить агент
```

День/время меняются переменными окружения при установке, например:

```bash
RUN_DAY=1 RUN_HOUR=8 RUN_MINUTE=30 ./schedule.sh install
```

Имя агента — `local.family-bot.dashboard`. Другое имя задаёт строка
`LAUNCHD_LABEL=...` в `.env` (без кавычек) или переменная окружения при запуске
`schedule.sh`. Сменил имя — сначала `./schedule.sh uninstall` со старым именем.

Логи сборки: `output/build.log` (ручной запуск) и `output/launchd.log`
(по расписанию). Строка лога: время, уровень, модуль, текст. Уровень задаёт
`LOG_LEVEL` в `.env` (по умолчанию `INFO`, подробнее — `DEBUG`). Если сборка без `--trial` упала, в тот же чат
`DASHBOARD_CHAT_ID` приходит короткое сообщение: на каком этапе и тип ошибки.
Подробности — в логе. Текст ошибки и токен в сообщение не попадают.
Сгенерированный plist лежит в `~/Library/LaunchAgents/` и в репозиторий не
попадает.

Месяцы раньше `DASHBOARD_FROM_MONTH` скрыты (флаг `--from` её переопределяет). Ручные доначисления
(например, забытая аренда) лежат в `dashboard/manual_adjustments.csv` (или в папке
`DASHBOARD_FILES_DIR`) и автоматически подмешиваются при сборке.

Результат: `dashboard/output/expenses_dashboard.html` — открывается двойным
кликом. Папка `output/` в `.gitignore`, т.к. содержит реальные финансовые данные.

## Заметки

- Курсы валют берутся с бесплатного `exchangerate-api.com` (без ключа).
- Темы комментариев кэшируются в `output/llm_cache_<provider>.json` — повторные
  запуски отправляют в LLM только новые комментарии.
- Готовый HTML работает без интернета: Chart.js вшит в файл. Первая сборка
  скачивает Chart.js в `dashboard/vendor/` (gitignored), дальше берёт из кэша.
