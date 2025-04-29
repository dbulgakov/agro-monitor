# Agro Monitor Serverless Application

Это серверлес-приложение для мониторинга сельскохозяйственных полей на основе публичных спутниковых данных.

## Архитектура

(Добавьте сюда краткую диаграмму или описание взаимодействия Frontend -> Backend API (FastAPI/Functions) -> Azure Storage (Queues, Blobs) -> Внешние API (STAC, OpenAI) -> Pulumi для деплоя)

## Компоненты

- **Backend**: Azure Functions на Python (развернутый как Docker-контейнер с FastAPI поверх ASGI адаптера)
- **Frontend**: Next.js React SPA (Static Export)
- **Infrastructure as Code**: Pulumi (Python)
- **CI/CD**: (Optional - Add details if implemented, e.g., GitHub Actions)

## Структура Проекта

```
.
├── backend/         # Backend: FastAPI + Azure Functions код + Dockerfile
│   ├── functions/   # Azure Function триггеры и биндинги
│   ├── routers/     # FastAPI роутеры API
│   ├── utils/       # Вспомогательные модули (e.g., Azure Storage client)
│   ├── tests/       # Тесты для backend
│   ├── app.py       # Основной FastAPI app
│   ├── Dockerfile   # Dockerfile для сборки образа
│   ├── requirements.txt # Зависимости Python для backend
│   └── .env.example # Пример файла переменных окружения
├── frontend/        # Frontend: Next.js приложение
│   ├── src/         # Исходный код Next.js (App Router)
│   ├── public/      # Статические ассеты
│   ├── lib/         # Вспомогательные модули (api client, sse client)
│   ├── tests/       # Тесты для frontend
│   ├── package.json # Зависимости Node.js
│   └── next.config.js # Конфигурация Next.js
├── infrastructure/  # Pulumi код для определения ресурсов Azure
│   ├── __main__.py  # Основной скрипт Pulumi
│   └── ... (модули для ресурсов: storage.py, function_app.py, etc.)
├── venv/            # Виртуальное окружение Python (если используется)
├── .gitignore       # Файл исключений Git
├── requirements.txt # Зависимости Python для Pulumi
├── Pulumi.yaml      # Определение проекта Pulumi
├── Pulumi.dev.yaml  # Конфигурация стека Pulumi 'dev'
└── README.md        # Этот файл
```

## Prerequisites

