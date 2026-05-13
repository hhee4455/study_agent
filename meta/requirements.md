# VIX 변동성 ETF 자동매매 시스템

LS증권 OpenAPI를 통해 미국 ETF **VIXY (1x VIX 단기선물 ETF)** 를 자동 매매하는 시스템을
이 워크스페이스(`meta/ws/`)에 새로 구축한다. **클린 아키텍처 + TDD** 기반이며,
사용자의 매매 전략은 다음과 같다.

- **저점 진입**: VIX ≤ 16 도달 → VIXY 매수
- **분할 매수 (공격형)**: VIX가 20 / 25 / 30 / 35 / 40 을 상승 돌파할 때마다 추가 매수
- **청산**: VIXY 평균단가 대비 +30% 도달 시 전량 시장가 매도

**1차 가동 모드는 dry-run** (실제 LS API 주문은 발행하지 않고 모든 의사결정을 로그로만
남김). 코드는 LS API 어댑터 인터페이스까지 완성하고, 실제 매매 활성화는 별도 플래그로
나중에 토글한다.

> **주의**: 이 문서는 *무엇*을 만들지 정의한다. *어떻게* 디렉토리/파일/패키지를 나눌지는
> 시스템(Decomposer + 구현 에이전트)이 결정한다. 본문에 디렉토리 트리/파일 경로는
> 명시하지 않는다.

---

## 결정된 사양 (변경 금지)

| 항목 | 값 |
|---|---|
| 매매 종목 | VIXY (NYSE Arca) — 해외주식 |
| LS증권 API | REST + WebSocket (OAuth2 AppKey/AppSecret) |
| 1차 가동 모드 | `dry-run` (주문 미발행, 의사결정만 로그) |
| VIX 데이터 소스 | Polygon.io Indices Starter, 티커 `I:VIX` |
| 폴링 주기 | 1분 |
| 폴링 시간대 | 미국 RTH 09:30–16:15 ET (DST 자동, 미국 공휴일 제외) |
| 신호 A — 저점 진입 | VIX ≤ 16 도달. 히스테리시스: 16 위 → 아래 자이클마다 1회 트리거 |
| 신호 B — 상승 돌파 | VIX가 20 / 25 / 30 / 35 / 40 을 상승 방향으로 돌파할 때마다 1회 트리거. 떨어졌다가 다시 돌파하면 재트리거 |
| 포지션 사이징 | AUM 대비 공격형: 16=15%, 20=15%, 25=20%, 30=25%, 35=30%, 40=35% (계 140%) |
| 청산 조건 | 보유 VIXY 평균단가 대비 +30% 도달 시 전량 시장가 매도 |
| 환율 처리 | LS API 외화자산/예수금 조회로 가용 USD 산출. USD 부족 시 주문 수량 비례 축소(부분주문). 자동 환전은 하지 않음 |
| AUM 정의 | LS API 외화증거금 + 보유 VIXY 평가액 합 (USD 기준) |
| 주문 방식 | 시장가 (빠른 체결 우선). 선택적 슬리피지 가드(설정값 초과 시 스킵 + 알림) |
| 영속화 | SQLite (signals, orders, positions, dry_run_orders, runs 테이블) |
| 로그 | 평문 회전 로그 + 구조화 JSONL 동시 출력 |
| 실행 환경 | macOS launchd 상주 |
| 언어/스택 | Python 3.11+, requests, pydantic v2, pyyaml, python-dotenv, sqlalchemy 2.x, freezegun (테스트), pytest, pytest-cov |

### CLI 서브커맨드 세트

