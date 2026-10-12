# KMW 메리츠 LOC 주문 테스트

`kmw_autotrade.py`는 KMW 프리셋만 사용해 SOXL 주문안을 계산한다. 기본 실행은 미리보기이며 `--submit`을 지정하면 실제 계좌에 일반 LOC/MOC 주문을 직접 전송한다. 기본 `--order-route direct`는 `/overseas/orders/buy`·`sell`에 LOC `oder_cls_code=32`, 보유기간 만료 MOC 매도 `34`를 사용한다. 직접 접수한 LOC도 종가 가격 조건이 맞아야 체결된다. 접수와 실제 체결은 별도 확인해야 한다.

예약주문이 필요한 경우에만 `--order-route reserved`를 명시한다. 예약 LOC는 `rsev_aspr_tpcd=5`, MOC는 `7`이며 일반예약(`1`)·장개시 발주(`0`)를 사용한다. 직접 주문이 거절되더라도 예약으로 자동 전환하지 않는다.

## 설치와 계좌 설정

저장소 폴더에서 Python 3.9 이상으로 실행한다.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-autotrade.txt
cp .env.meritz.example .env.meritz
chmod 600 .env.meritz
```

`.env.meritz`에 KMW에 사용할 메리츠 계좌의 App Key, App Secret, 호출 PC의 MAC 주소(구분자 없는 12자리)를 넣는다. 키는 발급받은 계좌에 묶이며 스크립트가 계좌번호를 선택하지 않는다. `MERITZ_ACCOUNT_PRESET=KMW`는 이 연결에 대한 명시적 표시다. 키는 채팅이나 GitHub에 올리지 않는다. `.env.meritz`와 `.autotrade/`는 Git에서 제외된다.

SOXL 보유잔고가 없으면 `MERITZ_SOXL_EXCHANGE_CODE`에 해당 계좌에서 확인한 거래소 벤더 코드를 입력한다. 보유잔고가 있으면 그 응답의 코드를 사용한다. 공식 명세는 NYSE `0321`, NASDAQ `0537`, AMEX `0066`을 제공하지만 상장 거래소 이름만으로 SOXL 코드를 추정하지 않는다. 시세 응답의 호가 단위가 `0`이면 `MERITZ_SOXL_PRICE_TICK`에 확인한 SOXL 호가 단위를 입력해야 한다. 예를 들어 확인된 단위가 1센트일 때 `0.01`을 사용한다. 지연시세는 종목/주문단위 확인에만 사용하며 주문가격은 전략에서 계산한다.

해외주식 및 해당 해외 ETP 거래신청이 되어 있는 계좌를 사용한다. 공개 공식 예제는 모의계좌가 없다고 안내하므로 `--submit`은 실제 주문이다.

## 오늘 테스트

먼저 웹앱에서 KMW의 보유수량과 최근 체결·현금을 확인하고 저장한다. 정상 주문안은 직전 미국 거래일의 `as_of_date`, SOXL·QQQ 확정 일봉 및 추천 기준일을 요구한다. 오래된 상태를 종가 시뮬레이션으로 실제 체결처럼 변경하지 않는다.

```sh
# 최신 GitHub KMW로 주문안 확인. 브로커 키 없이 실행 가능.
python kmw_autotrade.py

# 저장된 스냅샷이 오래됐을 때 계산 경로만 확인. 실주문과 병용 불가.
python kmw_autotrade.py --allow-stale-preview

# 실제 계좌 조회와 사전검증만 수행.
python kmw_autotrade.py --env-file .env.meritz --check-account --side BUY --quantity-cap 1

# 매수 1주로 실제 LOC 직접 접수 테스트. 실행 전 같은 옵션으로 미리본다.
python kmw_autotrade.py --side BUY --quantity-cap 1
python kmw_autotrade.py --env-file .env.meritz --submit --side BUY --quantity-cap 1

# 예약 경로가 필요할 때만 명시적으로 지정한다.
python kmw_autotrade.py --env-file .env.meritz --submit --side BUY --quantity-cap 1 --order-route reserved

