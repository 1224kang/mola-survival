"""Clients for the team's HTTP query endpoint and RESP2 server."""
import http.client
import json
import socket


class EngineError(RuntimeError):
    """A backend was unreachable or rejected a command."""


class Engines:
    def __init__(self, db_port, redis_port):
        self.db_port = db_port
        self.redis_port = redis_port

    def db(self, sql):
        body = json.dumps({"sql": sql}, ensure_ascii=False).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.db_port, timeout=5)
        try:
            connection.request("POST", "/query", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            if response.status != 200 or not result.get("ok"):
                raise EngineError(result.get("error", f"DBMS HTTP {response.status}"))
            return result
        except (ValueError, OSError, http.client.HTTPException) as error:
            raise EngineError(f"미니 DBMS 연결 오류: {error}") from error
        finally:
            connection.close()

    def redis(self, *args):
        values = [str(arg).encode("utf-8") for arg in args]
        parts = [f"*{len(values)}\r\n".encode()]
        for value in values:
            parts.extend((f"${len(value)}\r\n".encode(), value, b"\r\n"))
        try:
            with socket.create_connection(("127.0.0.1", self.redis_port), timeout=5) as sock:
                sock.sendall(b"".join(parts))
                with sock.makefile("rb") as stream:
                    header = stream.readline(8192)
                    if not header.endswith(b"\r\n"):
                        raise EngineError("미니 Redis 응답이 끊어졌습니다.")
                    kind, value = header[:1], header[1:-2]
                    if kind == b"+":
                        return value.decode()
                    if kind == b"-":
                        raise EngineError(value.decode())
                    if kind == b":":
                        return int(value)
                    if kind == b"$":
                        length = int(value)
                        if length == -1:
                            return None
                        if length < 0:
                            raise EngineError("잘못된 미니 Redis 응답 길이입니다.")
                        payload = stream.read(length)
                        if len(payload) != length or stream.read(2) != b"\r\n":
                            raise EngineError("미니 Redis 응답이 끊어졌습니다.")
                        return payload.decode()
                    raise EngineError("지원하지 않는 미니 Redis 응답입니다.")
        except (OSError, ValueError) as error:
            raise EngineError(f"미니 Redis 연결 오류: {error}") from error
