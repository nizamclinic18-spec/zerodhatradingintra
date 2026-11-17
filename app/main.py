import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from kiteconnect import KiteConnect

from . import backtest as bt
from .broker import LiveBroker, PaperBroker
from .clock import now_ist
from .config import load
from .engine import Engine
from .registry import STRATEGIES, make_strategy
from .store import Store
from .ticker import TickerService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("main")
cfg = load()
store = Store(cfg.data_dir)
STATIC = Path(__file__).resolve().parent.parent / "static"


class Runtime:
    engine = None
    ticker = None
    error = None
    kite = None
    tokens = {}
    all_tokens = {}
    bt = {"state": "idle", "msg": ""}

    def connect(self, access_token: str):
        kite = KiteConnect(api_key=cfg.api_key)
        kite.set_access_token(access_token)
        kite.profile()  # raises if the token is bad
        wanted = set(cfg.watchlist)
        eq = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")
              if i["instrument_type"] == "EQ" and i["segment"] == "NSE"}
        toks = {s: eq[s] for s in wanted if s in eq}
        missing = wanted - set(toks)
        broker = LiveBroker(kite) if cfg.mode == "live" else PaperBroker()
        self.kite, self.tokens, self.all_tokens = kite, toks, eq
        self.shutdown()
        self.engine = Engine(cfg, store, broker)
        if missing:
            self.engine.event("WARN", f"Symbols not found on NSE: {sorted(missing)}")
        self.engine.start()
        gone = [s for s in self.engine.tracked if s not in eq]
        for s in gone:
            self.engine.track_remove(s)
            self.engine.event("WARN", f"Tracker symbol {s} not found on NSE; removed")
        track_toks = {s: eq[s] for s in self.engine.tracked}
        self.ticker = TickerService(cfg.api_key, access_token, {**toks, **track_toks}, self.engine)
        self.error = None
        # Warm-up (history replay, then live ticks) runs in the background so the login redirect is instant.
        threading.Thread(target=self._warmup, args=(kite, toks, track_toks, self.engine, self.ticker), daemon=True).start()

    def _history(self, kite, tok, now):
        start = now.replace(hour=cfg.or_start.hour, minute=cfg.or_start.minute, second=0, microsecond=0, tzinfo=None)
        rows = kite.historical_data(tok, start, now.replace(tzinfo=None), f"{cfg.candle_minutes}minute")
        time.sleep(0.4)  # Kite allows about 3 history requests per second
        return rows

    def _warmup(self, kite, toks, track_toks, engine, ticker):
        """After a restart or late login, rebuild today's opening ranges from Zerodha's price history."""
        now = now_ist()
        if now.weekday() < 5 and now.time() >= cfg.or_start:
            for sym, tok in toks.items():
                try:
                    engine.replay(sym, self._history(kite, tok, now))
                except Exception as e:
                    engine.event("WARN", f"Could not rebuild today's candles for {sym}: {e}")
            for sym, tok in track_toks.items():
                try:
                    engine.replay_tracked(sym, self._history(kite, tok, now))
                except Exception as e:
                    engine.event("WARN", f"Could not rebuild tracker candles for {sym}: {e}")
        if engine is self.engine:
            ticker.start()

    def track_add(self, symbols):
        eng = self.engine
        if not eng:
            raise HTTPException(409, "Log in to Zerodha first")
        added, rejected = [], []
        for sym in symbols:
            tok = self.all_tokens.get(sym)
            if not tok:
                rejected.append(f"{sym} (not an NSE equity symbol)")
            elif not eng.track_add(sym):
                rejected.append(f"{sym} (already tracked, or the 30-stock limit is reached)")
            else:
                self.ticker.add(sym, tok)
                try:
                    now = now_ist()
                    if now.weekday() < 5 and now.time() >= cfg.or_start:
                        eng.replay_tracked(sym, self._history(self.kite, tok, now))
                except Exception as e:
                    eng.event("WARN", f"Could not load today's candles for {sym}: {e}")
                added.append(sym)
        return {"added": added, "rejected": rejected}

    def track_remove(self, sym):
        if self.engine:
            self.engine.track_remove(sym)
            if sym not in self.engine.watch and self.ticker:
                self.ticker.remove(sym)

    def restore(self):
        tok = store.get_token(now_ist().date().isoformat())
        if tok and cfg.api_key:
            try:
                self.connect(tok)
            except Exception as e:
                self.error = f"Saved login is no longer valid: {e}"

    def shutdown(self):
        if self.engine:
            self.engine.stop()
        if self.ticker:
            self.ticker.stop()


rt = Runtime()