- Python 3.9+ и pip
- Node.js 18+ и npm (или yarn)
- Pulumi CLI (`brew install pulumi` или https://www.pulumi.com/docs/get-started/install/)
- Azure CLI (`brew install azure-cli` или https://docs.microsoft.com/cli/azure/install-azure-cli)
- Docker Desktop или Docker Engine (для локальной сборки и деплоя backend)

## Setup

1.  **Клонировать репозиторий:**
    ```bash
    git clone <repository-url>
    cd agro-monitor
    ```

2.  **Войти в Azure:**
    ```bash
    az login
    az account set --subscription <your-subscription-id>
    ```

3.  **Войти в Pulumi:**
    ```bash
    pulumi login # (Использовать Azure Blob Storage backend или Pulumi Service)
    ```

4.  **Установить зависимости Pulumi:**
    ```bash
    # Создать и активировать виртуальное окружение (рекомендуется)
    python -m venv venv
    source venv/bin/activate # В Windows: venv\Scripts\activate

    # Установить зависимости из корневого requirements.txt
    pip install -r requirements.txt
    ```

## Локальная разработка

### Запуск Backend (FastAPI локально)

Для локальной разработки backend запускается напрямую с помощью `uvicorn`.

```bash
cd backend

# Установить зависимости backend (рекомендуется в том же venv)
pip install -r requirements.txt

# Создать файл .env из примера и заполнить его
cp .env.example .env
# Отредактируйте .env, указав ваше имя Azure Storage Account
# AZURE_STORAGE_ACCOUNT_NAME=<your_dev_storage_account_name>
# Убедитесь, что вы вошли в Azure CLI (`az login`) для DefaultAzureCredential

# Запустить FastAPI приложение с помощью Uvicorn
# Переменные из .env будут загружены автоматически, если установлен python-dotenv
# (добавьте python-dotenv в backend/requirements.txt, если его нет)
uvicorn app:app --reload --port 8000
```

Backend будет доступен по адресу `http://localhost:8000`. Убедитесь, что frontend настроен на этот URL во время локальной разработки.

### Запуск Frontend (Next.js dev server)

```bash
cd frontend

# Установить зависимости frontend
npm install # или yarn install

# Создать .env.local для указания URL локального backend
# (если не хотите использовать прокси через /api)
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local

# Запустить сервер разработки Next.js
npm run dev # или yarn dev
```

Frontend будет доступен по адресу `http://localhost:3000`.

## Тестирование

### Backend Тесты (Pytest)

```bash
cd backend

# Установить зависимости для тестов (если еще не установлены)
pip install pytest httpx pytest-asyncio aiohttp # Добавьте другие, если нужны

# Запустить тесты (предполагается, что они в папке tests/)
# Может потребоваться установка переменных окружения из .env
# Используйте `pytest -s` для вывода print() из тестов
pytest
```
*(Уточните, если для тестов нужна специфическая настройка или команды)*

### Frontend Тесты (Jest / Playwright)

```bash
cd frontend

# Запустить Unit/Integration Тесты (например, Jest)
npm test # или yarn test

# Запустить End-to-End Тесты (например, Playwright)
# Первый запуск: npx playwright install
npm run test:e2e # или yarn test:e2e (Если скрипт настроен в package.json)
```
*(Уточните команды и настройку, если используется)*

## Деплой (Используя Pulumi)

Деплой создает/обновляет инфраструктуру Azure и развертывает backend (Docker образ в ACR и Function App) и frontend (статические файлы в Blob Storage).

1.  **Выбрать/Создать стек Pulumi:**
    ```bash
    # Выбрать существующий стек (например, dev или prod)
    pulumi stack select dev

    # Или создать новый
    # pulumi stack init my-new-stack
    ```

2.  **Настроить стек (если новый):**
    Установить необходимые значения конфигурации для стека.
    ```bash
    # Обязательно для нового стека:
    pulumi config set azure-native:location <your-azure-region> # например, westus2

    # Опционально: Переменные для Function App (если нужны и не заданы в коде)
    # pulumi config set --secret AppSettingName AppSettingValue
    ```
    *Имена ресурсов (группа, хранилище и т.д.) генерируются динамически с суффиксом стека.* 

3.  **Собрать статические файлы Frontend:**
    Pulumi загружает статический вывод сборки frontend.
    ```bash
    cd frontend
npm install # или yarn install
npm run build # Генерирует статические файлы в frontend/out/
cd ..
    ```
    *Важно:* Скрипт `build` в `frontend/package.json` должен выполнять статический экспорт (`next build`). `NEXT_PUBLIC_API_URL` **не** требуется во время сборки, так как API вызывается на клиенте относительно URL бэкенда (который определяется во время выполнения).

4.  **Развернуть с помощью Pulumi:**
    Запустите `pulumi up` из корневой директории проекта.
    ```bash
    # Убедитесь, что вы в корне проекта
    # Убедитесь, что ваше виртуальное окружение активировано

    pulumi up
    ```
    Pulumi покажет предварительный просмотр изменений. Проверьте и подтвердите для продолжения. Pulumi автоматически соберет Docker образ из `backend/Dockerfile`, загрузит его в созданный Azure Container Registry и развернет Function App и статику frontend.

5.  **Получить выходные данные:**
    После успешного развертывания получите эндпоинты:
    ```bash
    pulumi stack output

    # Или конкретные выходы:
pulumi stack output function_app_default_hostname
pulumi stack output static_website_endpoint
    ```
    *(Имена совпадают с теми, что экспортируются в `infrastructure/__main__.py`)*

## Очистка

Для удаления всех ресурсов, созданных определенным стеком:

```bash
# Убедитесь, что выбран правильный стек
pulumi stack select <stack-to-destroy>

pulumi destroy

# Опционально удалить историю стека
pulumi stack rm <stack-to-destroy>
```