| 명령 | 동작 |
|---|---|
| `vix-trader run` | 메인 루프 (시그널/주문/청산/모니터) 상주 |
| `vix-trader run --once` | 한 번만 폴링/평가 후 종료 (디버그) |
| `vix-trader status` | 현재 포지션, 평가액, 미체결 주문, 마지막 시그널 |
| `vix-trader positions` | 보유 포지션 상세 (평균단가, 수량, 평가손익) |
| `vix-trader signals --tail 50` | 최근 시그널 이력 |
| `vix-trader orders --tail 50` | 최근 주문 이력 (dry-run 포함) |
| `vix-trader liquidate --confirm` | 보유 VIXY 전량 시장가 매도 (수동 청산) |
| `vix-trader rebalance --confirm` | 현재 VIX 레벨에 맞는 누적 목표 포지션으로 정렬 |
| `vix-trader pause` / `vix-trader resume` | kill-switch 토글 (다음 폴링부터 적용) |

진입점 이름과 패키지 구조는 시스템이 결정하되, 위 서브커맨드와 인자 시그니처는 그대로
노출되어야 한다.

### 안전장치 (필수)

- `dry-run` 이 default. `live` 전환은 설정의 `mode: live` + 환경변수 `VIX_TRADER_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK` 동시 충족 필요.
- kill-switch (state 디렉토리 안의 `PAUSE` 파일) 존재 시 모든 신규 주문 발행 차단.
- 일일 최대 주문 건수 / 최대 USD 명목금액 cap (설정값).
- 슬리피지 가드: 시장가 주문 직전 호가 대비 N% 이상 벗어나면 주문 스킵 + `signal_skipped_slippage` 이벤트.
- 동일 신호의 재발 주문 방지: 단일 신호 ID 단위로 idempotency. orders 테이블에 `signal_id` UNIQUE.
- `run` 모드는 heartbeat 파일을 갱신하고, 30분 무응답이면 launchd가 재시작.

### 강제 리밸런스 정의

현재 VIX 레벨 V 가 주어졌을 때 **있어야 할** 누적 목표 포지션 (USD 기준):

```
누적 목표 = AUM × Σ allocation[L] for L in {16,20,25,30,35,40}
            where (L == 16 AND V <= 16) OR (L > 16 AND V >= L)
```

실제 보유 USD 명목금액과의 차이를 계산해 `delta > 0` 이면 추가 매수, `delta < 0` 이면
부분 매도 주문 발행. **주의**: VIX가 한번 40 돌파했다가 18로 내려와도 자동으로
청산하지는 않음. 청산은 +30% 익절 또는 수동 `liquidate` 만으로.

리밸런스가 의미를 갖는 시점: 시스템 가동을 늦게 시작했거나(이미 VIX가 25였을 때),
일시 정지 후 재개했거나(놓친 신호 보완), 사용자가 사이즈 비율을 변경한 직후.

---

## 아키텍처 원칙 (Clean Architecture)

도메인/응용/인프라/인터페이스 4계층을 분리한다. 디렉토리/패키지 명명은 시스템에 위임.

### 핵심 의존성 규칙

1. **도메인 계층**은 다른 계층을 import 하지 않는다 (stdlib + pydantic 만 허용).
2. **응용 계층**은 도메인 계층만 import 한다.
3. **인프라 계층**은 도메인 계층의 포트(인터페이스)를 구현한다. 응용 계층을 import 하지 않는다.
4. **인터페이스 계층**(CLI/runner)이 모든 어댑터를 wire-up 하는 유일한 layer.
5. 모든 외부 호출(LS, Polygon, DB, 시계, 파일)은 포트로 추상화. 직접 호출 금지.

### TDD 원칙

1. **유스케이스/정책마다 테스트 먼저**. RED → GREEN → REFACTOR.
2. 도메인/응용 테스트는 외부 의존 없이 0 ms 수준이어야 한다.
3. 어댑터 테스트는 recorded fixture (json) 또는 fake 사용. 실 네트워크 호출 금지.
4. `pytest` 1회 전체 실행 < 30초 목표.
5. 도메인 + 응용 계층 coverage 90%+.

---

## 구현 영역 (Decomposer가 작업으로 분해)

각 영역은 **책임 / 필요한 개념 / 규칙 / 검증** 으로 명시한다. 영역 간 우선순위는 아래
순서이며, 한 영역이 GREEN(테스트 통과) 되기 전까지 다음 영역으로 넘어가지 않는다.

