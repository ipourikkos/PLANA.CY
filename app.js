# Cyprus Planning AI Web Interface

## 1. Keep the API running

From the main `cyprus-planning-ai` folder:

```powershell
.\.venv\Scripts\python.exe -m uvicorn planning_api:app --reload
```

Leave that PowerShell window open.

## 2. Start the web interface

Open a SECOND PowerShell window.

Move into this `web` folder, then run:

```powershell
python -m http.server 3000
```

## 3. Open the app

Open:

http://127.0.0.1:3000

The interface expects the API at:

http://127.0.0.1:8000
