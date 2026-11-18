"""Single-user aquarium. The game server owns state transitions for one local tank."""
from collections import deque

import json
import logging
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

WEB = Path(__file__).parent / "web"
HISTORY_KEY = "mola:history"
BENCH_KEY = "mola:history:benchmark"


def now_ms():
    return time.time_ns() // 1_000_000


def sql_text(value):
    return "'" + value.replace("'", "''") + "'"


class Game:
    def __init__(self, engines, data_dir):
        self.engine = engines
        self.lock = threading.RLock()
        self.history_lock = threading.Lock()
        self.bench_lock = threading.Lock()
        self.file = Path(data_dir) / "round.json"
        self.round = json.loads(self.file.read_text()) if self.file.exists() else None
        self.history = self.benchmark = None
        self.query_lock = threading.Lock()
        self.queries = deque(maxlen=8)

    def query(self, sql):
        entry = {"sql": sql, "ok": False, "count": 1}
        try:
            result = self.engine.db(sql)
            entry.update(ok=True, inserted_id=result.get("inserted_id"))
            return result
        finally:
            with self.query_lock:
                previous = self.queries[0] if self.queries else None
                if previous and all(previous.get(key) == value for key, value in entry.items() if key != "count"):
                    entry["count"] += self.queries.popleft()["count"]
                self.queries.appendleft(entry)


    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.round, ensure_ascii=False))
        tmp.replace(self.file)

    def key(self):
        return f"mola:alive:{self.round['id']}"

    def set_live(self, ttl, stress, hungry):
        state = json.dumps({"stress": stress, "hungry": hungry})
        self.engine.redis("SET", self.key(), state, "PX", ttl)
        self.round.update(status="alive", deadline=now_ms() + ttl, stress=stress, hungry=hungry)
        self.save()

    def adopt(self, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 20 or not name.isprintable():
            raise ValueError("이름은 한 줄로 1~20자 입력해 주세요.")
        name = name.strip()
        with self.lock:
            self.tick()
            if self.round and self.round["status"] != "dead":
                raise ValueError("이미 수조에 살고 있는 복치가 있어요.")
            result = self.query(f"INSERT INTO adoptions VALUES ({sql_text(name)});")
            start = now_ms()
            self.round = dict(id=result["inserted_id"], name=name, status="starting",
                              started=start, deadline=start + 10000, stress=0, hungry=False,
                              survival_ms=0, remaining_ms=10000, cause=None)
            self.save()
            self.tick()

    def finish(self, cause, end):
        r = self.round
        r.update(status="finishing", cause=cause, survival_ms=max(0, end-r["started"]), remaining_ms=0)
        self.save()
        self.record_death()

    def record_death(self):
        r = self.round
        with self.history_lock:
            # Recover a committed INSERT if the process stopped before invalidation.
            found = self.query(f"SELECT id FROM deaths WHERE pet_id = {r['id']};")
            if not found["rows"]:
                self.query(f"INSERT INTO deaths VALUES ({r['id']}, {sql_text(r['name'])}, "
                               f"{r['survival_ms']}, {sql_text(r['cause'])});")
            self.engine.redis("DEL", self.key(), HISTORY_KEY)
            r["status"] = "dead"
            self.save()

    def tick(self):
        r = self.round
        if not r or r["status"] == "dead":
            return
        if r["status"] == "finishing":
            self.record_death()
            return
        if r["status"] == "starting":
            ttl = r["deadline"] - now_ms()
            if ttl > 0:
                self.set_live(ttl, 0, False)
            else:
                self.finish("굶주림", r["deadline"])
            return
        live = self.engine.redis("GET", self.key())
        if live is None:
            self.finish("굶주림", r["deadline"])
            return
        stored = json.loads(live)
        r.update(stress=stored["stress"], hungry=stored["hungry"])
        ttl = self.engine.redis("PTTL", self.key())
        if ttl == -1:
            raise RuntimeError("생존 키에 만료 시간이 없습니다.")
        if ttl <= 0:
            self.finish("굶주림", r["deadline"])
            return
        r.update(remaining_ms=ttl, survival_ms=max(0, now_ms()-r["started"]))
        if ttl <= 3000 and not r["hungry"]:
            stress = r["stress"] + 1
            if stress == 3:
                r["stress"] = stress
                self.finish("스트레스", r["deadline"] - 3000)
            else:
                self.set_live(ttl, stress, True)

    def feed(self, pet_id):
        with self.lock:
            self.tick()
            r = self.round
            if not r or r["status"] != "alive" or (pet_id is not None and r["id"] != pet_id):
                raise ValueError("지금 새우를 받을 복치가 없어요.")
            ttl = self.engine.redis("PTTL", self.key())
            if ttl <= 0:
                self.finish("굶주림", r["deadline"])
            elif ttl + 4000 > 15000:
                self.finish("과식", now_ms())
            else:
                self.set_live(ttl + 4000, r["stress"], False)

    def lookup(self, key=None):
        start = time.perf_counter()
        cached = self.engine.redis("GET", key) if key else None
        if cached is None:
            records = self.query("SELECT name, survival_ms, cause FROM deaths;")["rows"]
            rows = [dict(name=n, survival_ms=t, cause=c) for n,t,c in reversed(records)]
            encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            if key and len(encoded.encode()) < 240_000:
                self.engine.redis("SET", key, encoded)
        else:
            rows = json.loads(cached)
        return dict(rows=rows, hit=cached is not None, db_calls=int(cached is None),
                    ms=(time.perf_counter()-start)*1000)

    def load_history(self):
        with self.history_lock:
            self.history = self.lookup(HISTORY_KEY)

    def compare(self):
        with self.bench_lock, self.history_lock:
            self.engine.redis("DEL", BENCH_KEY)
            results = {}
            for mode, key in (("off", None), ("on", BENCH_KEY)):
                start = time.perf_counter()
                calls = sum(self.lookup(key)["db_calls"] for _ in range(100))
                results[mode] = dict(ms=(time.perf_counter()-start)*1000, db_calls=calls)
            self.engine.redis("DEL", BENCH_KEY)
            self.benchmark = results

    def query_snapshot(self):
        with self.query_lock:
            return list(self.queries)

    def snapshot(self):
        with self.lock:
            self.tick()
            r = self.round
            return dict(pet=dict(r) if r else None, insert={"id":r["id"], "name":r["name"]} if r else None,
                        history=self.history, benchmark=self.benchmark, queries=self.query_snapshot())


def serve(engines, game_port, data_dir):
    game = Game(engines, data_dir)
    stop = threading.Event()

    def watch():
        while not stop.wait(.15):
            try:
                with game.lock:
                    game.tick()
            except Exception:
                logging.exception("수조 상태를 갱신할 수 없습니다.")
                stop.wait(1)

    class Handler(BaseHTTPRequestHandler):
        def send_data(self, code, mime, body):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def api(self):
            try:
                path = urlsplit(self.path).path
                if self.command == "POST":
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("JSON 요청만 지원합니다.")
                    origin = self.headers.get("Origin")
                    if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                        raise ValueError("다른 출처의 요청입니다.")
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 <= length <= 2048:
                        raise ValueError("요청 크기를 확인해 주세요.")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("입력을 확인해 주세요.")
                    if path == "/api/adopt":
                        game.adopt(body.get("name"))
                    elif path == "/api/feed":
                        game.feed(body.get("id"))
                    elif path == "/api/history":
                        game.load_history()
                    elif path == "/api/benchmark":
                        game.compare()
                    else:
                        return self.send_error(404)
                elif path != "/api/state":
                    return self.send_error(404)
                result, code = game.snapshot(), 200
            except ValueError as e:
                result, code = {"error":str(e)}, 400
            except Exception:
                logging.exception("게임 요청 실패")
                result, code = {"error":"저장소 응답을 확인할 수 없어요. 잠시 후 다시 시도해 주세요."}, 503
            self.send_data(code, "application/json; charset=utf-8", json.dumps(result, ensure_ascii=False).encode())

        do_POST = api

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                return self.api()
            file = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if WEB.resolve() not in file.parents or not file.is_file():
                return self.send_error(404)
            self.send_data(200, mimetypes.guess_type(file.name)[0] or "application/octet-stream", file.read_bytes())

        def log_message(self, fmt, *args):
            if not (self.path == "/api/state" and args[1] == '200'):
                super().log_message(fmt, *args)

    with ThreadingHTTPServer(("127.0.0.1", game_port), Handler) as server:
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        print(f"개복치 생존 신고센터 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        finally:
            stop.set()
            watcher.join()
