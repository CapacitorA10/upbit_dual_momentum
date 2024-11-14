import pyupbit
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import json
import time


class UpbitMomentumBacktest:
    def __init__(self, start_date, end_date, config_path='config.json'):
        """
        백테스팅 초기화

        Parameters:
        start_date (str): 백테스팅 시작일 (YYYY-MM-DD)
        end_date (str): 백테스팅 종료일 (YYYY-MM-DD)
        config_path (str): 설정 파일 경로
        """
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")

        # 설정 파일 로드
        with open(config_path, 'r') as f:
            config = json.load(f)

        # 트레이딩 설정 로드
        self.manual_holdings = config['trading']['manual_holdings']
        base_exclude_coins = config['trading']['exclude_coins']
        self.exclude_coins = base_exclude_coins + self.manual_holdings
        self.max_slots = config['trading'].get('max_slots', 3)
        self.rebalancing_interval = config['trading'].get('rebalancing_interval', 10080)  # 분 단위

        # 트래킹 변수 초기화
        self.holding_periods = {}
        self.consecutive_holds = {}
        self.is_trading_suspended = False

        # 포트폴리오 초기화
        self.portfolio = {'KRW': 1000000}  # 초기 자금 1,000,000 KRW
        self.portfolio_history = []
        self.trade_log = []

        # 리밸런싱 마지막 시간 초기화
        self.last_rebalance_time = self.start_date - timedelta(minutes=self.rebalancing_interval)

        # 로깅을 위한 설정 (옵션)
        self.verbose = True

        # 요청 헤더 설정
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                          ' Chrome/58.0.3029.110 Safari/537.3'
        }

    def log(self, message):
        if self.verbose:
            print(message)

    def get_coin_list(self):
        """
        업비트 상장 코인 목록 가져오기
        """
        tickers = pyupbit.get_tickers(fiat="KRW")
        symbols = [ticker.split('-')[1] for ticker in tickers]
        return symbols

    def get_market_cap_data(self, coin_list):
        """
        CoinGecko API를 사용하여 시가총액 데이터를 가져오기

        Parameters:
        coin_list (list): 코인 심볼 리스트

        Returns:
        dict: 코인별 날짜별 시가총액
        """
        coin_market_caps = {}
        # CoinGecko의 모든 코인 리스트 가져오기
        self.log("Fetching CoinGecko coin list...")
        coin_list_url = "https://api.coingecko.com/api/v3/coins/list"
        try:
            response = requests.get(coin_list_url, headers=self.headers)
            if response.status_code != 200:
                self.log(f"CoinGecko 코인 리스트 가져오기 실패: {response.status_code}")
                return coin_market_caps
            cg_coins = response.json()
        except Exception as e:
            self.log(f"CoinGecko 코인 리스트 가져오기 중 오류 발생: {str(e)}")
            return coin_market_caps

        for coin in coin_list:
            self.log(f"Fetching market cap for {coin} from CoinGecko...")
            # CoinGecko의 coin list에서 심볼을 매칭하여 ID 찾기
            cg_coin = next((c for c in cg_coins if c['symbol'].upper() == coin.upper()), None)
            if not cg_coin:
                self.log(f"CoinGecko에서 {coin}을(를) 찾을 수 없습니다.")
                continue
            cg_id = cg_coin['id']

            # 시가총액 히스토리 데이터 가져오기
            market_cap_url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
            params = {
                'vs_currency': 'usd',
                'days': 'max'
            }
            try:
                response = requests.get(market_cap_url, headers=self.headers, params=params)
                if response.status_code == 401:
                    self.log(f"CoinGecko API 인증 오류: {response.status_code} for {cg_id}")
                    continue
                elif response.status_code != 200:
                    self.log(f"CoinGecko API 오류 ({response.status_code}) for {cg_id}")
                    continue
                data = response.json()
                # 'market_caps'는 [timestamp, market_cap] 형식
                market_caps = {}
                for entry in data['market_caps']:
                    date = datetime.utcfromtimestamp(entry[0] / 1000).strftime("%Y-%m-%d")
                    if self.start_date.strftime("%Y-%m-%d") <= date <= self.end_date.strftime("%Y-%m-%d"):
                        market_caps[date] = entry[1]
                coin_market_caps[coin.upper()] = market_caps
                time.sleep(1)  # 요청 간 지연 시간 (1초)
            except Exception as e:
                self.log(f"CoinGecko API 호출 중 오류 발생 for {cg_id}: {str(e)}")
                continue
        return coin_market_caps

    def load_historical_data(self, ticker, start_date, end_date):
        """
        특정 티커의 과거 가격 데이터 로드

        Parameters:
        ticker (str): 티커 심볼 (예: "KRW-BTC")
        start_date (datetime): 시작일
        end_date (datetime): 종료일

        Returns:
        DataFrame: 과거 가격 데이터
        """
        try:
            df = pyupbit.get_ohlcv(ticker, interval="day", from_=start_date, to=end_date)
            return df
        except Exception as e:
            self.log(f"{ticker}의 데이터 로드 실패: {str(e)}")
            return pd.DataFrame()

    def get_btc_ma120(self, df_btc):
        """
        비트코인의 120일 이동평균선 계산

        Parameters:
        df_btc (DataFrame): 비트코인 가격 데이터

        Returns:
        Series: 120일 이동평균선
        """
        return df_btc['close'].rolling(window=120).mean()

    def get_top20_market_cap(self, date_str, coin_market_caps):
        """
        특정 날짜의 시가총액 상위 20개 코인 조회

        Parameters:
        date_str (str): 조회 날짜 (YYYY-MM-DD)
        coin_market_caps (dict): 코인별 날짜별 시가총액 데이터

        Returns:
        list: 상위 20개 코인의 티커 리스트
        """
        market_cap_today = {}
        for coin, caps in coin_market_caps.items():
            cap = caps.get(date_str, 0)
            if cap > 0:
                market_cap_today[coin] = cap

        # 시가총액 기준 정렬
        sorted_coins = sorted(market_cap_today.items(), key=lambda x: x[1], reverse=True)
        top20 = [f"KRW-{coin}" for coin, cap in sorted_coins[:20] if f"KRW-{coin}" not in self.exclude_coins]
        return top20

    def calculate_7day_return(self, df, current_date):
        """
        특정 날짜의 7일 수익률 계산

        Parameters:
        df (DataFrame): 코인 가격 데이터
        current_date (datetime): 기준 날짜

        Returns:
        float: 7일 수익률
        """
        past_date = current_date - timedelta(days=7)
        past_date_str = past_date.strftime("%Y-%m-%d")
        current_date_str = current_date.strftime("%Y-%m-%d")
        if past_date_str in df.index and current_date_str in df.index:
            past_close = df.loc[past_date_str]['close']
            current_close = df.loc[current_date_str]['close']
            return ((current_close - past_close) / past_close) * 100
        else:
            return -np.inf  # 데이터 부족 시 극단적인 손실률 반환

    def get_top3_momentum(self, date_str, top20, all_price_data):
        """
        모멘텀 상위 3개 코인 선정

        Parameters:
        date_str (str): 기준 날짜 (YYYY-MM-DD)
        top20 (list): 시가총액 상위 20개 코인 티커 리스트
        all_price_data (dict): 코인별 가격 데이터

        Returns:
        list: 모멘텀 상위 3개 코인 티커 리스트
        """
        returns = {}
        current_date = datetime.strptime(date_str, "%Y-%m-%d")
        for ticker in top20:
            df = all_price_data.get(ticker, pd.DataFrame())
            if df.empty:
                continue
            seven_day_return = self.calculate_7day_return(df, current_date)
            if seven_day_return > -100:  # 정상적인 수익률만 고려
                returns[ticker] = seven_day_return

        # 수익률 기준 정렬
        sorted_returns = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        top3 = [coin for coin, ret in sorted_returns[:3]]
        return top3

    def get_portfolio_value(self, date_str, all_price_data):
        """
        특정 날짜의 포트폴리오 총 가치 계산

        Parameters:
        date_str (str): 날짜 문자열 (YYYY-MM-DD)
        all_price_data (dict): 코인별 가격 데이터

        Returns:
        float: 포트폴리오 총 가치 (KRW)
        """
        total = self.portfolio.get('KRW', 0)
        for ticker, amount in self.portfolio.items():
            if ticker == 'KRW' or amount <= 0:
                continue
            coin = ticker.split('-')[1]
            df = all_price_data.get(ticker, pd.DataFrame())
            if df.empty or date_str not in df.index:
                price = 0  # 데이터 없으면 가격을 0으로 가정
            else:
                price = df.loc[date_str]['close']
            total += amount * price
        return total

    def sell_all(self, date_str, all_price_data):
        """
        모든 보유 포지션 매도

        Parameters:
        date_str (str): 매도 날짜 (YYYY-MM-DD)
        all_price_data (dict): 코인별 가격 데이터
        """
        for ticker in list(self.portfolio.keys()):
            if ticker == 'KRW' or ticker in self.manual_holdings:
                continue
            amount = self.portfolio.get(ticker, 0)
            if amount > 0:
                df = all_price_data.get(ticker, pd.DataFrame())
                if df.empty or date_str not in df.index:
                    price = 0  # 데이터 없으면 가격을 0으로 가정
                else:
                    price = df.loc[date_str]['close']
                krw_return = amount * price
                self.portfolio['KRW'] += krw_return
                self.portfolio[ticker] = 0
                self.trade_log.append({
                    'date': date_str,
                    'action': 'sell_all',
                    'ticker': ticker,
                    'amount': amount,
                    'price': price,
                    'total': krw_return
                })
                self.log(f"{date_str}: {ticker} 전량 매도 - 수익률 반영됨.")

    def execute_trades(self, date_str, top3, all_price_data):
        """
        매매 실행 (매도 및 매수)

        Parameters:
        date_str (str): 매매 날짜 (YYYY-MM-DD)
        top3 (list): 모멘텀 상위 3개 코인 티커 리스트
        all_price_data (dict): 코인별 가격 데이터
        """
        # 현재 포트폴리오에서 보유 중인 코인 리스트
        current_holdings = [ticker for ticker in self.portfolio if
                            ticker.startswith("KRW-") and self.portfolio[ticker] > 0]

        # 매도 대상 파악: 목표에 없는 현재 보유 코인 매도
        for ticker in current_holdings:
            if ticker not in top3:
                amount = self.portfolio.get(ticker, 0)
                df = all_price_data.get(ticker, pd.DataFrame())
                if df.empty or date_str not in df.index:
                    price = 0
                else:
                    price = df.loc[date_str]['close']
                krw_return = amount * price
                self.portfolio['KRW'] += krw_return
                self.portfolio[ticker] = 0
                self.trade_log.append({
                    'date': date_str,
                    'action': 'sell',
                    'ticker': ticker,
                    'amount': amount,
                    'price': price,
                    'total': krw_return
                })
                self.log(f"{date_str}: {ticker} 매도 - 수익률 반영됨.")

        # 매수 대상 파악: 상위 3개 코인에 균등 분배
        krw_balance = self.portfolio.get('KRW', 0)
        if krw_balance > 0 and len(top3) > 0:
            invest_amount = krw_balance / len(top3)
            invest_amount = int(invest_amount / 1000) * 1000  # 1000원 단위
            for ticker in top3:
                if invest_amount < 5000:
                    self.log(f"{date_str}: 투자금액({invest_amount:,.0f}원)이 최소 거래금액(5,000원) 미만입니다. 매수 건너뜀.")
                    continue  # 최소 거래 금액 5,000원
                df = all_price_data.get(ticker, pd.DataFrame())
                if df.empty or date_str not in df.index:
                    price = 0
                else:
                    price = df.loc[date_str]['close']
                amount = invest_amount / price if price > 0 else 0
                if amount > 0:
                    self.portfolio['KRW'] -= invest_amount
                    self.portfolio[ticker] = self.portfolio.get(ticker, 0) + amount
                    self.trade_log.append({
                        'date': date_str,
                        'action': 'buy',
                        'ticker': ticker,
                        'amount': amount,
                        'price': price,
                        'total': invest_amount
                    })
                    self.log(f"{date_str}: {ticker} 매수 - 투자금액: {invest_amount:,.0f}원, 수량: {amount:.6f}")

    def run_backtest(self):
        """
        백테스팅 실행
        """
        # 1. 모든 티커의 과거 가격 데이터 로드
        symbols = self.get_coin_list()
        self.log(f"업비트 상장 코인 수: {len(symbols)}")

        # CoinGecko에서 상위 300위 코인 중 업비트에 상장된 코인 필터링
        # 실제 시가총액 데이터를 CoinGecko에서 가져옴
        coin_market_caps = self.get_market_cap_data(symbols)
        top_coins = list(coin_market_caps.keys())
        self.log(f"시가총액 데이터를 가진 코인 수: {len(top_coins)}")

        # 모든 코인의 가격 데이터 로드
        all_price_data = {}
        for ticker in top_coins:
            coin = ticker.split('-')[1]
            df = self.load_historical_data(ticker, self.start_date - timedelta(days=120), self.end_date)
            if not df.empty:
                # 날짜 형식을 YYYY-MM-DD로 변경
                df.index = df.index.strftime("%Y-%m-%d")
                all_price_data[ticker] = df
            else:
                self.log(f"{ticker}의 가격 데이터가 없습니다.")
            time.sleep(0.2)  # 요청 간 지연 시간 (0.2초)

        # 비트코인 가격 데이터 로드
        df_btc = self.load_historical_data("KRW-BTC", self.start_date - timedelta(days=120), self.end_date)
        if df_btc.empty:
            self.log("BTC의 가격 데이터를 로드할 수 없습니다. 백테스팅을 중단합니다.")
            return
        df_btc.index = df_btc.index.strftime("%Y-%m-%d")
        ma120_series = self.get_btc_ma120(df_btc)

        # 백테스팅 기간 동안의 날짜 순회
        current_date = self.start_date
        while current_date <= self.end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            self.log(f"백테스팅 날짜: {date_str}")

            # BTC 120MA 계산
            ma120 = ma120_series.get(date_str, np.nan)
            btc_price = df_btc.loc[date_str]['close'] if date_str in df_btc.index else np.nan

            if np.isnan(ma120) or np.isnan(btc_price):
                self.log(f"BTC 데이터 부족으로 {date_str}은 스킵합니다.")
                current_date += timedelta(days=1)
                continue

            btc_above_ma = btc_price > ma120

            # BTC가 120MA 아래로 떨어진 경우
            if not btc_above_ma:
                if not self.is_trading_suspended:
                    self.log(f"{date_str}: BTC가 120일 이평선 아래로 떨어져 전체 매도 후 매매를 중지합니다.")
                    self.sell_all(date_str, all_price_data)
                    self.is_trading_suspended = True
                    self.last_rebalance_time = current_date
            else:
                if self.is_trading_suspended:
                    self.log(f"{date_str}: BTC가 120일 이평선 위로 올라와 매매를 재개합니다.")
                    self.is_trading_suspended = False
                    # 초기 포지션 진입
                    # 현재 시가총액 상위 20개 코인 중 모멘텀 상위 3개 매수
                    top20 = self.get_top20_market_cap(date_str, coin_market_caps)
                    top3 = self.get_top3_momentum(date_str, top20, all_price_data)
                    self.execute_trades(date_str, top3, all_price_data)
                    self.last_rebalance_time = current_date
                else:
                    # 리밸런싱 조건 체크
                    time_since_last_rebalance = (current_date - self.last_rebalance_time).total_seconds() / 60  # 분 단위
                    should_rebalance = False

                    # 손실 체크: 포트폴리오 내 모든 코인에 대해 -10% 이상 손실인 경우
                    has_significant_loss = False
                    for ticker in self.portfolio:
                        if ticker == 'KRW' or ticker in self.manual_holdings:
                            continue
                        if self.portfolio[ticker] <= 0:
                            continue
                        df = all_price_data.get(ticker, pd.DataFrame())
                        if df.empty or date_str not in df.index:
                            continue
                        price = df.loc[date_str]['close']
                        # 평균 매수 가격을 추적하지 않았으므로 단순히 현재 가격이 매수 가격 대비 -10% 이하인 경우로 가정
                        # 실제 전략에서는 평균 매수 가격을 저장하고 비교해야 함
                        # 여기서는 간단히 7일 수익률이 -10% 이하인 경우로 대체
                        seven_day_return = self.calculate_7day_return(df, current_date)
                        if seven_day_return <= -10:
                            has_significant_loss = True
                            break

                    if has_significant_loss or time_since_last_rebalance >= self.rebalancing_interval:
                        should_rebalance = True

                    if should_rebalance:
                        self.log(f"{date_str}: 리밸런싱 조건 충족 - {'큰 손실 발생' if has_significant_loss else '리밸런싱 주기 경과'}")
                        top20 = self.get_top20_market_cap(date_str, coin_market_caps)
                        top3 = self.get_top3_momentum(date_str, top20, all_price_data)
                        self.execute_trades(date_str, top3, all_price_data)
                        self.last_rebalance_time = current_date

            # 포트폴리오 기록
            portfolio_value = self.get_portfolio_value(date_str, all_price_data)
            self.portfolio_history.append({
                'date': date_str,
                'portfolio_value': portfolio_value
            })

            current_date += timedelta(days=1)

        # 백테스팅 결과 시각화
        self.plot_results()

    def plot_results(self):
        """
        백테스팅 결과 시각화
        """
        df_portfolio = pd.DataFrame(self.portfolio_history)
        df_portfolio['date'] = pd.to_datetime(df_portfolio['date'])
        df_portfolio.set_index('date', inplace=True)

        plt.figure(figsize=(14, 7))
        plt.plot(df_portfolio.index, df_portfolio['portfolio_value'], label='Portfolio Value (KRW)')
        plt.title('백테스팅 포트폴리오 가치 변화')
        plt.xlabel('날짜')
        plt.ylabel('포트폴리오 가치 (KRW)')
        plt.legend()
        plt.grid(True)
        plt.show()

    def get_trade_log(self):
        """
        매매 로그 반환

        Returns:
        DataFrame: 매매 로그
        """
        return pd.DataFrame(self.trade_log)


if __name__ == "__main__":
    # 백테스팅 기간 설정 (예: 2023-01-01부터 2023-12-31까지)
    start_date = '2023-01-01'
    end_date = '2023-12-31'

    # 백테스팅 클래스 초기화
    backtest = UpbitMomentumBacktest(start_date, end_date, config_path='config.json')

    # 백테스팅 실행
    backtest.run_backtest()

    # 매매 로그 출력
    trade_log = backtest.get_trade_log()
    print(trade_log)

    # 포트폴리오 그래프 시각화 (이미 run_backtest에서 호출됨)

##

