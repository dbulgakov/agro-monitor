# Agro Monitor Backend

Бэкенд-сервис для анализа сельскохозяйственных полей с использованием спутниковых снимков.
Реализован на Azure Functions (Python V2 model).

## Функционал

*   Запуск анализа поля по геометрии (точка/полигон) и параметрам.
*   Получение отчета с результатами анализа (NDVI, RGB, карта зон стресса) и текстовыми рекомендациями от AI (OpenAI).
*   Отслеживание прогресса выполнения задачи через Server-Sent Events (SSE).

## Структура Проекта

```
backend/
├── functions/                  # Основная папка с функциями
│   ├── AnalyzeFunction/        # HTTP-триггер для запуска анализа (/api/analyze)
│   │   ├── function.json
│   │   └── main.py
│   ├── ProcessQueueFunction/   # Queue-триггер для обработки задач анализа
│   │   ├── function.json
│   │   └── main.py
│   ├── ProgressFunction/       # HTTP-триггер для SSE прогресса (/api/progress/{jobId})
│   │   ├── function.json
│   │   └── main.py
│   ├── ReportFunction/         # HTTP-триггер для получения отчета (/api/report/{jobId})
│   │   ├── function.json
│   │   └── main.py
│   ├── shared_code/            # Общий код (схемы, хелперы)
│   │   ├── __init__.py
│   │   ├── helpers.py
│   │   └── schemas.py
│   └── __init__.py
├── .funcignore                 # Файлы, игнорируемые Azure Functions Core Tools
├── host.json                   # Конфигурация хоста функций (логирование, таймауты и т.д.)
├── local.settings.json.template # Шаблон для локальных настроек
├── requirements.txt            # Зависимости Python
└── README.md                   # Этот файл
```

## Настройка и Запуск

1.  **Установите зависимости:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Настройте переменные окружения:**
    *   Скопируйте `local.settings.json.template` в `local.settings.json`.
    *   Заполните значения в `local.settings.json`:
        *   `AzureWebJobsStorage`: Connection string вашего Azure Storage Account.
        *   `OPENAI_API_KEY`: Ваш API ключ OpenAI.
        *   (Опционально) `REPORTS_CONTAINER_NAME`, `IMAGES_CONTAINER_NAME`, `ANALYSIS_QUEUE_NAME`, `OPENAI_MODEL`.
    *   Убедитесь, что соответствующие контейнеры (`reports`, `images`) и очередь (`analysis-requests` или указанное вами имя) существуют в вашем Azure Storage Account.
3.  **Запустите локально (используя Azure Functions Core Tools):**
    ```bash
    func start
    ```

## API Эндпоинты

*   `POST /api/analyze`: Запуск нового анализа.
    *   Тело запроса: см. `StartAnalysisPayload` в `shared_code/schemas.py`.
    *   Ответ: `StartAnalysisResponse` с `jobId`.
*   `GET /api/report/{jobId}`: Получение отчета по `jobId`.
    *   Ответ: `ReportData`, если статус `COMPLETED`, иначе отчет с текущим статусом.
*   `GET /api/progress/{jobId}`: Подписка на Server-Sent Events для отслеживания прогресса.

## Развертывание в Azure

Проект можно развернуть в Azure Functions стандартными способами:
*   Через VS Code с расширением Azure Functions.
*   Используя Azure Functions Core Tools (`func azure functionapp publish <AppName>`).
*   Через CI/CD пайплайны (GitHub Actions, Azure DevOps и т.д.).

**Важно:** Убедитесь, что в настройках приложения Azure Functions (Application Settings) установлены те же переменные окружения, что и в `local.settings.json` (`AzureWebJobsStorage`, `OPENAI_API_KEY` и т.д.). Также убедитесь, что среда выполнения Azure Functions имеет доступ к системным библиотекам, необходимым для `rasterio` и `shapely` (GDAL, GEOS), если используете не стандартный образ. 