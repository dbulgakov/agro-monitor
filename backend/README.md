# Agro Monitor Backend

Бэкенд-сервис для анализа сельскохозяйственных полей с использованием спутниковых снимков Sentinel-2.
Реализован на Azure Functions (Python V2 programming model).

## Функционал

*   Запуск анализа поля по геометрии (точка/полигон), временному интервалу и другим параметрам.
*   Асинхронная обработка задач анализа через Azure Storage Queue.
*   Получение финального отчета с результатами анализа (статистика NDVI, ссылки на изображения RGB, NDVI, карты зон стресса) и текстовыми рекомендациями от AI (OpenAI).
*   Отслеживание прогресса выполнения задачи.
*   Health-check эндпоинт для мониторинга состояния сервиса.

## Структура Проекта

```
backend/
├── functions/
│   ├── AnalyzeFunction/        # HTTP: POST /analyze - Запуск анализа
│   │   ├── function.json
│   │   └── main.py
│   ├── ProcessQueueFunction/   # Queue Trigger: Обработка сообщений из очереди анализа
│   │   ├── function.json
│   │   └── main.py
│   ├── ProgressFunction/       # HTTP: GET /progress/{jobId} - Статус и прогресс задачи
│   │   ├── function.json
│   │   └── main.py 
│   ├── ReportFunction/         # HTTP: GET /report/{jobId} - Получение финального отчета
│   │   ├── function.json
│   │   └── main.py
│   ├── HealthCheckFunction/    # HTTP: GET /healthz - Проверка состояния сервиса
│   │   ├── function.json
│   │   └── main.py
│   ├── shared_code/            # Общий код (схемы Pydantic, хелперы)
│   │   ├── __init__.py
│   │   ├── helpers.py
│   │   └── schemas.py
│   └── __init__.py
├── .funcignore               # Файлы, исключаемые из сборки
├── .gitignore
├── host.json                 # Конфигурация хоста функций (логи, очереди, concurrency)
├── local.settings.json.template # Шаблон для локальных настроек
├── requirements.txt          # Зависимости Python
└── README.md
```

## Настройка и Запуск Локально