### 영역 1 — 도메인 모델 (엔티티 / 값 객체 / 포트)

**책임**: 비즈니스 핵심 개념을 외부 의존 없이 표현.

**필요한 개념**:
- 엔티티: `Signal` (변형: `LowZoneEntry`, `Breakout(level)`), `Order`(BUY/SELL, market/limit, status), `Fill`, `Position`(VIXY 평균단가/수량), `Portfolio` (USD 잔고 + 포지션 합계).
- 값 객체: `Money`(amount + currency=USD/KRW, 산술 시 통화 일치 검증), `VixLevel`, `AllocationPct`, `Threshold`.
- 포트(인터페이스): `BrokerPort` (get_account, get_positions, get_quote, place_order, cancel_order), `MarketDataPort` (get_vix, get_vixy_quote), `ClockPort`, `LoggerPort`, `SignalRepoPort`, `OrderRepoPort`, `PositionRepoPort`, `RunRepoPort`.

**검증**: 도메인 모듈만 import 한 상태에서 엔티티/포트/값 객체 모두 import 가능. 값
객체 단위 테스트(통화 mismatch 거부, 음수 거부, 비율 0~200% 검증) 통과.

### 영역 2 — 시그널 정책 (signal_policy)

**책임**: VIX 시계열 입력으로부터 시그널 객체를 결정. 상태머신은 영속화 가능한 형태로
표현(예: 직렬화 가능한 모델).

**규칙**:
- 첫 시세는 상태 시드 (시그널 발신 없음).
- VIX ≤ 16 도달 시 `LowZoneEntry` 1회 발신, 16 위로 갔다 다시 ≤16 진입하면 재발신.
- 20/25/30/35/40 상승 돌파 시 `Breakout(level)` 1회 발신. 해당 레벨 아래로 떨어졌다 다시 돌파하면 재발신.
- 1주기에 다중 레벨 동시 돌파 가능 (예: 19 → 27 → `Breakout(20)` 와 `Breakout(25)` 동시 발생).

**검증**: 시나리오 `[18, 17, 15.5, 15, 14.5, 17, 21, 26, 30]` →
`[low_zone(15.5), breakout(20@21), breakout(25@26), breakout(30@30)]` 정확히 발생.
추가 케이스: 다중 사이클 재진입, 첫 시세 시드 무발신, 동일 레벨 중복 미발신, 다중 레벨
동시 돌파.

### 영역 3 — 사이즈 / 청산 / 리밸런스 정책

**책임**: 도메인 입력으로부터 주문 명세(USD 명목 / 수량) 결정.

**규칙**:
- **사이즈**: AUM(USD) × allocation% / VIXY 호가 = 주문 수량 (소수점 버림). 가용 USD 부족 시 비례 축소.
- **청산**: portfolio 평균단가 × 1.30 ≤ 현재가 → 전량 매도 의사결정 1건 반환.
- **리밸런스**: 현 VIX 레벨로부터 "있어야 할 누적 USD 명목"을 계산, 보유분과의 차이로 매수/매도 주문 리스트 반환.

**검증**: 각 정책별 7케이스 이상 (경계값, USD 부족, 0 보유, 이미 목표, AUM=0,
allocation 합 100% 초과, 다중 레벨 누적).

### 영역 4 — 영속화 어댑터 (SQLite + Repository 포트 구현)

**책임**: 도메인 포트 `*RepoPort` 의 SQLite 구현.

**규칙**:
- SQLAlchemy 2.x declarative.
- 테이블: `signals`, `orders`(`signal_id` UNIQUE), `positions`, `runs`(부팅 이력), `dry_run_orders`(분리 저장).
- 마이그레이션은 `Base.metadata.create_all()` 만 (Alembic 도입은 비-목표).
- 인메모리 SQLite 로 테스트 가능해야 함.

