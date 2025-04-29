# Agro Monitor Serverless Application

Это серверлес-приложение для мониторинга сельскохозяйственных полей на основе публичных спутниковых данных.
В проекте используется:

- **Backend**: Azure Functions на Python (FastAPI + Azure Functions)
- **Frontend**: Next.js React SPA
- **Infrastructure as Code**: Pulumi (Python)

## Prerequisites

- Python 3.9+
- Node.js 16+ и npm
- Pulumi CLI (https://www.pulumi.com/docs/get-started/install/)
- Azure CLI (https://docs.microsoft.com/cli/azure/install-azure-cli)
- Docker Desktop or Docker Engine (for backend image building by Pulumi)

## Azure CLI

```bash
az login
az account set --subscription <your-subscription-id>
```

## Локальная разработка (optional)

### Backend

```bash
cd backend
pip install -r requirements.txt
func start --script-root .
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

По умолчанию фронтенд будет обращаться к API по адресу http://localhost:7071/api.
Если нужно изменить, создайте файл `.env.local` в папке frontend и пропишите:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:7071/api
```

## Подготовка Production-стека

1. Перейдите в корень проекта:

    ```bash
    cd <project-root>
    ```

2. Установите Python-зависимости для Pulumi:

    ```bash
    pip install -r requirements.txt
    ```

3. Залогиньтесь в Pulumi и создайте production-стек:

    ```bash
    pulumi login
    pulumi stack init prod
    ```

4. Установите конфигурацию Azure для стека 'prod':

    ```bash
    pulumi config set azure:location <azure-region>             # например EastUS
    pulumi config set azure:resourceGroupName <rg-name>        # по умолчанию agro-monitor-rg
    # Опционально: Установите секреты для API ключей (будут сохранены в App Configuration)
    # pulumi config set --secret planetaryComputerApiKey YOUR_PC_KEY
    # pulumi config set --secret openaiApiKey YOUR_OPENAI_KEY
    ```

## Сборка Frontend

```bash
cd frontend
npm ci
# Установите переменную окружения с URL вашего API перед сборкой
export NEXT_PUBLIC_API_URL=<your-api-url-from-pulumi> 
npm run build
npx next export
```
Если вы используете Windows, команда для установки переменной окружения будет другой (например, `set NEXT_PUBLIC_API_URL=<your-api-url>` или `$env:NEXT_PUBLIC_API_URL="<your-api-url>"` в PowerShell).

После этого в папке `frontend/out/` появится статический сайт.

## Развёртывание инфраструктуры и кода

Теперь развёртывание инфраструктуры, бэкенда и фронтенда происходит одной командой:

```bash
cd <project-root>
pulumi up --stack prod
```

Pulumi автоматически:
- Создаст или обновит инфраструктуру Azure.
- Упакует и развернёт код Azure Functions из папки `backend`.
- Загрузит статические файлы фронтенда из папки `frontend/out` в Storage Account.

Подтвердите изменения и дождитесь завершения.

**Важно:** Перед запуском `pulumi up`, убедитесь, что вы собрали фронтенд с правильным URL API:

1.  Получите URL API (если вы еще не знаете его):
    ```bash
    pulumi stack output functionEndpoint
    ```
2.  Соберите фронтенд:
    ```bash
    cd frontend
    export NEXT_PUBLIC_API_URL=<your-api-url-from-pulumi>
    npm ci
    npm run build
    npx next export
    cd ..
    ```
    (Используйте `set` или `$env:` для Windows)

## Проверка деплоя

- **Backend API**:  
  ```bash
  pulumi stack output functionEndpoint
  ```

- **Frontend**:  
  ```bash
  pulumi stack output frontendUrl
  ```

Теперь ваше приложение запущено в Azure в режиме `prod`.