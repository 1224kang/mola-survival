# 개복치 생존 신고센터

직접 만든 미니 Redis와 미니 DBMS로 개복치를 키우는 1인 웹 게임입니다. 이름을 지어 입양하면 생존 시간이 10초부터 시작됩니다. 새우 한 마리는 4초를 더하지만, 15초를 초과하면 과식으로 죽습니다. 3초 이하로 내려갈 때마다 스트레스가 한 번씩 쌓이고, 세 번째 배고픔은 스트레스 사망, 생존 기한 만료는 굶주림 사망으로 기록됩니다.

## 브라우저에서 두 저장소까지

브라우저는 Python 게임 서버에 HTTP로 요청합니다. 게임 서버는 미니 DBMS의 HTTP API에 SQL을 보내고, 미니 Redis에는 TCP 연결로 RESP 명령을 보냅니다. 사이드바의 관측 일지에서 입양 INSERT 결과, 남은 TTL, 스트레스, 캐시 적중 여부와 DBMS 호출 수를 확인할 수 있습니다.

| 게임 동작 | 미니 DBMS | 미니 Redis |
| --- | --- | --- |
| 입양 | 이름을 INSERT하고 발급 ID를 반환 | 입양 ID에 대응하는 생존 키를 TTL 10초로 생성 |
| 수조 관찰과 급식 | | GET으로 상태를 확인하고 PTTL로 남은 시간을 읽음. 새우를 주면 남은 시간에 4초를 더해 갱신 |
| 부고 | 입양 ID와 함께 이름·생존 시간·사인을 저장 | 생존 키를 정리하고 복치 기록 캐시를 무효화 |
| 기록 조회 | 캐시 미스일 때 사망 기록을 SELECT | 조회 결과를 캐싱해 다음 요청에서 재사용 |

## TTL은 화면에서 어떻게 보이나요?

브라우저는 약 0.3초 간격으로 게임 서버에 상태를 요청합니다. 서버가 Redis에서 읽은 PTTL 값이 2,340ms라면 사이드바에는 **2.3초**가 표시됩니다. 새우를 누를 때도 서버가 남은 시간을 다시 읽습니다.

생존 기한과 메모리 회수 시점은 다를 수 있습니다. 만료 시각이 지나면 조회 결과는 GET에서 nil, PTTL에서 -2입니다. 서버는 굶주림의 생존 시간을 만료 시각 기준으로 기록합니다. 과식이나 스트레스로 생존 키를 삭제해도 GET 결과는 같으므로, 실제 사인은 미니 DBMS에 남겨 둡니다.

## 복치 기록에서 MISS와 HIT는 어떻게 다른가요?

1. **내가 키운 복치들**을 열면 게임 서버가 Redis에 `GET mola:history`를 보냅니다.
2. **MISS**이면 DBMS에서 이름·생존 시간·사인을 조회합니다. 목록을 JSON으로 Redis에 저장한 다음 브라우저에 응답합니다. 이 요청의 DBMS 호출 수는 1회입니다.
3. **다시 조회**를 누르면 캐싱된 목록으로 응답합니다. **HIT**이고 DBMS 호출 수는 0회입니다.
4. 새 부고를 DBMS에 저장한 뒤 서버가 `DEL mola:history`를 실행합니다. 다음 조회는 새 복치를 포함한 목록을 다시 채웁니다.

**복치 기록 100번 열기**는 같은 목록을 캐시 끔과 켬 조건에서 각각 100번 순차 조회합니다. 캐시 켬은 빈 비교용 키에서 시작하므로 일반적인 목록에서는 첫 요청만 DBMS에 접근합니다. 화면에는 실측 총 처리 시간과 호출 수가 표시됩니다. 첫 MISS에서 Redis에 저장하는 시간도 포함됩니다. 목록이 240,000바이트 이상이면 서버가 캐시 저장을 생략합니다.

## 로컬 실행

macOS 또는 Linux에서 Python 3, make, C 컴파일러를 사용합니다. 세 저장소를 다음 위치에 두면 게임 실행기가 두 엔진을 자동으로 찾습니다.

```text
week-12/
├── mola-survival/
├── mini-sql/jungle-mini-dbms/
└── mini-redis/mini-redis-impl/jungle-mini-redis/
```

처음 준비할 때는 `week-12`에서 아래 명령으로 두 엔진의 검증된 버전을 받을 수 있습니다.

```sh
git clone -b develop https://github.com/stereokkh/jungle-mini-dbms.git mini-sql/jungle-mini-dbms
git -C mini-sql/jungle-mini-dbms checkout 6ee5e6f3d58f4a1951e5b9a41790ac70d57d5320
git clone https://github.com/Choidongho08/jungle-mini-redis.git mini-redis/mini-redis-impl/jungle-mini-redis
git -C mini-redis/mini-redis-impl/jungle-mini-redis checkout 467229894f2efd10dbcd4a6de74a87a7f2a02ed0
```

게임 폴더에서 실행합니다.

```sh
python3 run.py
```

[수조 열기](http://127.0.0.1:8787). 기본 포트는 게임 8787, DBMS 18780, Redis 16379입니다. 두 엔진을 다른 곳에 복제했다면 각 Makefile이 있는 폴더를 지정해 실행할 수 있습니다.

```sh
MOLA_DB_SRC=/path/to/jungle-mini-dbms MOLA_REDIS_SRC=/path/to/jungle-mini-redis python3 run.py
```

게임 데이터와 빌드 결과는 로컬의 `.local/`에 보관합니다. Ctrl+C로 종료한 뒤 같은 데이터 경로에서 다시 실행하면 기록을 이어서 볼 수 있습니다. 게임은 한 수조를 공유하며 미니 DBMS 서버는 모든 네트워크 인터페이스에 바인딩됩니다.

엔진 소스는 [미니 DBMS](https://github.com/stereokkh/jungle-mini-dbms)와 [미니 Redis](https://github.com/Choidongho08/jungle-mini-redis)에서 확인할 수 있습니다. 게임의 실제 흐름은 [서버 로직](game_server.py), [저장소 연결](engines.py), [질문과 답변](질문과-답변.md)에 정리했습니다.
