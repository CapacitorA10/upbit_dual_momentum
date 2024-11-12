import pyupbit
import pandas as pd
import time
import json
from datetime import datetime, timedelta
import numpy as np
import os
import requests


class UpbitMomentumStrategy:
    def __init__(self, config_path='config.json'):
        """
        설정 파일에서 API 키와 설정을 로드하여 초기화

        Parameters:
        config_path (str): JSON 설정 파일 경로
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
            self.exclude_coins = base_exclude_coins + self.manual_holdings  # 기본 제외 코인 + 수동 보유 코인
            self.max_slots = config['trading'].get('max_slots', 3)  # 기본값 3
            self.rebalancing_interval = config['trading'].get('rebalancing_interval', 10080)  # 기본값 1주일

            # 트래킹 변수 초기화
            self.holding_periods = {}  # 코인별 보유 기간 추적
            self.consecutive_holds = {}  # 연속 보유 횟수 추적

            # 시작 메시지 전송
            self.send_telegram_message("🤖 자동매매 봇이 시작되었습니다.")

        except FileNotFoundError:
            raise Exception(f"설정 파일을 찾을 수 없습니다: {config_path}")
        except json.JSONDecodeError:
            raise Exception(f"설정 파일 형식이 잘못되었습니다: {config_path}")
        except KeyError as e:
            raise Exception(f"필수 설정이 누락되었습니다: {str(e)}")
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
        매핑된 코인들의 시가총액 기준 상위 20개 코인 조회
        """
        try:
            # 매핑 테이블 로드
            with open('coin_mappings.json', 'r') as f:
                mappings = json.load(f)

            # 코인게코 API URL
            coingecko_ids = ','.join(mappings.values())  # 매핑된 모든 코인게코 심볼
            url = f"https://api.coingecko.com/api/v3/simple/price"
            params = {
                'ids': coingecko_ids,
                'vs_currencies': 'usd',
                'include_market_cap': 'true'
            }

            # API 호출
            response = requests.get(url, params=params)
            if response.status_code != 200:
                raise Exception(f"코인게코 API 오류: {response.status_code}")

            data = response.json()

            # 시가총액 정보 추출 및 정렬
            market_caps = []
            for upbit_symbol, gecko_symbol in mappings.items():
                try:
                    if gecko_symbol in data:
                        market_cap = data[gecko_symbol].get('usd_market_cap')
                        if market_cap:
                            market_caps.append({
                                'symbol': upbit_symbol,
                                'market_cap': market_cap,
                                'market_cap_billion': market_cap / 1_000_000_000  # 10억 달러 단위
                            })
                except Exception as e:
                    self.send_telegram_message(f"⚠️ {upbit_symbol} 시가총액 처리 중 오류: {str(e)}")
                    continue

            # 시가총액 기준 정렬
            sorted_coins = sorted(market_caps, key=lambda x: x['market_cap'], reverse=True)
            top_20 = sorted_coins[:20]

            # 결과 로깅
            message = "📊 시가총액 상위 20개 코인:\n"
            for i, coin in enumerate(top_20, 1):
                message += f"{i}. {coin['symbol']}: ${coin['market_cap_billion']:.2f}B\n"
            self.send_telegram_message(message)

            # 업비트 티커 형식으로 변환하여 반환
            return [f"KRW-{coin['symbol']}" for coin in top_20]

        except Exception as e:
            self.send_telegram_message(f"❌ 시가총액 정보 조회 중 오류 발생: {str(e)}")
            return []

    def check_loss_threshold(self, threshold=-10):
        """
        보유 중인 코인들의 손실이 임계값(-10%) 이상인지 확인
        1만원 이상 보유 중인 코인만 체크

        Parameters:
        threshold (float): 손실 임계값 (기본값: -10%)

        Returns:
        bool: 임계값 이상의 손실이 있으면 True, 아니면 False
        """
        try:
            # 수동 보유 코인을 제외한 현재 보유 코인들 확인
            balances = self.upbit.get_balances()
            for balance in balances:
                currency = balance['currency']
                if currency not in self.manual_holdings and currency != 'KRW':
                    # 보유 금액이 1만원 이상인 코인만 체크
                    current_balance = float(balance['balance'])
                    avg_buy_price = float(balance['avg_buy_price'])
                    total_value = current_balance * avg_buy_price

                    if total_value < 10000:  # 1만원 미만 스킵
                        continue

                    ticker = f"KRW-{currency}"

                    # 현재가 조회
                    current_price = pyupbit.get_current_price(ticker)

                    if current_price is None:
                        self.send_telegram_message(f"⚠️ {ticker}의 현재가를 조회할 수 없습니다. (상장폐지 의심)")
                        continue

                    # 수익률 계산
                    profit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100

                    # 설정한 손실 임계값 이상인지 확인
                    if profit_rate <= threshold or True:
                        self.send_telegram_message(
                            f"⚠️ {ticker}의 손실률이 {profit_rate:.2f}%로 임계값({threshold}%)을 초과했습니다.\n"
                            f"보유수량: {current_balance:.8f}\n"
                            f"평균단가: {avg_buy_price:,.0f}원\n"
                            f"현재가: {current_price:,.0f}원\n"
                            f"평가금액: {total_value:,.0f}원"
                        )
                        return True

            return False

        except Exception as e:
            self.send_telegram_message(f"❌ 손실 체크 중 오류 발생: {str(e)}")
            return False

    def calculate_7day_returns(self, tickers):
        """
        7일간의 수익률 계산
        """
        returns = {}
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=8)
            if df is not None and len(df) >= 7:
                seven_day_return = (df['close'].iloc[-1] - df['close'].iloc[-7]) / df['close'].iloc[-7] * 100
                returns[ticker] = seven_day_return

        return returns

    def get_top3_momentum(self):
        """
        모멘텀 상위 3개 코인 선정
        """
        top20 = self.get_top20_market_cap()
        self.send_telegram_message(f"🔍 시가총액 상위 20개 코인: {', '.join(top20)}")
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

    def execute_trades(self):
        """
        매매 실행
        - 현재 보유 중인 코인들과 새로운 매수 대상 코인들을 비교하여 리밸런싱
        """
        # 현재 보유 중인 코인들 (수동 보유 코인 제외, 1만원 이상)
        current_holdings = [
            balance['currency']
            for balance in self.upbit.get_balances()
            if (float(balance['balance']) > 0 and
                balance['currency'] not in self.manual_holdings and
                float(balance['balance']) * float(balance['avg_buy_price']) >= 10000)
        ]

        # 새로운 매수 대상 코인들
        target_coins = self.get_top3_momentum()
        exit()

        # 매도 대상 파악
        for coin in current_holdings:
            ticker = f"KRW-{coin}"
            if ticker not in target_coins or not self.should_keep_coin(ticker):
                try:
                    balance = self.upbit.get_balance(coin)
                    self.send_telegram_message(f"🔄 {ticker} 전량 매도 시도 중...")
                    self.upbit.sell_market_order(ticker, balance)
                    self.send_telegram_message(f"✅ {ticker} 매도 완료")

                    if ticker in self.holding_periods:
                        del self.holding_periods[ticker]
                    self.consecutive_holds[ticker] = 0
                except Exception as e:
                    self.send_telegram_message(f"❌ {ticker} 매도 실패: {str(e)}")

        # 매수 대상 파악
        krw_balance = float(self.upbit.get_balance("KRW"))
        if krw_balance > 0:
            # 현재 자동매매로 보유 중인 코인 수 확인
            auto_holdings_count = len(current_holdings)

            # 남은 슬롯 수에 따라 투자금액 조정
            remaining_slots = self.max_slots - auto_holdings_count
            if remaining_slots > 0:
                invest_amount = krw_balance / remaining_slots  # 남은 슬롯 기준 균등 분할 투자

                for ticker in target_coins:
                    if ticker not in [f"KRW-{coin}" for coin in current_holdings]:
                        try:
                            self.send_telegram_message(f"🛒 {ticker} 매수 시도 중... (금액: {invest_amount:,.0f}원)")
                            self.upbit.buy_market_order(ticker, invest_amount)
                            self.send_telegram_message(f"✅ {ticker} 매수 완료")

                            self.holding_periods[ticker] = datetime.now()
                            self.consecutive_holds[ticker] = self.consecutive_holds.get(ticker, 0) + 1
                        except Exception as e:
                            self.send_telegram_message(f"❌ {ticker} 매수 실패: {str(e)}")

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
        - 보유 코인이 -10% 이상 손실일 때 리밸런싱
        - 최대 1주일 간격으로 리밸런싱
        """
        # last rebalance time은 현재 시간부터 1달 전
        last_rebalance_time = datetime.now() - timedelta(days=30)
        is_trading_suspended = False  # 매매 중지 상태 추적

        while True:
            try:
                current_time = datetime.now()
                btc_above_ma = self.get_btc_ma120()
                has_significant_loss = self.check_loss_threshold() # 단 1개의 코인이라도 -10% 이상 손실이 있다면 바로 return
                time_since_last_rebalance = (current_time - last_rebalance_time).total_seconds() / 60  # 분 단위

                # BTC가 120MA 아래로 떨어진 경우
                if not btc_above_ma:
                    if not is_trading_suspended:
                        message = "😱 BTC가 120일 이평선 아래로 떨어져 전체 매도 후 매매를 중지합니다."
                        self.send_telegram_message(message)
                        self.sell_all_positions()  # 전체 포지션 매도
                        is_trading_suspended = True
                        last_rebalance_time = current_time

                # BTC가 120MA 위로 올라온 경우
                elif btc_above_ma and is_trading_suspended:
                    message = "✅ BTC가 120일 이평선 위 올라왔습니다. 매매를 재개합니다."
                    self.send_telegram_message(message)
                    is_trading_suspended = False
                    self.execute_trades()  # 초기 포지션 진입
                    last_rebalance_time = current_time

                # 정상 매매 상태에서의 리밸런싱 조건 체크
                elif not is_trading_suspended:
                    should_rebalance = (
                            has_significant_loss or  # -10% 이상 손실 발생
                            time_since_last_rebalance >= self.rebalancing_interval  # 1주일 경과
                    )

                    if should_rebalance:
                        message_parts = [
                            "🔄 <b>리밸런싱 실행</b>",
                            f"시간: {current_time.strftime('%Y-%m-%d %H:%M:%S')}",
                            f"BTC 120MA: {'상단 ✅' if btc_above_ma else '하단 ❌'}",
                            f"큰 손실 발생: {'예 ⚠️' if has_significant_loss else '아니오 ✅'}",
                            f"마지막 리밸런싱 후 경과: {time_since_last_rebalance:.1f}분"
                        ]

                        self.send_telegram_message("\n".join(message_parts))
                        self.execute_trades()
                        last_rebalance_time = current_time

                # 1분 간격으로 체크
                time.sleep(60)

            except Exception as e:
                error_message = f"❌ 실행 중 오류 발생: {str(e)}"
                self.send_telegram_message(error_message)
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