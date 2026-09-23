# Algorithm Iteration Platform

Flask API and Vue 3 dashboard for receiving anonymized fall events, reviewing trends, and exporting permitted analysis data.

## Development

- Backend: install `backend/requirements.txt` and run `python run.py` from `algorithm_platform/backend` (port 5003).
- Frontend: run `npm install` and `npm run dev` from `algorithm_platform/frontend` (port 5174).
- Copy `.env.example` to `.env`; generate local secrets and source-alias salt. Match the API key and source salt in `analysis_platform/.env` only when explicitly enabling synchronization.

The sample event generator is synthetic. The current care platform does not persist inference-time skeletons into its archive, so generated skeletons must remain labeled synthetic. The anonymization contract is documented in `backend/app/services/anonymize.py`.
