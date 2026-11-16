"""SQLite persistence. On Railway, mount a Volume at DATA_DIR, otherwise the
daily lock is wiped on every redeploy and the kill-switch lock is useless."""
import json
import os
import sqlite3
import threading


class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "bot.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._l = threading.Lock()
        with self._l:
            self.db.executescript(
                """
                create table if not exists kv(k text primary key, v text);
                create table if not exists events(id integer primary key autoincrement, ts text, level text, msg text);
                create table if not exists trades(id integer primary key autoincrement, day text, symbol text,
                    side text, qty integer, entry real, exit real, pnl real, reason text, opened text, closed text);
                """
            )
            self.db.commit()

    def _exec(self, sql, args=()):
        with self._l:
            self.db.execute(sql, args)
            self.db.commit()

    def _q(self, sql, args=()):
        with self._l:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def get(self, k):
        r = self._q("select v from kv where k=?", (k,))
        return r[0]["v"] if r else None

    def set(self, k, v):
        self._exec("insert into kv(k,v) values(?,?) on conflict(k) do update set v=excluded.v", (k, v))

    # Daily kill-switch lock, keyed by IST date so it clears itself tomorrow.
    def lock_today(self, day: str, reason: str, ts: str):
        if not self.lock_info(day):
            self.set(f"lock:{day}", json.dumps({"reason": reason, "at": ts}))

    def lock_info(self, day: str):
        v = self.get(f"lock:{day}")
        return json.loads(v) if v else None

    # Kite access token (expires every morning).
    def set_token(self, day: str, token: str):
        self.set("token", json.dumps({"day": day, "token": token}))

    def get_token(self, day: str):
        v = self.get("token")
        if not v:
            return None
        d = json.loads(v)
        return d["token"] if d.get("day") == day else None

    def log(self, ts: str, level: str, msg: str):
        self._exec("insert into events(ts,level,msg) values(?,?,?)", (ts, level, msg))

    def recent_events(self, n=80):
        return self._q("select ts,level,msg from events order by id desc limit ?", (n,))

    def add_trade(self, day, symbol, side, qty, entry, exit_, pnl, reason, opened, closed):
        self._exec(
            "insert into trades(day,symbol,side,qty,entry,exit,pnl,reason,opened,closed) values(?,?,?,?,?,?,?,?,?,?)",
            (day, symbol, side, qty, entry, exit_, pnl, reason, opened, closed),
        )

    def trades_for(self, day):
        return self._q("select * from trades where day=? order by id desc", (day,))
