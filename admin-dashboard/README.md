# Pune Metro AI - Admin Dashboard

This is the React frontend for the Pune Metro AI admin dashboard.

## Prerequisites

- Node.js (v18 or higher)
- npm or yarn

## Getting Started

1.  **Install dependencies:**

    ```bash
    npm install
    ```

2.  **Run the development server:**

    Make sure the FastAPI backend is running on `http://localhost:8000`.

    ```bash
    npm run dev
    ```

    The admin dashboard will be available at `http://localhost:5173`.

## Environment Variables

The API base URL is configured in the `.env` file:

- `VITE_API_BASE_URL`: The base URL for the admin API (e.g., `http://localhost:8000/api/v1/admin`)