**검증**: 인메모리 SQLite 로 CRUD 라운드트립 + UNIQUE violation 캐치 + 동시성 케이스
1개 (같은 signal_id 두 트랜잭션 동시 insert 시도).

### 영역 5 — 시장 캘린더 (market_clock)

**책임**: `is_market_open(now)` 판정.

**규칙**:
- `zoneinfo.ZoneInfo("America/New_York")` 기반 RTH 09:30–16:15.
- 2026–2027 NYSE 휴장일 정적 리스트.
- 주말/공휴일 → False.
- naive datetime 은 UTC 로 간주.

**검증**: DST 전환 경계, 평일/주말, 공휴일, RTH 경계 ±1분.

### 영역 6 — Polygon 어댑터 (MarketDataPort 구현)

**책임**: VIX 지수 + (보조적으로) VIXY 호가 조회.

**규칙**:
- VIX: `GET /v3/snapshot/indices?ticker.any_of=I:VIX`.
- Bearer 인증, 5초 타임아웃, `requests` 에러 → 도메인 예외 `MarketDataError`.
- 주 호가는 LS API quote 우선 사용 (영역 7). Polygon은 fallback.

**검증**: recorded JSON fixture 4종(정상/빈 results/HTTP 5xx/타임아웃) → 정확한 도메인
객체 또는 예외.

### 영역 7 — LS증권 어댑터 (BrokerPort 구현, REST + WS)

**책임**: OAuth2 토큰 관리, 해외주식 주문/조회, 외화 잔고 조회, 체결 푸시 구독.

**규칙**:
- LS증권 OpenAPI 문서(`https://openapi.ls-sec.co.kr`) 가 정전(canonical).
- OAuth2 토큰 발급/갱신, 만료 5분 전 자동 재발급.
- 해외주식 주문, 외화 예수금/잔고/평가, WebSocket 체결 푸시 구독.
- VIXY 거래소 코드: NYSE.
- 실 AppKey/Secret 부재 시 `LSCredentialsMissing` 명시적 예외.
- 401 → 토큰 재발급 후 1회 재시도. 4xx → 도메인 예외로 변환.
- TR 코드/필드명은 LS 매뉴얼 섹션 번호와 함께 코드 주석에 표기.

**검증**: recorded JSON 응답으로 토큰 발급/주문/잔고/401 재시도/4xx 매핑 단위 테스트.
**실 네트워크 호출 금지**.

### 영역 8 — 응용 유스케이스 + 안전 게이트

**책임**: 도메인 + 포트를 조립한 유스케이스.

**필요한 유스케이스**:
- `evaluate_signal` (1주기: 시장 데이터 → 시그널 정책 → 신규 시그널 영속화)
- `execute_signal` (시그널 → 사이즈 정책 → broker 주문 또는 dry-run 분기)
- `monitor_exit` (포지션 → 청산 정책 → 매도 주문)
- `force_liquidate`, `force_rebalance`, `pause_resume`
- `status_report` (CLI status/positions/signals/orders 응답 빌더)

**안전 게이트 책임**: mode / kill-switch / idempotency / 일일 cap / 슬리피지 가드를 단일
모듈로 묶어 모든 주문 발행 직전에 통과시킨다.

**규칙**:
- dry-run 모드: 주문 의사결정 생성, broker 호출 0회, `dry_run_orders` 테이블에 기록.
- live 모드: `VIX_TRADER_LIVE_CONFIRM` 환경변수 미충족 시 부팅 거부.
- kill-switch 활성 시 신규 주문 차단 + 이벤트 기록.
- USD 부족 시 주문 수량 비례 축소.
- 동일 `signal_id` 재시도 시 중복 주문 미발행.

**검증**: fake `BrokerPort` / `MarketDataPort` 로 위 5가지 시나리오 단위 테스트 + e2e
시나리오 1개 (영역 2 시나리오 + 사이즈 + dry-run 기록 검증).

### 영역 9 — 인터페이스 (CLI + runner + launchd)

**책임**: CLI 디스패치, 메인 루프, macOS LaunchAgent 등록.