@asynccontextmanager
async def lifespan(app):
    if not cfg.admin_password:
        log.error("DASHBOARD_PASSWORD is not set; every API call will be refused.")
    rt.restore()
    yield
    rt.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
_fails = {}


def auth(request: Request):
    ip = request.client.host if request.client else "?"
    n, until = _fails.get(ip, (0, 0))
    if until > time.time():
        raise HTTPException(429, "Too many wrong passwords. Wait a few minutes.")
    given = request.headers.get("x-admin-token", "")
    if not cfg.admin_password or not hmac.compare_digest(given, cfg.admin_password):
        n += 1
        _fails[ip] = (n, time.time() + 300 if n >= 8 else 0)
        raise HTTPException(401, "Wrong password")
    _fails.pop(ip, None)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state(request: Request):
    auth(request)
    base = {"connected": rt.engine is not None, "error": rt.error, "mode": cfg.mode,
            "keys_set": bool(cfg.api_key and cfg.api_secret)}
    if rt.engine:
        base.update(rt.engine.snapshot())
    else:
        base.update({"events": store.recent_events(30), "loss_limit": cfg.loss_limit, "day_pnl": 0.0,
                     "capital": cfg.capital, "positions": [], "open_trades": [], "closed_trades": [],
                     "watch": [], "armed": False, "killed": False, "lock": None, "now": now_ist().strftime("%H:%M:%S"),
                     "rules": {}})
    return base


@app.get("/api/login-url")
def login_url(request: Request):
    auth(request)
    if not cfg.api_key:
        raise HTTPException(400, "KITE_API_KEY is not set")
    return {"url": KiteConnect(api_key=cfg.api_key).login_url()}


@app.get("/kite/callback")
def kite_callback(request_token: str = "", status: str = ""):
    """Set this URL as the Redirect URL in the Kite developer console."""
    if status != "success" or not request_token:
        return RedirectResponse("/?login=failed")
    try:
        sess = KiteConnect(api_key=cfg.api_key).generate_session(request_token, api_secret=cfg.api_secret)
        store.set_token(now_ist().date().isoformat(), sess["access_token"])
        rt.connect(sess["access_token"])
    except Exception as e:
        log.error("login failed: %s", e)
        rt.error = f"Login failed: {e}"
        return RedirectResponse("/?login=failed")
    return RedirectResponse("/?login=ok")


def need_engine():
    if not rt.engine:
        raise HTTPException(409, "Log in to Zerodha first")
    return rt.engine


@app.post("/api/arm")
def arm(request: Request):
    auth(request)
    ok, msg = need_engine().arm()
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True}


@app.post("/api/disarm")
def disarm(request: Request):
    auth(request)
    need_engine().disarm()
    return {"ok": True}


@app.post("/api/kill")
def kill(request: Request):
    auth(request)
    need_engine().kill_switch("Manual kill-switch from dashboard")
    return {"ok": True}


# ---------- backtesting ----------
@app.post("/api/backtest/download")
def bt_download(request: Request, days: int = 90):
    auth(request)
    if not rt.kite:
        raise HTTPException(409, "Log in to Zerodha first")
    if rt.bt["state"] == "running":
        raise HTTPException(409, "A download is already running")
    days = max(10, min(days, 365))

    def work():
        rt.bt.update(state="running", msg="Starting")
        try:
            bt.download(cfg, rt.kite, rt.tokens, days, rt.bt)
            rt.bt.update(state="done", msg=f"Downloaded {days} days for {len(rt.tokens)} symbols")
        except Exception as e:
            rt.bt.update(state="error", msg=f"Download failed: {e}")

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.get("/api/backtest/status")
def bt_status(request: Request):
    auth(request)
    return {**rt.bt, "strategies": sorted(STRATEGIES)}


@app.get("/api/backtest/run")
def bt_run(request: Request, strategy: str = "orb"):
    auth(request)
    if strategy not in STRATEGIES:
        raise HTTPException(400, "Unknown strategy")
    res = bt.backtest(cfg, cfg.watchlist, lambda c: make_strategy(c, strategy))
    if not res:
        raise HTTPException(409, "No price history yet. Click 'Download price history' first.")
    res["strategy"] = strategy
    return res


# ---------- stock tracker (alerts only) ----------
@app.post("/api/tracker/add")
def tracker_add(request: Request, symbols: str = ""):
    auth(request)
    syms = [x.strip().upper() for x in symbols.replace("\n", ",").split(",") if x.strip()]
    if not syms:
        raise HTTPException(400, "Enter at least one NSE symbol")
    return rt.track_add(syms[:30])


@app.post("/api/tracker/remove")
def tracker_remove(request: Request, symbol: str = ""):
    auth(request)
    rt.track_remove(symbol.strip().upper())
    return {"ok": True}
