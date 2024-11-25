import pyupbit
import pandas as pd
import time
import json
from datetime import datetime
import os
import requests
import signal

class UpbitMomentumStrategy:
    def __init__(self, config_path='config.json'):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)

            self.upbit = pyupbit.Upbit(config['upbit']['access_key'], config['upbit']['secret_key'])
            self.telegram_bot_token = config['telegram']['bot_token']
            self.telegram_chat_id = config['telegram']['channel_id']
            self.manual_holdings = config['trading']['manual_holdings']
            self.exclude_coins = config['trading']['exclude_coins'] + self.manual_holdings
            self.max_slots = config['trading'].get('max_slots', 3)
            self.rebalancing_interval = config['trading'].get('rebalancing_interval', 10080) * 60 # 일 단위로 변환
            self.last_purchase_time = None
            self.holdings_file = 'holdings_data.json'

            self.load_holdings_data()
            self.send_telegram_message("🤖 자동매매 봇이 시작되었습니다.")
            self.sync_holdings_with_current_state()
            self.setup_signal_handlers()
        except Exception as e:
            raise Exception(f"초기화 중 오류 발생: {e}")

    def send_telegram_message(self, message):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "HTML"}
            )
            if not response.ok:
                print(f"텔레그램 메시지 전송 실패: {response.text}")
        except Exception as e:
            print(f"텔레그램 메시지 전송 중 오류 발생: {e}")

    def setup_signal_handlers(self):
        def handler(signum, frame):
            self.send_telegram_message(f"⚠️ 프로그램이 {signal.Signals(signum).name}에 의해 종료되었습니다.")
            exit(0)
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, handler)

    def get_btc_ma120(self):
        df = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=120)
        return pyupbit.get_current_price("KRW-BTC") > df['close'].mean()

    def get_top20_market_cap(self):
        try:
            tickers = [ticker for ticker in pyupbit.get_tickers(fiat="KRW")
                       if ticker.split('-')[1] not in self.exclude_coins]
            response = requests.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 300, "page": 1, "sparkline": False}
            )
            response.raise_for_status()
            top_coins = {coin['symbol'].upper(): coin for coin in response.json()}
            market_caps = [
                (f"KRW-{symbol}", coin['market_cap'], coin['market_cap_rank'])
                for ticker in tickers
                if (symbol := ticker.split('-')[1].upper()) in top_coins and (coin := top_coins[symbol]).get('market_cap')
            ]
            top20 = sorted(market_caps, key=lambda x: x[1], reverse=True)[:20]
            market_cap_msg = "📊 시가총액 상위 20개 코인:\n" + "\n".join(
                [f"{i+1}. {ticker} (세계 순위: #{rank}) - ${cap/1e9:.1f}B"
                 for i, (ticker, cap, rank) in enumerate(top20)]
            )
            self.send_telegram_message(market_cap_msg)
            return [item[0] for item in top20]
        except Exception as e:
            self.send_telegram_message(f"❌ 시가총액 상위 코인 조회 중 오류 발생: {e}")
            time.sleep(1)
            return []

    def check_loss_threshold(self, threshold=-10):
        sold = []
        try:
            for balance in self.upbit.get_balances():
                currency = balance['currency']

                # 수동 보유 코인은 손실률 체크 제외
                if currency in self.manual_holdings or currency == 'KRW':
                    continue

                balance_amt = float(balance['balance'])
                avg_price = float(balance['avg_buy_price'])
                if balance_amt * avg_price < 10000:
                    continue

                current_price = pyupbit.get_current_price(f"KRW-{currency}")
                if not current_price:
                    self.send_telegram_message(f"⚠️ {currency}의 현재가를 조회할 수 없습니다.")
                    continue

                profit = ((current_price - avg_price) / avg_price) * 100

                if profit <= threshold:
                    msg = (f"⚠️ {currency}의 손실률이 {profit:.2f}%로 임계값({threshold}%)을 초과하여 매도합니다.\n"
                           f"보유수량: {balance_amt:.8f}\n평균단가: {avg_price:,.0f}원\n"
                           f"현재가: {current_price:,.0f}원\n평가금액: {balance_amt * avg_price:,.0f}원")
                    self.send_telegram_message(msg)

                    try:
                        self.upbit.sell_market_order(f"KRW-{currency}", balance_amt)
                        self.send_telegram_message(f"✅ {currency} 매도 완료")
                        sold.append(f"KRW-{currency}")

                    except Exception as e:
                        self.send_telegram_message(f"❌ {currency} 매도 실패: {e}")

            self.sync_holdings_with_current_state()

        except Exception as e:
            self.send_telegram_message(f"❌ 손실 체크 중 오류 발생: {e}")
        return sold

    def calculate_7day_returns(self, tickers):
        returns = {}
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=8)
            if df is not None and len(df) >= 7:
                returns[ticker] = ((df['close'].iloc[-1] - df['close'].iloc[-7]) / df['close'].iloc[-7]) * 100
            time.sleep(0.2)
        self.send_telegram_message(f"📈 7일 수익률: {returns}")
        sorted_returns = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_returns[:3]
        self.send_telegram_message(f"🔝 7일 수익률 상위 3개: {top3}")
        return [coin[0] for coin in top3]

    def get_top3_momentum(self):
        return self.calculate_7day_returns(self.get_top20_market_cap())

    def should_keep_coin(self, ticker):
        now = datetime.now()
        holding_days = (now - self.holding_periods.get(ticker, now)).days
        if holding_days >= 14 or self.consecutive_holds.get(ticker, 0) >= 3:
            return False
        return True

    def load_holdings_data(self):
        try:
            if os.path.exists(self.holdings_file):
                with open(self.holdings_file, 'r') as f:
                    data = json.load(f)
                    self.holding_periods = {k: datetime.fromisoformat(v) for k, v in
                                            data.get('holding_periods', {}).items()}
                    self.consecutive_holds = data.get('consecutive_holds', {})

                    # 가장 오래된 보유 기간을 기준으로 last_purchase_time 설정
                    if self.holding_periods:
                        self.last_purchase_time = min(self.holding_periods.values())
                        self.send_telegram_message(
                            f"📅 가장 오래된 보유 기간 기준으로 last_purchase_time 초기화: {self.last_purchase_time}")
                    else:
                        self.last_purchase_time = None
            else:
                self.holding_periods = {}
                self.consecutive_holds = {}
                self.last_purchase_time = None

        except Exception as e:
            self.send_telegram_message(f"❌ 보유 정보 로드 중 오류 발생: {e}")
            self.holding_periods, self.consecutive_holds = {}, {}
            self.last_purchase_time = None

    def save_holdings_data(self):
        try:
            data = {
                'holding_periods': {k: v.isoformat() for k, v in self.holding_periods.items()},
                'consecutive_holds': self.consecutive_holds
            }
            with open(self.holdings_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.send_telegram_message(f"보유 정보 저장 중 오류 발생: {e}")

    def sync_holdings_with_current_state(self):
        try:
            current_holdings = {
                f"KRW-{balance['currency']}"
                for balance in self.upbit.get_balances()
                if (float(balance['balance']) > 0 and
                    balance['currency'] not in self.manual_holdings and
                    float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
            }

            recorded = set(self.holding_periods.keys())

            for ticker in recorded - current_holdings:
                del self.holding_periods[ticker]
                self.consecutive_holds[ticker] = 0

            for ticker in current_holdings - recorded:
                self.holding_periods[ticker] = datetime.now()
                self.consecutive_holds[ticker] = self.consecutive_holds.get(ticker, 0) + 1

            self.save_holdings_data()

        except Exception as e:
            self.send_telegram_message(f"보유 상태 동기화 중 오류 발생: {e}")

    def execute_trades(self):
        try:
            current_holdings = [
                balance['currency']
                for balance in self.upbit.get_balances()
                if (float(balance['balance']) > 0 and
                    balance['currency'] not in self.manual_holdings and
                    float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
            ]
            target_coins = self.get_top3_momentum()
            sold = []
            for coin in current_holdings:
                ticker = f"KRW-{coin}"
                if ticker not in target_coins or not self.should_keep_coin(ticker):
                    try:
                        balance_amt = self.upbit.get_balance(coin)
                        self.send_telegram_message(f"🔄 {ticker} 전량 매도 시도 중...")
                        self.upbit.sell_market_order(ticker, balance_amt)
                        self.send_telegram_message(f"✅ {ticker} 매도 완료")
                        sold.append(coin)
                        self.holding_periods.pop(ticker, None)
                        self.consecutive_holds[ticker] = 0
                    except Exception as e:
                        self.send_telegram_message(f"❌ {ticker} 매도 실패: {e}")

            krw_balance = float(self.upbit.get_balance("KRW"))
            if krw_balance > 0 and (slots := self.max_slots - (len(current_holdings) - len(sold))) > 0:
                invest = max(int(krw_balance / slots / 1000) * 1000, 5000)
                for ticker in target_coins:
                    if ticker not in [f"KRW-{c}" for c in current_holdings]:
                        try:
                            self.send_telegram_message(f"🛒 {ticker} 매수 시도 중... (금액: {invest:,}원)")
                            self.upbit.buy_market_order(ticker, invest)
                            self.send_telegram_message(f"✅ {ticker} 매수 완료")
                            self.holding_periods[ticker] = datetime.now()
                            self.consecutive_holds[ticker] = self.consecutive_holds.get(ticker, 0) + 1
                            self.last_purchase_time = datetime.now()
                            current_holdings.append(ticker.split('-')[1])
                        except Exception as e:
                            self.send_telegram_message(f"❌ {ticker} 매수 실패: {e}")
            self.save_holdings_data()
        except Exception as e:
            self.send_telegram_message(f"❌ 매매 실행 중 오류 발생: {e}")

    def sell_all_positions(self):
        try:
            for balance in self.upbit.get_balances():
                currency = balance['currency']

                if currency in self.manual_holdings or float(balance['balance']) * float(balance['avg_buy_price']) < 10000:
                    continue

                ticker = f"KRW-{currency}"

                try:
                    balance_amt = self.upbit.get_balance(currency)
                    self.send_telegram_message(f"🔄 {ticker} 전량 매도 시도 중...")
                    self.upbit.sell_market_order(ticker, balance_amt)
                    self.send_telegram_message(f"✅ {ticker} 매도 완료")
                    self.holding_periods.pop(ticker, None)
                    self.consecutive_holds[ticker] = 0

                except Exception as e:
                    self.send_telegram_message(f"❌ {ticker} 매도 실패: {e}")

        except Exception as e:
            self.send_telegram_message(f"❌ 전체 매도 중 오류 발생: {e}")

    def run(self):
        is_suspended = False
        while True:
            try:
                btc_above_ma = self.get_btc_ma120() # BTC 120일 이평선 상위인지 확인
                sold_coins = self.check_loss_threshold(threshold=-20) # 손절 체크 후 매도
                self.sync_holdings_with_current_state()

                if not btc_above_ma:
                    if not is_suspended:
                        self.send_telegram_message("😱 BTC가 120일 이평선 아래로 떨어져 전체 매도 후 매매를 중지합니다.")
                        self.sell_all_positions()
                        is_suspended = True
                else:
                    if is_suspended: # 매매 재개 체크
                        self.send_telegram_message("✅ BTC가 120일 이평선 위 올라왔습니다. 매매를 재개합니다.")
                        is_suspended = False
                        self.execute_trades()

                # 리밸런싱 조건 체크
                holding_count = len([
                    balance['currency']
                    for balance in self.upbit.get_balances()
                    if (
                            float(balance['balance']) > 0 and  # 잔액이 0보다 큰 경우
                            balance['currency'] not in self.manual_holdings and  # manual_holdings에 없는 경우
                            float(balance['balance']) * float(balance['avg_buy_price']) >= 10000  # 총 가치가 10,000 이상인 경우
                    )
                ])

                # 손절 매도가 없고 보유 코인 수가 max_slots보다 작은 경우
                if (not sold_coins) and (holding_count < self.max_slots) and (not is_suspended):
                    self.send_telegram_message(f"보유 코인이 {self.max_slots}개 보다 적은 상태입니다. 매매를 실행합니다.")
                    self.execute_trades()
                # 리밸런싱 주기마다 매매 실행
                elif (self.last_purchase_time is not None) and (
                        (datetime.now() - self.last_purchase_time).total_seconds() >= self.rebalancing_interval):
                    self.send_telegram_message(f"리밸런싱 주기가 도래하여 매매를 실행합니다.")
                    self.execute_trades()


                time.sleep(60)
            except Exception as e:
                self.send_telegram_message(f"❌ 실행 중 오류 발생: {e}")
                time.sleep(60)

if __name__ == "__main__":
    try:
        UpbitMomentumStrategy().run()
    except Exception as e:
        print(f"오류 발생: {e}")
