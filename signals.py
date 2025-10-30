import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from sklearn.linear_model import LogisticRegression
from ta.momentum import RSIIndicator
from ta.trend     import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

# ---------- Candles & features ----------
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ret1"] = df["close"].pct_change()
    df["ret3"] = df["close"].pct_change(3)
    df["ret6"] = df["close"].pct_change(6)

    df["rsi"]    = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"]  = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"]  = EMAIndicator(df["close"], window=50).ema_indicator()
    macd = MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"]     = macd.macd()
    df["macd_sig"] = macd.macd_signal()
    bb            = BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_low"]   = bb.bollinger_lband()
    df["bb_high"]  = bb.bollinger_hband()
    df["atr"]      = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    df["ema_spread"]   = (df["ema20"] - df["ema50"]) / df["close"]
    df["price_bb_pos"] = (df["close"] - df["bb_low"]) / (df["bb_high"] - df["bb_low"])

    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    return df

def make_label(df: pd.DataFrame, horizon: int = 3) -> pd.Series:
    fwd = df["close"].shift(-horizon) / df["close"] - 1.0
    return (fwd > 0).astype(int)

def price_model_signal(df: pd.DataFrame, horizon_bars: int, min_proba: float) -> tuple[float, int]:
    df_feat = build_features(df)
    y       = make_label(df_feat, horizon_bars)
    X       = df_feat[["ret1","ret3","ret6","rsi","ema_spread","macd","macd_sig","price_bb_pos","atr"]]
    y       = y.iloc[:len(X)]
    X       = X.iloc[:len(y)]
    if len(X) < 50:
        return 0.5, 0
    clf     = LogisticRegression(max_iter=200)
    clf.fit(X.iloc[:-1], y.iloc[:-1])
    proba_up = float(clf.predict_proba(X.iloc[[-1]])[0][1])
    pred_label = 1 if proba_up >= 0.5 else 0

    if proba_up >= min_proba:
        return proba_up, 1
    elif (1 - proba_up) >= min_proba:
        return proba_up, 0
    else:
        return proba_up, -1

# ---------- News & sentiment (Hugging Face Inference Providers) ----------
def _hf_sentiment_finbert(texts: list[str], hf_token: str) -> list[float]:
    if not texts or not hf_token:
        return []
    # NEW endpoint for Inference Providers
    url     = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"
    headers = {"Authorization": f"Bearer {hf_token}"}
    scores  = []
    for t in texts:
        try:
            resp = requests.post(url, headers=headers, json={"inputs": t}, timeout=20)
            resp.raise_for_status()
            out  = resp.json()
            print("DEBUG HF output:", out)

            # Map probabilities: assume out is list of label‐prob dicts
            if isinstance(out, list) and len(out)>0 and isinstance(out[0], dict):
                probs     = out[0]
                # Not always dict of dicts; but for example format: [{'label':'positive','score':0.54}, …]
                pos_score = next((x['score'] for x in out[0] if x.get('label','').lower()=='positive'), 0.0)
                neg_score = next((x['score'] for x in out[0] if x.get('label','').lower()=='negative'), 0.0)
                # neutral ignored (implicitly zero contribution)
                value     = pos_score - neg_score
                scores.append(value)
            elif isinstance(out, dict) and "label" in out and "score" in out:
                label = out["label"].lower()
                sc    = float(out["score"])
                if "positive" in label:
                    scores.append(+sc)
                elif "negative" in label:
                    scores.append(-sc)
                else:
                    scores.append(0.0)
            else:
                scores.append(0.0)
        except Exception as e:
            print(f"⚠️ HF inference error for text '{t[:50]}...': {e}")
            scores.append(0.0)
    return scores