**규칙**:
- 위 "CLI 서브커맨드 세트" 표 그대로 노출 (이름/인자 시그니처 일치).
- `run` 은 1분 폴링 + SIGTERM/SIGINT graceful shutdown + heartbeat 갱신.
- launchd plist 템플릿 + 설치 스크립트 (venv 경로 자동 감지, `~/Library/LaunchAgents/` 설치).

**검증**:
- `status` 가 빈 DB 상태에서도 정상 출력 (오류 없이).
- `run --once` 가 (POLYGON_API_KEY 만 있는 상태에서) dry-run으로 1주기 처리 후 종료.
- e2e: fake broker + fake market data로 `[18,17,15.5,15,14.5,17,21,26,30]` 시나리오 처리,
  `signals`/`dry_run_orders` 테이블에 예상 행 수 확인.

### 영역 10 — README 운영 가이드

**책임**: 사람이 운영할 수 있도록 절차 문서화.

**필수 섹션**:
- 셋업 (venv, 패키지 설치, `.env`).
- Polygon Indices Starter / LS OpenAPI 신청 절차 링크.
- dry-run → live 전환 절차 (mode + `VIX_TRADER_LIVE_CONFIRM` + 모의계좌 우선).
- launchd 설치/해제, 로그 분석 jq 예시.
- 안전장치 동작 설명 (kill-switch, 일일 cap, 슬리피지).
- 트러블슈팅: 토큰 만료, 시계 점프, USD 부족, Polygon 한도.

**검증**: `grep -E "^##" README.md` 출력에 위 6개 섹션 모두 존재.

---

## 출력 규칙

- 모든 변경 후 `pytest` 가 100% 통과해야 작업을 DONE으로 마크.
- 의문점은 마지막 줄에 `[QUESTION]\n...\n[/QUESTION]\n` 정확한 형식으로 출력.
- 의문 없으면 마지막 줄 `[NO_QUESTIONS]`.
- 외부 네트워크 호출 금지 (테스트 포함). LS/Polygon 응답은 fixture 사용.
- 새 의존성은 위 "결정된 사양" 표에 명시된 것만. 추가 필요 시 [QUESTION] 으로 협의.
- 커밋/PR은 자율 시스템이 만들지 않는다 (사람이 검토 후 결정).
- 디렉토리/패키지 명명은 자율 시스템이 결정. 본 문서에 디렉토리 트리는 명시하지 않는다.

---

## 비-목표

- 백테스트 / 시뮬레이션 / 차트 (별도 단계).
- Telegram/Slack/이메일 알림 (1차는 로그만).
- 자동 환전 (USD 부족 시 주문 축소만).
- 다중 자산 / VKOSPI / 옵션.
- 실거래 모드(`live`) 자동 활성화 — 사람이 명시적 토글.
- VIX 하락 시 분할매수 단계 자동 청산 (오직 +30% 익절 또는 수동 liquidate).
- Alembic 마이그레이션 (현재는 `create_all()` 충분).
- Docker / 컨테이너화 (1차는 venv + launchd).
- LS XingAPI(COM) 지원 — REST/WS만.

---

## 주의사항

1. **VIXY는 장기 보유 시 콘탱고 손실 누적**. 본 시스템은 단기 변동성 스파이크 트레이딩
   목적이며, 평균단가 +30% 익절 후 즉시 청산이 핵심.
2. **첫 가동은 반드시 dry-run**. 최소 1주일 로그를 검토하여 신호 빈도/주문 사이즈/
   슬리피지 가드가 의도대로 동작하는지 확인 후 live 전환.
3. **LS API 모의투자 → 실계좌 순서**. live 전환 시에도 모의투자 endpoint 우선.
4. **Polygon Indices Starter ($29/월)** 는 15분 지연 데이터일 수 있음. 본 전략의
   임계값(정수 단위 5포인트 간격)은 15분 지연으로도 충분하나, 실시간이 필요하면
   Indices Advanced 로 업그레이드.
