# Agro Monitor Project

This project contains the source code and infrastructure definition for the Agro Monitor application. It's composed of a Python backend (Azure Functions), a Node.js frontend (Azure App Service), and the necessary Azure infrastructure managed by Pulumi.

## Project Structure

- `backend/`: Contains the Python code for the Azure Function App (FastAPI).
- `frontend/`: Contains the Node.js/Next.js code for the web application.
- `infrastructure/`: Contains the Pulumi code (Python) for defining Azure resources.
- `.github/workflows/`: Contains GitHub Actions workflows for CI/CD.
- `docker-compose.yml`: Configuration for running dependent services (like Azurite) locally.

## Prerequisites

- [Node.js](https://nodejs.org/) (check `frontend/` for specific version, likely LTS)
- [Python](https://www.python.org/downloads/) (version 3.11+)
- [Pip](https://pip.pypa.io/en/stable/installation/) & `venv`
- [Pulumi CLI](https://www.pulumi.com/docs/install/)
- [Azure CLI](https://docs.microsoft.com/cli/azure/install-azure-cli)
- [Docker](https://www.docker.com/products/docker-desktop/) (for running Azurite locally)
- An Azure Account

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd agro-monitor
    ```

2.  **Login to Azure:**
    ```bash
    az login
    ```

3.  **Install Backend Dependencies:**
    ```bash
    cd backend
    python -m venv .venv
    source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
    pip install -r requirements.txt
    deactivate
    cd ..
    ```

4.  **Install Frontend Dependencies:**
    ```bash
    cd frontend
    # Assuming npm, adjust if using yarn or pnpm
    npm install
    cd ..
    ```

5.  **Install Infrastructure Dependencies:**
    ```bash
    cd infrastructure
    python -m venv .venv
    source .venv/bin/activate # On Windows use `.venv\Scripts\activate`
    pip install -r requirements.txt
    deactivate
    cd ..
    ```

## Running Locally

1.  **Start Local Azure Services (Azurite):**
    Use Docker Compose to start a local Azurite instance for Azure Storage emulation.
    ```bash
    docker-compose up -d
    ```
    This will provide local endpoints for Blob Storage and Queues.

2.  **Configure Backend:**
    - Create a `local.settings.json` file in the `backend/` directory (refer to Azure Functions documentation).
    - Set environment variables, particularly `AzureWebJobsStorage` to use the local Azurite connection string (`UseDevelopmentStorage=true`).

3.  **Run Backend:**
    Navigate to the `backend/` directory and run the Azure Functions host locally (ensure the virtual environment is activated).
    ```bash
    cd backend
    source .venv/bin/activate # Or Windows equivalent
    func start
    ```

4.  **Configure Frontend:**
    - Set necessary environment variables (e.g., pointing to the local backend API endpoint). Refer to `frontend/README.md` if available.

5.  **Run Frontend:**
    Navigate to the `frontend/` directory and start the development server.
    ```bash
    cd frontend
    npm run dev # Or the appropriate script
    ```

## Infrastructure Deployment (Pulumi)

The Azure infrastructure is managed using Pulumi in the `infrastructure/` directory.

1.  **Navigate to Infrastructure Directory:**
    ```bash
    cd infrastructure
    ```

2.  **Activate Python Environment:**
    ```bash
    source .venv/bin/activate # Or Windows equivalent
    ```

3.  **Login to Pulumi:**
    ```bash
    pulumi login
    ```

4.  **Set Configuration:**
    Configure the required stack settings. Refer to `infrastructure/README.md` for details on required values like location, repository URL, etc.
    ```bash
    # Example:
    pulumi config set location <your-azure-location>
    pulumi config set gitRepoUrl <your-github-repo-url>
    pulumi config set gitBranch main
    # Add other necessary configs...
    ```

5.  **Preview and Deploy:**
    ```bash
    pulumi preview
    pulumi up
    ```
    Pulumi will provision the necessary Azure resources. Note the outputs, especially the app names needed for deployment secrets.

## Application Deployment (GitHub Actions)

Deployments are handled via GitHub Actions defined in `.github/workflows/`.

1.  **Configure Secrets:**
    Ensure the following secrets are configured in your GitHub repository settings (`Settings > Secrets and variables > Actions`):
    - `AZURE_CREDENTIALS`: Azure Service Principal credentials with permissions to deploy.
    - `FUNC_API_APP_NAME`: The name of the deployed Function App (from Pulumi output).
    - `FRONTEND_APP_NAME`: The name of the deployed Frontend App Service (from Pulumi output).
    - `FUNC_API_PUBLISH_PROFILE`: The publish profile for the Function App (can be downloaded from Azure Portal or obtained via Azure CLI).
    - `FRONTEND_PUBLISH_PROFILE`: The publish profile for the Frontend App Service.

2.  **Trigger Deployment:**
    Pushing changes to the `main` branch (or the configured trigger branch) will automatically trigger the deployment workflows.

## Cleanup

1.  **Destroy Infrastructure:**
    To remove all Azure resources created by Pulumi:
    ```bash
    cd infrastructure
    source .venv/bin/activate # Or Windows equivalent
    pulumi destroy
    ```

2.  **Stop Local Services:**
    ```bash
    docker-compose down
    ``` 