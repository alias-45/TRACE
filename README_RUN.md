# Run TRACE (backend + frontend + FalkorDB Cloud)

1. Keep your existing `.env` in the project root (FALKOR_HOST / PORT / USER / PASS). The backend reads it automatically.
   Delete `backend/.env` (it points at localhost and has a stray uvicorn line).
2. Backend (terminal 1):
   cd backend
   pip install -r requirements.txt
   uvicorn main:app --reload --port 8000
   Check http://localhost:8000/api/health  ->  {"ok": true, "graph": "trace"}
3. Frontend (terminal 2):
   cd frontend
   npm install
   npm run dev
   Open http://localhost:5173 . The sidebar should say "FalkorDB connected".
