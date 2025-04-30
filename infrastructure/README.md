# Azure FastAPI & Next.js Demo Deployment with Pulumi

This Pulumi project deploys a demo application consisting of a Python FastAPI backend (running on Azure Functions) and a Next.js frontend (running on Azure App Service) to Microsoft Azure.

It provisions the necessary infrastructure, including:

*   Azure Resource Group
*   Azure Storage Account (for Functions, Queues, Blobs)
*   Storage Queue (`analysis-queue`)
*   Storage Blob Container (`results`)
*   Azure Application Insights
*   Azure Cosmos DB (Serverless SQL API) Database and Container
*   Azure Functions Consumption Plan (Linux)
*   Azure App Service Plan (Free F1 tier, Linux)
*   Azure Function App (Python) deployed from a Git repository
*   Azure Web App (Node.js/Next.js) deployed from a Git repository

## Prerequisites

Before you begin, ensure you have the following installed and configured:

1.  **Pulumi CLI:** [Install Pulumi](https://www.pulumi.com/docs/install/)
2.  **Azure CLI:** [Install Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli)
3.  **Python:** Version 3.8 or later.
4.  **Pip:** Python package installer.
5.  **Git:** [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
6.  **Node.js and npm:** (Required by the frontend build process in Azure) [Install Node.js and npm](https://nodejs.org/)
7.  **Azure Subscription:** You need an active Azure subscription. [Create one for free](https://azure.microsoft.com/free/).
8.  **Pulumi Account:** You need a Pulumi account to manage state. [Sign up for free](https://app.pulumi.com/).

## Setup

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd <your-repository-name>
    ```

2.  **Navigate to Infrastructure Directory:**
    ```bash
    cd infrastructure
    ```

3.  **Install Python Dependencies:**
    Create a virtual environment (recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows use `venv\Scripts\activate`
    ```
    Install packages:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Log in to Azure:**
    ```bash
    az login
    ```
    Ensure you have selected the correct subscription:
    ```bash
    az account set --subscription "<your-azure-subscription-id>"
    ```

5.  **Log in to Pulumi:**
    ```bash
    pulumi login
    ```
    Follow the prompts to log in to your Pulumi account (this manages your infrastructure state).

6.  **Configure Pulumi Stack:**
    This project uses a Pulumi stack named `dev` by default. Configuration is stored in `Pulumi.dev.yaml`.

    *   Review the `azure-native:location` (e.g., `westeurope`). Change it if needed.
    *   **Crucially, update the Git repository details:** Edit `Pulumi.dev.yaml` and replace the placeholder values for `gitRepoUrl` and `gitBranch` with your actual repository URL and the branch you want to deploy from.
        ```yaml
        config:
          azure-native:location: westeurope
          azure-fastapi-demo:gitRepoUrl: https://github.com/your-username/your-repo.git # <-- REPLACE THIS
          azure-fastapi-demo:gitBranch: main # <-- REPLACE THIS
        ```

## Deployment

1.  **Preview the Deployment:**
    Run `pulumi preview` to see the resources that will be created.
    ```bash
    pulumi preview
    ```

2.  **Deploy the Infrastructure:**
    Run `pulumi up` to create the Azure resources and configure the deployments.
    ```bash
    pulumi up
    ```
    Pulumi will show you a preview and ask for confirmation before provisioning.

    *   **Deployment Trigger:** Pulumi configures Azure App Service (both Functions and Web App) to deploy from your specified Git repository and branch. Azure will automatically pull the code, build the frontend (`npm install && npm run build`), install Python dependencies for the backend, and start the applications.
    *   **Private Repositories:** If your Git repository is private, the initial deployment might fail because Azure App Service needs permission to access it. You might need to configure access credentials manually in the Azure Portal's "Deployment Center" for both the Function App and the Web App after the first `pulumi up` creates the resources. Once configured, subsequent `pulumi up` runs should trigger deployments correctly.
    *   **Repository Structure:** This deployment assumes your repository has a `backend` folder containing the Function App code and a `frontend` folder containing the Next.js code at the root level.

## Accessing the Application

Once the deployment is complete, Pulumi will output the endpoints:

```bash
pulumi stack output
```

You will see outputs like:

*   `backend_endpoint`: The base URL for your FastAPI backend (e.g., `https://fastapidemo-func-app-dev.azurewebsites.net/api`).
*   `frontend_endpoint`: The URL for your Next.js frontend (e.g., `https://fastapidemo-web-app-dev.azurewebsites.net`).

## Cleanup

To remove all resources created by this project, run:

```bash
pulumi destroy
```

Confirm the deletion when prompted.

To remove the stack itself:

```bash
pulumi stack rm dev
``` 