import os
from typing import Dict

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import signals
from state_store import check_password, store

SECRET = os.getenv("WEB_SECRET_KEY", "change-me")

app = FastAPI(title="Future Trading Bot Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SECRET)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


async def require_auth(request: Request):
    if not request.session.get("auth"):
        raise HTTPException(status_code=401)


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("auth"):
        return _redirect_to_login()
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if not check_password(password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})
    request.session["auth"] = True
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return _redirect_to_login()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, auth=Depends(require_auth)):
    state = store.get_state()
    return templates.TemplateResponse("dashboard.html", {"request": request, "state": state, "title": "Dashboard"})


@app.post("/commands")
async def commands(request: Request, action: str = Form(...), auth=Depends(require_auth)):
    if action not in {"run", "stop", "retrain"}:
        raise HTTPException(status_code=400, detail="Invalid action")
    meta: Dict[str, str] = {}
    if action == "run":
        store.update_state(bot_status="running", last_action="run")
    elif action == "stop":
        store.update_state(bot_status="stopped", last_action="stop")
    else:
        store.update_state(last_retrain="just now", last_action="retrain")
    store.record_command(action, meta)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/indicators")
async def update_indicators(
    request: Request,
    min_price_model_proba: float = Form(...),
    min_news_sentiment: float = Form(...),
    max_news_sentiment_for_short: float = Form(...),
    auth=Depends(require_auth),
):
    updates = {
        "min_price_model_proba": min_price_model_proba,
        "min_news_sentiment": min_news_sentiment,
        "max_news_sentiment_for_short": max_news_sentiment_for_short,
    }
    store.update_indicators(updates)
    store.record_command("indicator_update", updates)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/news-refresh")
async def news_refresh(request: Request, auth=Depends(require_auth)):
    cfg = {
        "lookback_hours": 6,
        "max_headlines": 6,
        "source": os.getenv("NEWS_SOURCE", "cryptopanic"),
    }
    try:
        sentiment = signals.news_sentiment(cfg)
        last_err = None
    except Exception as exc:  # pragma: no cover - defensive
        sentiment = 0.0
        last_err = str(exc)
        store.record_news_health(cfg["source"], 0, last_err)
    store.record_command("news_refresh", {"sentiment": sentiment})
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/api/state")
async def state(auth=Depends(require_auth)):
    return JSONResponse(store.get_state())