def _fetch_news_cryptopanic(api_key: str, lookback_hours: int, max_headlines: int, currency: str = "ETH") -> list[str]:
    if not api_key:
        print("⚠️ No CryptoPanic API key.")
        return []
    url    = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": api_key,
        "public":     "true",
        "currencies": currency,
        "kind":       "news",
        "filter":     "rising,hot,important",
    }
    try:
        r       = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data    = r.json()
        print("DEBUG CryptoPanic keys:", list(data.keys()))
        results = data.get("results", [])
        print(f"DEBUG CryptoPanic found {len(results)} result items")
    except Exception as e:
        print(f"⚠️ CryptoPanic fetch error: {e}")
        return []

    titles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    for idx, item in enumerate(results):
        title = item.get("title") or item.get("slug") or ""
        title = title.strip()
        if not title:
            continue
        pub = item.get("published_at") or item.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(pub.replace("Z","+00:00"))
        except Exception:
            dt = None
        if idx < 3:
            print(f"DEBUG item[{idx}]: title='{title[:50]}', published='{pub}'")
        if dt is None or dt >= cutoff:
            titles.append(title)
        else:
            titles.append(title + " (old)")
        if len(titles) >= max_headlines:
            break
    print(f"✅ CryptoPanic returned {len(titles)} headlines")
    return titles

def _fetch_news_newsapi(api_key: str, lookback_hours: int, max_headlines: int) -> list[str]:
    if not api_key:
        return []
    q       = "Ethereum OR ETH price OR crypto market"
    from_ts = int((datetime.utcnow() - timedelta(hours=lookback_hours)).timestamp())
    url     = "https://newsapi.org/v2/everything"
    params  = {
        "q":        q,
        "pageSize": max_headlines,
        "from":     datetime.utcfromtimestamp(from_ts).isoformat()+"Z",
        "sortBy":   "publishedAt",
        "apiKey":   api_key,
        "language":"en"
    }
    try:
        r        = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        titles   = [(a.get("title") or "").strip() for a in articles if a.get("title")]
        print(f"✅ NewsAPI returned {len(titles)} headlines")
        return titles[:max_headlines]
    except Exception as e:
        print(f"⚠️ NewsAPI fetch error: {e}")
        return []

def news_sentiment(cfg_news: dict) -> float:
    lookback = int(cfg_news.get("lookback_hours", 6))
    maxh     = int(cfg_news.get("max_headlines", 12))
    source   = (cfg_news.get("source", "cryptopanic")).lower()
    cp_key   = os.getenv("CRYPTOPANIC_API_KEY", "")
    na_key   = os.getenv("NEWSAPI_KEY", "")
    hf_key   = os.getenv("HUGGINGFACE_API_TOKEN", "")

    titles = []
    if source == "cryptopanic" and cp_key:
        titles = _fetch_news_cryptopanic(cp_key, lookback, maxh)
    if not titles and na_key:
        titles = _fetch_news_newsapi(na_key, lookback, maxh)

    if not titles:
        print("⚠️ No news headlines found from CryptoPanic or NewsAPI.")
        return 0.0

    print("📰 Example headlines:", titles[:3])
    scores = _hf_sentiment_finbert(titles, hf_key)
    if not scores:
        print("⚠️ No sentiment scores returned.")
        return 0.0

    avg_score = float(np.mean(scores))
    print(f"📰 News processed: {len(titles)}, sentiment avg: {avg_score:.3f}")
    return avg_score

def decide_direction(df: pd.DataFrame, cfg: dict) -> tuple[str, dict]:
    proba_up, _ = price_model_signal(df, cfg.get("pred_horizon_bars", 3), cfg.get("min_price_model_proba", 0.55))
    s_news      = news_sentiment(cfg.get("news", {}))
    w_p         = float(cfg.get("blend_weight_price", 0.6))
    w_n         = float(cfg.get("blend_weight_news", 0.4))
    price_comp  = (proba_up - 0.5) * 2.0
    blended     = w_p * price_comp + w_n * s_news

    side = "skip"
    if blended >= 0.1 and proba_up >= cfg.get("min_price_model_proba", 0.55) and s_news >= cfg.get("min_news_sentiment", 0.10):
        side = "long"
    elif blended <= -0.1 and (1 - proba_up) >= cfg.get("min_price_model_proba", 0.55) and s_news <= cfg.get("max_news_sentiment_for_short", -0.10):
        side = "short"

    diag = {
        "proba_up": round(proba_up, 3),
        "news_sent": round(s_news, 3),
        "blended": round(blended, 3)
    }
    return side, diag
