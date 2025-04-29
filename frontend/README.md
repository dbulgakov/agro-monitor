# Frontend for Agro Monitor

This is the Next.js frontend application for the Agro Monitor project.

## Development

1.  **Navigate to the frontend directory:**
    ```bash
    cd frontend
    ```
2.  **Install dependencies:**
    ```bash
    npm install
    # or
    yarn install
    ```
3.  **Run the development server:**
    ```bash
    npm run dev
    # or
    yarn dev
    ```
    The application will be available at `http://localhost:3000`.

### Connecting to the Backend

-   **Mock API (Default):** By default, the dev server uses Next.js route handlers under `src/app/api/` to simulate backend responses. This allows for frontend development without a running backend.
-   **Local Backend:** To connect to a locally running backend (e.g., the FastAPI app running on `http://localhost:8000`), create a `.env.local` file in the `frontend/` directory with the following content:
    ```env
    NEXT_PUBLIC_API_URL=http://localhost:8000/
    ```
    Restart the Next.js dev server after creating/modifying `.env.local`.
-   **Deployed Backend:** When deployed, the frontend should ideally be configured to call the deployed backend URL. This is typically handled during the deployment process (see root `README.md`).

## Building for Production (Static Export)

This frontend is designed to be deployed as a set of static files (HTML/CSS/JS) hosted in Azure Blob Storage.

1.  **Build the application:**
    ```bash
    npm run build
    # or
    yarn build
    ```
    This command runs `next build` which generates an optimized production build.

2.  **Export to static files:**
    The `build` script should ideally include `next export` if not already configured in `next.config.js`. If `next export` needs to be run separately:
    ```bash
    # npx next export # Only if not part of `npm run build`
    ```
    This will generate the static HTML, CSS, and JavaScript files in the `out/` directory.

These files in the `out/` directory are what Pulumi uploads to the Azure Storage Account static website hosting during deployment.

## Testing

-   **Unit/Integration Tests:** (Assuming Jest is configured)
    ```bash
    npm test
    # or
    yarn test
    ```
-   **End-to-End Tests:** (Assuming Playwright is configured)
    ```bash
    # First time setup: npx playwright install
    npm run test:e2e # (Or the specific script defined in package.json)
    # or
    yarn test:e2e
    ```
