# Care and Monitoring Platform

This application contains the fall inference and event-care backend, Vue web client, optional headless voice service, and family WeChat mini program.

## Development

- Backend: create a virtual environment, install `backend/requirements.txt`, then run `python run.py` from `analysis_platform/backend`. Default development API port is 5001.
- Web client: run `npm install` and `npm run dev` from `analysis_platform/frontend`; the development server uses port 5173.
- Mini program: import `miniprogram/` into WeChat DevTools. Replace the placeholder API host in `app.js` with your own backend URL and configure a development app ID.
- The main Flask API reads `analysis_platform/.env`. The optional standalone mini API additionally reads `analysis_platform/backend/.env`.
- Optional integrations (camera vendor, speech, notifications, algorithm sync) require local credentials. Algorithm sync is off by default and requires a URL, API key, and matching source salt on both platforms.

Do not use real household video, device identifiers, credentials, or contact details in demo data. `backend/data/` is runtime storage and is excluded from this repository.
