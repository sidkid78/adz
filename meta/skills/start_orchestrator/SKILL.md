# Start Full-Stack Dev Servers

## When to use this
Use this skill when you need to run both the frontend development server and backend API server concurrently and verify both are operational before proceeding with user tasks or automated testing.

## Instructions
1. Inspect the workspace configuration (e.g., `package.json`, `Procfile`, `Makefile`, or environment files) to identify the launch commands, default ports, and health check endpoints or readiness log patterns for both servers.
2. Check if the required ports are available, terminating any conflicting processes if necessary.
3. Start the backend API server process in a managed background task.
4. Start the frontend development server process in a separate managed background task.
5. Poll the backend readiness signal (such as an HTTP health endpoint or stdout line indicating the server is listening) until it confirms ready or times out.
6. Poll the frontend readiness signal (such as an HTTP GET request to the local dev URL or stdout ready message) until it confirms ready or times out.
7. If either server fails to start or report ready within the timeout, capture the process logs, terminate both background processes, and report the error details.
8. Once both servers respond successfully, return control with a summary of the active server URLs and process IDs.