1.  **Клонируйте репозиторий.**
2.  **Установите Azure Functions Core Tools:** [Инструкция](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
3.  **Создайте и активируйте виртуальное окружение:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Linux/macOS
    # .\.venv\Scripts\activate # Windows PowerShell
    # .\.venv\Scripts\activate.bat # Windows Cmd
    ```
4.  **Установите зависимости:**
    ```bash
    pip install -r requirements.txt
    ```
    *Примечание: Установка `rasterio` и `shapely` может потребовать наличия системных библиотек GDAL и GEOS. См. документацию этих пакетов или используйте пакеты с pre-built wheels, если доступны для вашей ОС/архитектуры (например, с `pip install rasterio shapely --find-links=...` или через менеджеры пакетов типа `conda`).*
5.  **Настройте переменные окружения:**
    *   Скопируйте `local.settings.json.template` в `local.settings.json`.
    *   Заполните **обязательные** значения в `local.settings.json`:
        *   `AzureWebJobsStorage`: Connection string вашего Azure Storage Account (требуется для работы хоста функций, очередей, блобов по умолчанию).
        *   `REPORTS_CONTAINER_NAME`: Имя контейнера в Blob Storage для хранения JSON-отчетов и статусов (например, `reports`).
        *   `IMAGES_CONTAINER_NAME`: Имя контейнера в Blob Storage для хранения сгенерированных изображений (например, `images`).
        *   `ANALYSIS_QUEUE_NAME`: Имя очереди в Storage Queue для задач анализа (например, `analysis-requests`).
        *   `OPENAI_API_KEY`: Ваш API ключ OpenAI (если хотите использовать генерацию рекомендаций).
    *   (Опционально) `APPINSIGHTS_INSTRUMENTATIONKEY`: Ключ инструментирования Application Insights для включения мониторинга и логирования в Azure.
    *   (Опционально) Настройте `OPENAI_MODEL` (например, `gpt-4o`) или другие параметры OpenAI.
    *   Убедитесь, что соответствующие контейнеры и очередь существуют в вашем Azure Storage Account.
6.  **Запустите локально:**
    ```bash
    func start
    ```
    Сервер будет доступен по адресу `http://localhost:7071`. Эндпоинты функций будут доступны по маршрутам, указанным в их `function.json`.

## Использование API

### 1. Запуск анализа: `POST /analyze`

Отправьте POST-запрос на `http://localhost:7071/analyze` (или URL в Azure) с телом JSON:

**Пример тела запроса (Полигон):**
```json
{
  "area": {
    "type": "Feature",
    "geometry": {
      "type": "Polygon",
      "coordinates": [
        [
          [ 37.6, 55.7 ],
          [ 37.7, 55.7 ],
          [ 37.7, 55.8 ],
          [ 37.6, 55.8 ],
          [ 37.6, 55.7 ]
        ]
      ]
    },
    "properties": {}
  },
  "frequency": "single",
  "ndvi_threshold": 0.3,
  "date_range": "2023-07-01/2023-07-31",
  "max_cloud_cover": 20,
  "crop_type": "Wheat"
}
```

**Пример тела запроса (Точка):**
```json
{
  "area": {
    "type": "Feature",
    "geometry": {
      "type": "Point",
      "coordinates": [ 37.65, 55.75 ]
    },
    "properties": {}
  },
  "frequency": "single",
  "ndvi_threshold": 0.3,
  "date_range": "2023-07-01/2023-07-31",
  "max_cloud_cover": 20,
  "crop_type": "Corn"
}
```

**Параметры:**
*   `area`: GeoJSON Feature (Point или Polygon) описывающий область интереса.
*   `frequency`: Частота анализа (пока поддерживается только `single`).
*   `ndvi_threshold`: Порог NDVI для определения зон стресса (0-1).
*   `date_range`: Временной интервал для поиска снимков в формате `YYYY-MM-DD/YYYY-MM-DD`.
*   `max_cloud_cover`: Максимальный процент облачности снимка (0-100).
*   `crop_type`: Тип культуры (строка, используется для AI рекомендаций).

**Успешный ответ (202 Accepted):**
```json
{
  "jobId": "<уникальный_идентификатор_задачи>"
}
```
Этот `jobId` используется для получения статуса и отчета.

**Ошибка (400 Bad Request):**
```json
{
  "message": "Invalid request body",
  "details": [ /* ... массив ошибок валидации Pydantic ... */ ]
}
```

### 2. Получение статуса: `GET /progress/{jobId}`

Запросите `http://localhost:7071/progress/<jobId>`.

**Пример ответа:**
```json
{
  "jobId": "<jobId>",
  "status": "PROCESSING", // PENDING, PROCESSING, COMPLETED, FAILED
  "progress": 40, // Процент выполнения (0-100), -1 при ошибке
  "message": "Calculating NDVI and generating images", // Текущий шаг или сообщение об ошибке
  "timestamp": 1678886400.123
}
```

### 3. Получение отчета: `GET /report/{jobId}`

Запросите `http://localhost:7071/report/<jobId>`.

**Пример ответа (успешное завершение, 200 OK):**
```json
{
  "jobId": "<jobId>",
  "status": "COMPLETED",
  "summary": "Overall field health appears moderate... [AI Recommendations] ...",
  "snapshotImageUrl": "<URL_к_RGB_изображению>",
  "ndviImageUrl": "<URL_к_NDVI_изображению>",
  "stressZoneImageUrl": "<URL_к_изображению_зон_стресса>",
  "input_parameters": { /* ... копия входных параметров ... */ },
  "processing_details": {
    "image_id": "S2A_MSIL2A_...",
    "image_date": "2023-07-15",
    "image_cloud_cover": 15.5,
    "avg_ndvi": 0.65,
    "stress_percentage": 12.3
  }
}
```

**Пример ответа (задача еще не завершена, 202 Accepted):**
```json
{
    "jobId": "<jobId>",
    "status": "PROCESSING", // или PENDING
    "summary": "Calculating NDVI and generating images" // Текущий статус
}
```

**Пример ответа (ошибка выполнения, статус FAILED в отчете, 200 OK):**
```json
{
    "jobId": "<jobId>",
    "status": "FAILED",
    "summary": "Processing failed: No suitable Sentinel-2 images found...",
    "input_parameters": { /* ... */ }
    // Остальные поля могут отсутствовать или быть null
}
```

**Пример ответа (задача не найдена, 404 Not Found):**
```json
{
  "message": "Report or status for Job ID '<jobId>' not found."
}
```

### 4. Проверка состояния сервиса: `GET /healthz`

Запросите `http://localhost:7071/healthz`.

**Пример ответа (здоров):**
```json
{
    "status": "healthy",
    "checks": {
        "blob_storage_reports": "connected",
        "blob_storage_images": "connected",
        "queue_storage": "connected"
    }
}
```

**Пример ответа (не здоров):**
```json
{
    "status": "unhealthy",
    "checks": {
        "blob_storage_reports": "failed: ClientAuthenticationError",
        "blob_storage_images": "failed: ClientAuthenticationError",
        "queue_storage": "connected"
    }
}
```

## Развертывание в Azure

Проект можно развернуть в Azure Functions стандартными способами:
*   Через VS Code с расширением Azure Functions.
*   Используя Azure Functions Core Tools (`func azure functionapp publish <AppName>`).
*   Через CI/CD пайплайны (GitHub Actions, Azure DevOps и т.д.).

**Важно при развертывании:**
*   Убедитесь, что в настройках приложения Azure Functions (Application Settings / Configuration) установлены **все** необходимые переменные окружения (`AzureWebJobsStorage`, `REPORTS_CONTAINER_NAME`, и т.д.).
*   Проверьте совместимость версий `rasterio` и `shapely` (и их системных зависимостей GDAL/GEOS) с окружением выполнения Azure Functions Linux App Service Plan. Если возникают проблемы с установкой или выполнением, может потребоваться [использование пользовательского Docker-образа](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-custom-container?pivots=programming-language-python) для сборки приложения с нужными системными библиотеками.