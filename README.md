# Agro Monitor Serverless Application

Это серверлес-приложение для мониторинга сельскохозяйственных полей на основе публичных спутниковых данных.
В проекте используется:

- **Backend**: Azure Functions на Python (развернутый как Docker-контейнер с FastAPI)
- **Frontend**: Next.js React SPA (Static Export)
- **Infrastructure as Code**: Pulumi (Python)
- **CI/CD**: (Optional - Add details if implemented, e.g., GitHub Actions)

## Project Structure

```
.
├── backend/         # Backend: Azure Functions code + FastAPI app + Dockerfile
├── frontend/        # Frontend: Next.js application
├── infrastructure/  # Pulumi code for defining Azure resources
├── __main__.py      # Pulumi entrypoint
├── requirements.txt # Pulumi Python dependencies
├── Pulumi.yaml      # Pulumi project definition
├── Pulumi.dev.yaml  # Pulumi dev stack configuration
└── README.md        # This file
```

## Prerequisites

- Python 3.9+ and pip
- Node.js 18+ and npm (or yarn)
- Pulumi CLI (`brew install pulumi` or see https://www.pulumi.com/docs/get-started/install/)
- Azure CLI (`brew install azure-cli` or see https://docs.microsoft.com/cli/azure/install-azure-cli)
- Docker Desktop or Docker Engine (required for building the backend image locally and by Pulumi)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd agro-monitor
    ```

2.  **Log in to Azure:**
    ```bash
    az login
    az account set --subscription <your-subscription-id>
    ```

3.  **Log in to Pulumi:**
    ```bash
    pulumi login # (Use Azure Blob Storage backend or Pulumi Service)
    ```

4.  **Install Pulumi Dependencies:**
    ```bash
    # Create a virtual environment (recommended)
    python -m venv venv
    source venv/bin/activate # On Windows use `venv\Scripts\activate`

    pip install -r requirements.txt
    ```

## Local Development

### Running the Backend (FastAPI app locally)

Running the backend locally typically involves running the FastAPI server directly, often using Uvicorn. This requires backend dependencies and setting environment variables similar to `local.settings.json`.

```bash
cd backend

# Install backend dependencies (assuming requirements.txt exists in backend/)
pip install -r requirements.txt

# Create/Update local.settings.json (or use .env file + dotenv)
# Mimic required Azure Function App Settings:
# AZURE_STORAGE_ACCOUNT_NAME=<your_dev_storage_account_name>
# ANALYSIS_QUEUE_NAME=analysis-requests
# REPORTS_CONTAINER_NAME=reports
# PROGRESS_CONTAINER_NAME=job-status
# (Ensure Azure credentials are available, e.g., via `az login` for DefaultAzureCredential)

# Run FastAPI app using Uvicorn
# Replace 'app:app' with the actual location of your FastAPI instance if needed
uvicorn app:app --reload --port 8000
```
*Note: The backend running locally via Uvicorn will be available at `http://localhost:8000`. Ensure your frontend points to this URL during local development.* 
*Alternatively, you can try running with Azure Functions Core Tools, but ensure your `function.json` is configured for an ASGI handler if using FastAPI.* 

```bash
# func start # Might require specific configuration for ASGI
```

### Running the Frontend (Next.js dev server)

```bash
cd frontend

# Install frontend dependencies
npm install # or yarn install

# Create .env.local (if needed) to point to local backend
echo "NEXT_PUBLIC_API_URL=http://localhost:8000/" > .env.local

# Start the Next.js development server
npm run dev # or yarn dev
```

The frontend will be available at `http://localhost:3000` and will connect to the backend specified by `NEXT_PUBLIC_API_URL` (or the mock API at `/api` if unset).

## Testing

### Backend Tests (Example: Pytest)

```bash
cd backend
# Ensure test dependencies are installed (e.g., pytest, httpx)
pip install pytest httpx

# Run tests (assuming tests are in a 'tests/' directory)
pytest
```
*(Add specific instructions if test structure differs)*

### Frontend Tests (Example: Jest / Playwright)

```bash
cd frontend

# Run Unit/Integration Tests (Jest)
npm test # or yarn test

# Run End-to-End Tests (Playwright)
# First time setup: npx playwright install
npm run test:e2e # or yarn test:e2e (Assuming script exists in package.json)
```
*(Add specific instructions for running tests)*

## Deployment (Using Pulumi)

Deployment creates/updates the Azure infrastructure and deploys the backend container and frontend static files.

1.  **Select/Create Pulumi Stack:**
    ```bash
    # Choose an existing stack (e.g., dev or prod)
    pulumi stack select dev

    # Or create a new one
    # pulumi stack init my-new-stack
    ```

2.  **Configure Stack (if new):**
    Set required configuration values for the stack. The stack-specific naming is handled in the code, but location might be needed.
    ```bash
    # Example for a new stack
    pulumi config set azure-native:location <your-azure-region> # e.g., westus2

    # Optional: Set secrets via App Configuration (if needed by backend)
    # pulumi config set --secret MySecretSetting mySecretValue
    ```
    *Note: Resource group name, storage account names, etc., are now generated dynamically based on the stack name.* 

3.  **Build Frontend Static Files:**
    Pulumi needs the static output from the frontend build.
    ```bash
    cd frontend
npm install # or yarn install
npm run build # This should generate static files in frontend/out/
cd ..
    ```
    *Important:* The `build` script in `frontend/package.json` should generate a static export suitable for hosting on Blob Storage (likely using `next build && next export`). `NEXT_PUBLIC_API_URL` is **not** needed at build time for static export if API calls are made client-side relative to the deployed backend URL.

4.  **Deploy with Pulumi:**
    Run `pulumi up` from the project root.
    ```bash
    # Ensure you are in the project root directory
    # Ensure your virtual environment is active

    pulumi up
    ```
    Pulumi will show a preview of the changes. Review and confirm to proceed.

5.  **Get Outputs:**
    After successful deployment, get the endpoints:
    ```bash
    # Get outputs for the current stack (e.g., 'dev')
    pulumi stack output

    # Specific outputs (names are now stack-suffixed):
    pulumi stack output functionEndpoint_dev
    pulumi stack output frontendUrl_dev
    ```

## Cleaning Up

To remove all resources created by a specific stack:

```bash
# Ensure the correct stack is selected
pulumi stack select <stack-to-destroy>

pulumi destroy

# Optionally remove the stack history
pulumi stack rm <stack-to-destroy>
```