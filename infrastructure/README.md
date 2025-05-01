# Agro Monitor Infrastructure

This project uses Pulumi to deploy the Agro Monitor application to Azure. The infrastructure includes:
- Azure Function App (Consumption Plan Y1) for the FastAPI backend
- Azure Static Web App (Free SKU) for the Next.js frontend
- Azure Storage Account for blobs and queues
- Application Insights for monitoring

## Prerequisites

1. Install [Pulumi](https://www.pulumi.com/docs/install/)
2. Install [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli)
3. Install [Python 3.11+](https://www.python.org/downloads/)
4. Have an [Azure account](https://azure.microsoft.com/en-us/free/) with Free Tier subscription

## Setup

1. Login to Azure CLI:
```bash
az login
```

2. Create a new Python virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure Pulumi:
```bash
pulumi login
```

5. Set configuration values:
```bash
pulumi config set azure-fastapi-demo:location northeurope
pulumi config set azure-fastapi-demo:openaiApiKey your-openai-api-key

# Optional: If you want Pulumi to automatically set up GitHub integration for the Static Web App, supply a GitHub PAT
pulumi config set --secret azure-fastapi-demo:repoToken ghp_XXX

az provider register --namespace Microsoft.OperationalInsights
```

## Deployment

1. Preview the deployment:
```bash
pulumi preview
```

2. Deploy the infrastructure:
```bash
pulumi up
```

3. After deployment completes, Pulumi will output the following:
- Backend endpoint (Function App URL)
- Frontend endpoint (Static Web App URL)
- Storage account name
- Cosmos DB endpoint
- Queue and container names

## Post-Deployment

1. The Function App will automatically deploy the backend from your GitHub repository
2. The Web App will automatically deploy the frontend from the same repository
3. You can monitor the deployment progress in the Azure Portal

## Cleanup

To remove all resources:
```bash
pulumi destroy
```

## Notes

- All resources are configured for Azure Free Tier
- The deployment uses serverless options where possible to minimize costs
- Application Insights is included for basic monitoring
- The infrastructure includes retry policies for better reliability
- This is a single environment setup - all changes will affect the production environment 