# 접수 및 실제 체결 확인. 매수뿐 아니라 모든 매도 주문도 확인한다.
python kmw_autotrade.py --env-file .env.meritz --status
```

`--quantity-cap 1`은 **모든 주문 행을 최대 1주로 제한**한다. 매수 1주와 여러 회차 매도 각각 1주가 나갈 수 있다. 미리보기에도 같은 옵션을 지정하면 실제 전송 수량을 확인할 수 있다. 전체 추천 수량으로 전송하려면 `--quantity-cap`을 생략한다.

`--side BUY`는 매수만, `--side SELL`은 각 회차 매도만 선택한다. 두 옵션 모두 생략하면 전체 매수·매도 추천이 대상이다. 자동 기본값에서는 한쪽을 임의로 제외하지 않는다. 2026-10-12 미리보기에서는 매수 LOC 144.79달러와 5회차 매도 LOC 144.65달러가 교차하므로 전체 주문 전송은 차단된다. 한쪽만 테스트하려면 `--side`를 명시한다. 이미 접수된 반대쪽 주문이 있으면 한쪽 테스트도 중단하며 자동 취소하지 않는다.

현재 프로그램은 일반/예약 주문 접수 결과와 주문·체결 조회를 제공한다. 체결조회 결과를 웹앱 GitHub 스냅샷에 자동 기록하는 기능은 포함하지 않는다. 실제 체결에 따라 웹앱의 KMW 보유수량·현금을 반영한 후 다음 주문안을 생성한다. 수량 변경 없이 종가만 보고 매도됐다고 판단하지 않는다. 미리보기는 저장 상태와 실제 상태의 차이를 알려줄 수 있으며 전송 전에는 보유수량이 브로커 SOXL 잔고와 정확히 일치해야 한다.

## 매일 17:01 실행

```sh
# 미리보기 스케줄 검증
python kmw_autotrade.py --schedule

# 지정 PC/서버에서 실제 자동 LOC/MOC 직접 주문
python kmw_autotrade.py --env-file .env.meritz --submit --schedule
```

프로세스를 켜둔 PC/서버가 한국시간 매일 17:01에 실행하며 미국 휴장일·주말은 건너뛴다. PC가 자거나 프로세스를 종료하면 실행되지 않는다. 이미 17:01이 지난 뒤 시작하면 다음날 17:01부터 동작하므로 오늘 즉시 테스트에는 `--schedule`을 생략한다.

현재 추천 계산은 미국 정규장 개장 전만 지원하며 직접 주문도 같은 미국 거래일의 09:30 ET 이전으로 제한한다. 메리츠의 직접 LOC 접수 가능시간은 서버가 최종 판단한다. 17:01 KST는 미국 서머타임 때 04:01 ET, 겨울에는 03:01 ET이므로 해당 시간의 실제 접수 가능 여부를 계좌로 확인해야 한다. 거절된 주문을 자동 재시도하거나 예약주문으로 전환하지 않는다. 예약 경로를 명시하면 기존 한국시간 09:00부터 미국 정규장 개장 1시간 전까지의 예약 시간 제한을 사용한다.

## 중단 조건과 기록

- 오래된 스냅샷·시장데이터, 계좌 보유수량 불일치, 부족한 USD 주문가능금액은 전송을 막는다. 매도대금의 당일 재사용을 미리 가정하지 않고 기본 0.5% 수수료 여유를 확보한다.
- 기존 SOXL 미체결/활성 예약이 있으면 전송하지 않는다. 매수 LOC가 매도 LOC와 교차하거나 MOC 매도와 동시에 있으면 배치 전체를 중단한다. 이 경우 보유기간 만료 매도도 전송되지 않으므로 해당 주문을 따로 처리해야 한다. 스크립트는 기존 주문을 자동 취소하거나 주문을 상계하지 않는다.
- `.autotrade/orders.sqlite3`에서 계좌 키의 해시·주문일·회차·포지션별 전송 시도를 기록한다. 가격·수량을 바꾸거나 프로그램을 재시작해도 같은 주문을 다시 보내지 않는다.
- 통신 중단·확인 불가 응답은 `UNKNOWN`으로 남기고 다음 전송도 중단한다. 경고는 자동 확인/재전송하지 않는다. `--status`와 메리츠 주문 화면으로 확인한다. 테스트 중 이 DB를 삭제해 재전송하지 않는다.
- 한 주문이 거절되면 배치의 나머지 주문을 중단한다. 앞서 접수된 주문은 유지되므로 상태를 함께 확인한다. `DIRECT_ACCEPTED`는 일반 주문 접수, `RESERVED`는 예약접수이며 둘 다 체결을 뜻하지 않는다. 주문 경로를 변경해도 같은 계좌·주문일·포지션의 재전송은 차단한다.

포털 요청 필드의 경고 확인값 `Y`와 일부 응답 설명/GitHub의 `A`가 일치하지 않아 자동 경고 확인은 구현하지 않았다.

## 공식 명세

- [메리츠 API JSON 정본](https://openapi.imeritz.com/api-spec/download/json)
- [메리츠 미국 예약시간·LOC/MOC·상충주문 안내](https://home.imeritz.com/mobile/content/overseas/usa.html)
- [공식 Python 예제와 운영계좌 안내](https://github.com/meritz-securities/open-api)

## 텔레그램 주문 결과 알림

`.env.meritz`에 두 값을 추가한다. 봇 토큰은 [BotFather](https://t.me/BotFather)에서 만든 봇의 토큰이고, 채팅 ID는 메시지를 받을 개인/그룹의 숫자 ID다. 그룹 ID의 앞자리 `-`를 포함한다. 알림을 받을 채팅에서 봇과 대화를 시작하거나 그룹에 봇을 추가한다. 토큰을 채팅이나 GitHub에 올리지 않는다.

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

```sh
# 브로커 호출이나 주문 없이 텔레그램 연결만 테스트
python kmw_autotrade.py --env-file .env.meritz --test-notification

