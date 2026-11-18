"""Build and launch the two native engines with game-owned schemas and data."""
import fcntl
import os
import select
import signal
import subprocess
import sys
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

from engines import Engines

ROOT = Path(__file__).resolve().parent
SCHEMAS = {
    "adoptions": "id,INT\nname,TEXT\n",
    "deaths": "id,INT\npet_id,INT\nname,TEXT\nsurvival_ms,INT\ncause,TEXT\n",
}


def source_path(variable, relative):
    if variable in os.environ:
        return Path(os.environ[variable]).expanduser().resolve()
    for parent in (ROOT.parent, ROOT.parent / "mini-sql", ROOT.parent.parent):
        source = parent / relative
        if (source / "Makefile").exists():
            return source
    raise RuntimeError(f"{variable} 환경 변수에 엔진 저장소 경로를 지정하세요.")


def build_engines(build):
    db_src = source_path("MOLA_DB_SRC", "jungle-mini-dbms")
    redis_src = source_path("MOLA_REDIS_SRC", "mini-redis/mini-redis-impl/jungle-mini-redis")
    db_build = build / "db"
    redis_build = build / "redis"
    flags = []
    if sys.platform == "darwin":
        # GNU-specific rwlock scheduling preference. Darwin uses its native rwlock policy.
        flags = ["CFLAGS=-std=c11 -D_GNU_SOURCE -Isrc -O2 -pthread "
                 "'-Dpthread_rwlockattr_setkind_np(a,b)=((void)0)'"]
    subprocess.run(["make", "-C", str(db_src), f"BUILD={db_build}", *flags, "server"], check=True)
    subprocess.run(["make", "-C", str(redis_src), f"BUILD_DIR={redis_build}", "all"], check=True)
    return db_build / "dbms-server", redis_build / "mini-redis"


def discard_logs(stream):
    for _ in stream:
        pass


@contextmanager
def child(args, cwd, listening):
    # Read the child's own readiness line so a different service on this port is never adopted.
    process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, start_new_session=True)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 10)
        if not ready or not process.stdout.readline().startswith(listening):
            raise RuntimeError(f"{Path(args[0]).name} 실행 실패. 포트와 데이터 경로를 확인하세요.")
        drain = threading.Thread(target=discard_logs, args=(process.stdout,), daemon=True)
        drain.start()
        yield
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        process.stdout.close()


def main():
    from game_server import serve
    data = Path(os.environ.get("MOLA_DATA", ROOT / ".local")).expanduser().resolve()
    game_port = int(os.environ.get("MOLA_PORT", "8787"))
    db_port = int(os.environ.get("MOLA_DB_PORT", "18780"))
    redis_port = int(os.environ.get("MOLA_REDIS_PORT", "16379"))
    ports = (game_port, db_port, redis_port)
    if len(set(ports)) != 3 or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("게임, DBMS, Redis에 서로 다른 포트(1~65535)를 지정하세요.")
    data.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock = stack.enter_context((data / "game.lock").open("a"))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("같은 경로의 게임이 이미 실행 중입니다.") from error
        for name in ("db", "redis"):
            (data / name).mkdir(exist_ok=True)
        for name, schema in SCHEMAS.items():
            path = data / "db" / f"{name}.schema"
            if not path.exists():
                path.write_text(schema, encoding="utf-8")
            elif path.read_text(encoding="utf-8") != schema:
                raise RuntimeError(f"게임 스키마가 일치하지 않습니다: {path}")
        db_binary, redis_binary = build_engines(data / "build")
        stack.enter_context(child([db_binary, str(db_port), "db"], data, b"listening on port"))
        stack.enter_context(child([redis_binary, "127.0.0.1", str(redis_port), "redis"], data, b"Listening on"))
        print(f"개복치 생존 신고센터  http://127.0.0.1:{game_port}", flush=True)
        serve(Engines(db_port, redis_port), game_port, data)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
