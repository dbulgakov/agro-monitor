# Agro Monitor Backend

Бэкенд-сервис для анализа сельскохозяйственных полей с использованием спутниковых снимков.
Реализован на Azure Functions (Python V2 model) с использованием FastAPI для обработки HTTP-запросов.

## Функционал

*   Запуск анализа поля по геометрии (точка/полигон) и параметрам.
*   Получение отчета с результатами анализа (NDVI, RGB, карта зон стресса) и текстовыми рекомендациями от AI (OpenAI).
*   Отслеживание прогресса выполнения задачи через Server-Sent Events (SSE).

## Структура Проекта

```
backend/
├── functions/
│   ├── HttpApi/                # HTTP-триггер для FastAPI (/api/{*route})
│   │   ├── function.json
│   │   └── __init__.py
│   ├── ProcessQueueFunction/   # Queue-триггер для обработки задач анализа
│   │   ├── function.json
│   │   └── main.py
│   ├── shared_code/            # Общий код (схемы, хелперы, модели)
│   │   ├── __init__.py
│   │   ├── helpers.py
│   │   └── schemas.py
│   └── __init__.py
├── .funcignore
├── .gitignore
├── app.py                      # FastAPI приложение (экземпляр и роутеры)
├── host.json
├── local.settings.json.template # Шаблон для локальных настроек
├── requirements.txt
└── README.md
```
*(Структура может включать и другие функции, например, для SSE или получения отчетов, если они вынесены отдельно от FastAPI)*

## Настройка и Запуск

1.  **Клонируйте репозиторий.**
2.  **Установите Azure Functions Core Tools:** [Инструкция](https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local)
3.  **Создайте и активируйте виртуальное окружение:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Linux/macOS
    # .\.venv\Scripts\activate # Windows
    ```
4.  **Установите зависимости:**
    ```bash
    pip install -r requirements.txt
    ```
    *Примечание: Установка `rasterio` и `shapely` может потребовать наличия системных библиотек GDAL и GEOS. См. документацию этих пакетов для вашей ОС.* 
5.  **Настройте переменные окружения:**
    *   Скопируйте `local.settings.json.template` в `local.settings.json`.
    *   Заполните **обязательные** значения в `local.settings.json`:
        *   `AzureWebJobsStorage`: Connection string вашего Azure Storage Account (требуется для работы хоста функций).
        *   `AZURE_STORAGE_CONNECTION_STRING`: Connection string для доступа к очередям и блобам из кода приложения (может быть той же, что и `AzureWebJobsStorage`).
        *   `OPENAI_API_KEY`: Ваш API ключ OpenAI.
    *   Убедитесь, что соответствующие контейнеры (по умолчанию `reports`, `images`) и очередь (по умолчанию `analysis-requests`) существуют в вашем Azure Storage Account.
    *   (Опционально) Настройте `OPENAI_ENDPOINT`, `OPENAI_API_VERSION`, `OPENAI_MODEL`, если используете Azure OpenAI или специфичную модель.
6.  **Запустите локально:**
    ```bash
    func host start  # Или просто func start
    ```
    Сервер будет доступен по адресу `http://localhost:7071`. Ваши FastAPI эндпоинты будут доступны по `http://localhost:7071/{*route}` (без префикса `/api` по умолчанию, так как мы настроили `route` в `function.json`).

## API Эндпоинты

Эндпоинты определяются в роутерах FastAPI, подключенных в `app.py`. Например:

*   `POST /analyze`: Запуск нового анализа.
*   `GET /report/{jobId}`: Получение отчета по `jobId`.
*   `GET /progress/{jobId}`: Подписка на Server-Sent Events для отслеживания прогресса.

(Точные пути зависят от того, как вы определите роутеры в FastAPI).

## Развертывание в Azure

Проект можно развернуть в Azure Functions стандартными способами:
*   Через VS Code с расширением Azure Functions.
*   Используя Azure Functions Core Tools (`func azure functionapp publish <AppName>`).
*   Через CI/CD пайплайны (GitHub Actions, Azure DevOps и т.д.).

**Важно:** 
*   Убедитесь, что в настройках приложения Azure Functions (Application Settings) установлены **все** необходимые переменные окружения, как в `local.settings.json`.
*   Проверьте совместимость версий `rasterio` и `shapely` (и их зависимостей GDAL/GEOS) с окружением выполнения Azure Functions Linux. Возможно, потребуется использовать кастомный Docker-образ для сборки с нужными системными библиотеками. Подробнее см. [документацию Azure Functions по пользовательским образам](https://docs.microsoft.com/en-us/azure/azure-functions/functions-how-to-custom-container). 