# 기존 주문 실행 명령에 알림이 자동 적용된다.
python kmw_autotrade.py --env-file .env.meritz --submit --side BUY --quantity-cap 1

# 전송 실패/미설정으로 보류된 알림만 재전송. 주문은 재전송하지 않는다.
python kmw_autotrade.py --env-file .env.meritz --retry-notifications
```

실제 `--submit` 실행이 끝나면 KMW·미국 주문일·한국시간 계산 시각·종목·매수/매도·수량·LOC 가격 또는 MOC·회차와 일반 주문번호 또는 예약번호를 보낸다. 일부 접수 후 실패, 통신 문제로 접수 여부가 불확실한 경우, 사전검증에서 전송이 중단된 경우도 구분해 알린다. 미리보기·계좌조회·`--status`는 자동 알림을 보내지 않는다.

직접 LOC 성공 알림은 **LOC 주문 접수 완료**, 예약 경로는 **예약주문 접수 완료**이며 둘 다 **체결 완료가 아니다**. 체결 여부는 기존 `--status`로 확인한다. 이 기능은 체결을 자동으로 감시하지 않는다.

알림은 `.autotrade/orders.sqlite3`에 별도로 보관한다. 텔레그램 실패는 브로커 주문 상태를 바꾸거나 주문 재시도를 유발하지 않는다. 토큰/채팅 ID가 없거나 설정이 잘못되면 알림을 보류하고 로컬 로그에 표시한다. 설정 후 `--retry-notifications`로 보낼 수 있다. 한 번의 재전송은 최대 20건을 처리한다.

통신 중단으로 알림 전송 여부까지 불확실하면 수동 재전송 시 같은 메시지가 두 번 보일 수 있다. 재시도해도 주문은 다시 넣지 않는다. 이미 확인된 성공 알림은 재전송하지 않는다. 전송 중 프로세스가 종료된 알림은 5분 후 재전송 대상으로 들어간다.

API 전송은 [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage)를 사용한다. 봇 토큰과 앱키, 계좌 잔고, 원본 오류 응답은 알림에 포함하지 않는다.
