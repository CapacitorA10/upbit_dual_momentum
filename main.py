import pyupbit
import pandas as pd
import time
import json
from datetime import datetime, timedelta
import numpy as np
import os
import requests
import signal


class UpbitMomentumStrategy:
    def __init__(self, config_path='config.json'):
        """
        설정 파일에서 API 키와 설정을 로드하여 초기화
        """
        try:
            # 설정 파일 로드
            with open(config_path, 'r') as f:
                config = json.load(f)

            # API 키 설정
            access_key = config['upbit']['access_key']
            secret_key = config['upbit']['secret_key']

            # 텔레그램 설정
            self.telegram_bot_token = config['telegram']['bot_token']
            self.telegram_chat_id = config['telegram']['channel_id']

            # 업비트 API 초기화
            self.upbit = pyupbit.Upbit(access_key, secret_key)

            # 트레이딩 설정 로드
            self.manual_holdings = config['trading']['manual_holdings']
            base_exclude_coins = config['trading']['exclude_coins']
            self.exclude_coins = base_exclude_coins + self.manual_holdings
            self.max_slots = config['trading'].get('max_slots', 3)
            self.rebalancing_interval = config['trading'].get('rebalancing_interval', 10080)
            self.last_purchase_time = None

            # 기존 보유 정보 로드
            self.holdings_file = 'holdings_data.json'
            self.load_holdings_data()

            # 시작 메시지 전송
            self.send_telegram_message("🤖 자동매매 봇이 시작되었습니다.")
            self.sync_holdings_with_current_state()
            self.setup_signal_handlers()

        except Exception as e:
            raise Exception(f"초기화 중 오류 발생: {str(e)}")

    def send_telegram_message(self, message):
        """
        텔레그램으로 메시지 전송

        Parameters:
        message (str): 전송할 메시지
        """
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=payload)

            if not response.ok:
                print(f"텔레그램 메시지 전송 실패: {response.text}")

        except Exception as e:
            print(f"텔레그램 메시지 전송 중 오류 발생: {str(e)}")

    def setup_signal_handlers(self):
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            message = f"⚠️ 프로그램이 {signal_name}에 의해 종료되었습니다."
            self.send_telegram_message(message)
            exit(0)

        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # kill 명령

    def get_btc_ma120(self):
        """
        비트코인의 120일 이동평균선 계산 및 현재가와 비교
        """
        df = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=120)
        ma120 = df['close'].rolling(window=120).mean().iloc[-1]
        current_price = pyupbit.get_current_price("KRW-BTC")
        return current_price > ma120

    def get_top20_market_cap(self):
        """
        시가총액 상위 20개 코인 조회 (제외 코인 제외)
        CoinGecko API를 활용하여 실제 시가총액 기준으로 정렬
        """
        try:
            # 업비트 상장 코인 목록 가져오기
            tickers = pyupbit.get_tickers(fiat="KRW")
            symbols = [ticker.split('-')[1] for ticker in tickers]

            # CoinGecko에서 상위 300위 코인 목록 가져오기
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 300,
                "page": 1,
                "sparkline": False  # 불필요한 데이터 제외
            }

            response = requests.get(url, params=params)
            if response.status_code != 200:
                raise Exception(f"CoinGecko API 오류: {response.status_code}")

            top_300_coins = response.json()

            # CoinGecko 심볼 기준으로 코인 데이터 매핑
            coin_gecko_symbol_map = {coin['symbol'].upper(): coin for coin in top_300_coins}

            # 업비트 코인들의 시가총액 매핑
            market_caps = []
            for symbol in symbols:
                if symbol not in self.exclude_coins:
                    if symbol in coin_gecko_symbol_map:
                        coin_data = coin_gecko_symbol_map[symbol]
                        market_cap = coin_data['market_cap']
                        if market_cap:  # None이나 0이 아닌 경우만 추가
                            market_caps.append((f"KRW-{symbol}", market_cap, coin_data['market_cap_rank']))

            if not market_caps:
                raise Exception("시가총액 계산 가능한 코인이 없습니다.")

            # 시가총액 기준 정렬 및 상위 20개 추출
            sorted_market_caps = sorted(market_caps, key=lambda x: x[1], reverse=True)
            top_20 = sorted_market_caps[:20]

            # 로그 출력
            market_cap_msg = "📊 시가총액 상위 20개 코인:\n"
            for i, (ticker, cap, rank) in enumerate(top_20):
                market_cap_billion_usd = cap / 1_000_000_000  # 10억 달러 단위로 변환
                market_cap_msg += (f"{i + 1}. {ticker} "
                                   f"(세계 순위: #{rank}) - "
                                   f"${market_cap_billion_usd:.1f}B\n")
            self.send_telegram_message(market_cap_msg)

            return [item[0] for item in top_20]

        except Exception as e:
            self.send_telegram_message(f"❌ 시가총액 상위 코인 조회 중 오류 발생: {str(e)}")
            time.sleep(1)  # API 오류 시 잠시 대기
            return []

    def check_loss_threshold(self, threshold=-10):
        """
        보유 중인 코인들의 손실이 임계값(-10%) 이상인지 확인하고, 해당 코인만 매도

        Parameters:
        threshold (float): 손실 임계값 (기본값: -10%)

        Returns:
        list: 손실이 임계값 이상인 코인 목록
        """
        try:
            balances = self.upbit.get_balances()
            sold_coins = []
            for balance in balances:
                currency = balance['currency']
                if currency not in self.manual_holdings and currency != 'KRW':
                    current_balance = float(balance['balance'])
                    avg_buy_price = float(balance['avg_buy_price'])
                    total_value = current_balance * avg_buy_price

                    if total_value < 10000: # 1만원 미만은 제외
                        continue

                    ticker = f"KRW-{currency}"

                    # 현재가 조회
                    current_price = pyupbit.get_current_price(ticker)
                    time.sleep(0.1)
                    if current_price is None:
                        self.send_telegram_message(f"⚠️ {ticker}의 현재가를 조회할 수 없습니다. (상장폐지 의심)")
                        continue

                    # 수익률 계산
                    profit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100

                    if profit_rate <= threshold:
                        # 손절 매도 로직
                        self.send_telegram_message(
                            f"⚠️ {ticker}의 손실률이 {profit_rate:.2f}%로 임계값({threshold}%)을 초과하여 매도합니다.\n"
                            f"보유수량: {current_balance:.8f}\n"
                            f"평균단가: {avg_buy_price:,.0f}원\n"
                            f"현재가: {current_price:,.0f}원\n"
                            f"평가금액: {total_value:,.0f}원"
                        )
                        try:
                            self.upbit.sell_market_order(ticker, current_balance)
                            self.send_telegram_message(f"✅ {ticker} 매도 완료")
                            sold_coins.append(ticker)
                        except Exception as e:
                            self.send_telegram_message(f"❌ {ticker} 매도 실패: {str(e)}")

            # 보유 상태 최신화
            self.sync_holdings_with_current_state()

            return sold_coins

        except Exception as e:
            self.send_telegram_message(f"❌ 손실 체크 중 오류 발생: {str(e)}")
            return []


    def calculate_7day_returns(self, tickers):
        """
        7일간의 수익률 계산
        """
        returns = {}
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=8)
            time.sleep(0.2) # 요청 제한을 피하기 위한 대기 시간
            if df is not None and len(df) >= 7:
                seven_day_return = (df['close'].iloc[-1] - df['close'].iloc[-7]) / df['close'].iloc[-7] * 100
                returns[ticker] = seven_day_return

        return returns

    def get_top3_momentum(self):
        """
        모멘텀 상위 3개 코인 선정
        """
        top20 = self.get_top20_market_cap()
        returns = self.calculate_7day_returns(top20)
        self.send_telegram_message(f"📈 7일 수익률: {returns}")

        # 수익률 기준 정렬
        sorted_returns = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        self.send_telegram_message(f"🔝 7일 수익률 상위 3개: {sorted_returns[:3]}")
        return [coin[0] for coin in sorted_returns[:3]]

    def should_keep_coin(self, ticker):
        """
        코인 보유 여부 결정
        - 최대 2주 보유
        - 3번 연속 보유 불가
        """
        current_time = datetime.now()

        # 보유 기간 체크
        if ticker in self.holding_periods:
            holding_days = (current_time - self.holding_periods[ticker]).days
            if holding_days >= 14:  # 2주 이상 보유 시 매도
                return False

        # 연속 보유 횟수 체크
        if ticker in self.consecutive_holds and self.consecutive_holds[ticker] >= 3:
            return False

        return True

    def load_holdings_data(self):
        """보유 정보 파일에서 데이터 로드"""
        try:
            if os.path.exists(self.holdings_file):
                with open(self.holdings_file, 'r') as f:
                    data = json.load(f)
                    self.holding_periods = {k: datetime.fromisoformat(v) for k, v in data['holding_periods'].items()}
                    self.consecutive_holds = data['consecutive_holds']
            else:
                self.holding_periods = {}
                self.consecutive_holds = {}
        except Exception as e:
            self.send_telegram_message(f"보유 정보 로드 중 오류 발생: {str(e)}")
            self.holding_periods = {}
            self.consecutive_holds = {}

    def save_holdings_data(self):
        """보유 정보를 파일에 저장"""
        try:
            data = {
                'holding_periods': {k: v.isoformat() for k, v in self.holding_periods.items()},
                'consecutive_holds': self.consecutive_holds
            }
            with open(self.holdings_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.send_telegram_message(f"보유 정보 저장 중 오류 발생: {str(e)}")

    def sync_holdings_with_current_state(self):
        """현재 실제 보유 상태와 기록된 보유 정보 동기화"""
        try:
            # 실제 보유 중인 코인 확인
            current_holdings = {
                f"KRW-{balance['currency']}"
                for balance in self.upbit.get_balances()
                if (float(balance['balance']) > 0 and
                    balance['currency'] not in self.manual_holdings and
                    float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
            }

            # 기록된 보유 정보와 실제 보유 상태 비교 및 동기화
            recorded_holdings = set(self.holding_periods.keys())

            # 더 이상 보유하지 않는 코인 제거
            for ticker in recorded_holdings - current_holdings:
                del self.holding_periods[ticker]
                self.consecutive_holds[ticker] = 0

            # 새로 보유한 코인 추가 (처음 시작할 때)
            for ticker in current_holdings - recorded_holdings:
                self.holding_periods[ticker] = datetime.now()
                self.consecutive_holds[ticker] = self.consecutive_holds.get(ticker, 0) + 1

            self.save_holdings_data()

        except Exception as e:
            self.send_telegram_message(f"보유 상태 동기화 중 오류 발생: {str(e)}")

    def execute_trades(self):
        """매매 실행 - 현재 보유 중인 코인들과 새로운 매수 대상 코인들을 비교하여 리밸런싱"""
        try:
            # 현재 보유 중인 코인들 확인 (수동 보유 코인 제외, 1만원 이상)
            current_holdings = [
                balance['currency']
                for balance in self.upbit.get_balances()
                if (float(balance['balance']) > 0 and
                    balance['currency'] not in self.manual_holdings and
                    float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
            ]

            # 새로운 매수 대상 코인들
            target_coins = self.get_top3_momentum()

            # 매도 대상 파악 및 매도
            sold_coins = []  # 매도된 코인 추적
            for coin in current_holdings:
                ticker = f"KRW-{coin}"
                if ticker not in target_coins or not self.should_keep_coin(ticker):
                    try:
                        balance = self.upbit.get_balance(coin)
                        self.send_telegram_message(f"🔄 {ticker} 전량 매도 시도 중...")
                        self.upbit.sell_market_order(ticker, balance)
                        self.send_telegram_message(f"✅ {ticker} 매도 완료")

                        # 매도 성공한 코인 기록
                        sold_coins.append(coin)

                        if ticker in self.holding_periods:
                            del self.holding_periods[ticker]
                        self.consecutive_holds[ticker] = 0
                    except Exception as e:
                        self.send_telegram_message(f"❌ {ticker} 매도 실패: {str(e)}")

            # 매도된 코인들을 current_holdings에서 제거
            current_holdings = [coin for coin in current_holdings if coin not in sold_coins]

            # 매수 대상 파악 및 매수
            krw_balance = float(self.upbit.get_balance("KRW"))
            if krw_balance > 0:
                auto_holdings_count = len(current_holdings)
                remaining_slots = self.max_slots - auto_holdings_count
                if remaining_slots > 0:
                    invest_amount = krw_balance / remaining_slots
                    invest_amount = int(invest_amount / 1000) * 1000
                    if invest_amount < 5000:
                        self.send_telegram_message(f"⚠️ 투자금액({invest_amount:,.0f}원)이 최소 거래금액(5,000원) 미만입니다.")
                        return

                    for ticker in target_coins:
                        if ticker not in [f"KRW-{coin}" for coin in current_holdings]:
                            try:
                                self.send_telegram_message(f"🛒 {ticker} 매수 시도 중... (금액: {invest_amount:,.0f}원)")
                                self.upbit.buy_market_order(ticker, invest_amount)
                                self.send_telegram_message(f"✅ {ticker} 매수 완료")

                                self.holding_periods[ticker] = datetime.now()
                                self.consecutive_holds[ticker] = self.consecutive_holds.get(ticker, 0) + 1

                                # 매수 시점 기록
                                self.last_purchase_time = datetime.now()

                                # 매수 성공한 코인을 current_holdings에 추가
                                current_holdings.append(ticker.split('-')[1])
                            except Exception as e:
                                self.send_telegram_message(f"❌ {ticker} 매수 실패: {str(e)}")

            self.save_holdings_data()

        except Exception as e:
            self.send_telegram_message(f"❌ 매매 실행 중 오류 발생: {str(e)}")


    def sell_all_positions(self):
        """
        모든 보유 포지션 매도 (수동 보유 코인 제외)
        """
        try:
            current_holdings = [balance['currency'] for balance in self.upbit.get_balances()
                                if float(balance['balance']) > 0 and
                                balance['currency'] not in self.manual_holdings and
                                float(balance['balance']) * float(balance['avg_buy_price']) >= 10000]

            for coin in current_holdings:
                ticker = f"KRW-{coin}"
                balance = self.upbit.get_balance(coin)
                if balance > 0:
                    try:
                        self.send_telegram_message(f"🔄 {ticker} 전량 매도 시도 중...")
                        self.upbit.sell_market_order(ticker, balance)
                        self.send_telegram_message(f"✅ {ticker} 매도 완료")

                        if ticker in self.holding_periods:
                            del self.holding_periods[ticker]
                        self.consecutive_holds[ticker] = 0
                    except Exception as e:
                        self.send_telegram_message(f"❌ {ticker} 매도 실패: {str(e)}")
        except Exception as e:
            self.send_telegram_message(f"❌ 전체 매도 중 오류 발생: {str(e)}")

    def run(self):
        """
        동적 조건에 따른 전략 실행
        - BTC가 120일 이평선 아래일 때 전량 매도하고 매수 중지
        - BTC가 120일 이평선 위로 올라올 때 매수 알고리즘 재개
        - 보유 코인이 -10% 이상 손실일 때 매도
        - 가장 오래된 보유 코인 기준으로 1주일 간격 리밸런싱
        """
        is_trading_suspended = False

        while True:
            try:
                current_time = datetime.now()
                btc_above_ma = self.get_btc_ma120()

                # 보유 코인 체크
                current_holdings = [
                    balance['currency']
                    for balance in self.upbit.get_balances()
                    if (float(balance['balance']) > 0 and
                        balance['currency'] not in self.manual_holdings and
                        float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
                ]
                holding_count = len(current_holdings)
                holding_name = ', '.join(current_holdings)

                # 손실 코인 매도
                sold_coins = self.check_loss_threshold(threshold=-20)

                # 손실 코인 매도 후 보유 상태 동기화
                self.sync_holdings_with_current_state()

                # BTC가 120MA 아래로 떨어진 경우
                if not btc_above_ma:
                    if not is_trading_suspended:
                        self.send_telegram_message("😱 BTC가 120일 이평선 아래로 떨어져 전체 매도 후 매매를 중지합니다.")
                        self.sell_all_positions()
                        is_trading_suspended = True

                elif btc_above_ma and is_trading_suspended:
                    self.send_telegram_message("✅ BTC가 120일 이평선 위 올라왔습니다. 매매를 재개합니다.")
                    is_trading_suspended = False
                    self.execute_trades()

                elif not is_trading_suspended:
                    if holding_count == 0:
                        # 모든 코인이 매도되었을 때 - 새로운 매수 시점 기준으로 리밸런싱
                        if self.last_purchase_time:
                            elapsed_minutes_since_last_purchase = (current_time - self.last_purchase_time).total_seconds() / 60
                            if elapsed_minutes_since_last_purchase >= self.rebalancing_interval:
                                self.send_telegram_message("🔄 모든 코인 매도 후 리밸런싱을 위한 새로운 매수 시점이 도래했습니다.")
                                self.execute_trades()
                        else:
                            self.send_telegram_message("🔄 모든 코인이 매도되었지만, 최근 매수 시점이 기록되지 않았습니다 거래를 재개합니다.")
                            self.execute_trades()
                    else:
                        should_rebalance = (
                            len(sold_coins) > 0 or  # 일부 코인이 매도된 경우
                            holding_count < 3  # 보유 코인 수가 3개 미만일 경우
                        )

                        if should_rebalance:
                            self.send_telegram_message("🔄 <b>리밸런싱 실행</b> - 일부 코인 매도 후 재매수 또는 보유 코인 수 부족")
                            self.send_telegram_message(f"📈 현재 보유 코인: {holding_name}")
                            self.execute_trades()

                time.sleep(60)

            except Exception as e:
                self.send_telegram_message(f"❌ 실행 중 오류 발생: {str(e)}")
                time.sleep(60)




# 사용 예시
if __name__ == "__main__":
    try:
        # 기본 설정 파일 경로는 'config.json'
        strategy = UpbitMomentumStrategy()

        # 다른 경로의 설정 파일을 사용하려면:
        # strategy = UpbitMomentumStrategy('path/to/your/config.json')

        strategy.run()
    except Exception as e:
        print(f"오류 발생: {str(e)}")