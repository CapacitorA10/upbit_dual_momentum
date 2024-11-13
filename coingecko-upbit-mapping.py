import requests

# CoinGecko에서 시가총액 데이터 가져오기
def get_market_cap(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "krw",
        "ids": coin_id
    }
    response = requests.get(url, params=params)
    data = response.json()
    if data:
        return data[0]['market_cap']
    return None

# Upbit 심볼과 CoinGecko ID 매핑
upbit_to_coingecko = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    # 추가적인 코인들을 여기에 매핑
}

# BTC의 시가총액 가져오기 (Upbit 심볼을 사용)
upbit_symbol = "BTC"
coingecko_id = upbit_to_coingecko.get(upbit_symbol)
if coingecko_id:
    market_cap = get_market_cap(coingecko_id)
    print(f"{upbit_symbol} (CoinGecko ID: {coingecko_id})의 시가총액: {market_cap} KRW")
else:
    print(f"{upbit_symbol}에 대한 CoinGecko ID를 찾을 수 없습니다.")
## 상위 100개 암호화폐 시가총액 데이터 가져오기 (코인게코)
def get_top_300_coins():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "krw",  # 원화 기준
        "order": "market_cap_desc",  # 시가총액 내림차순 정렬
        "per_page": 300,  # 한 번에 100개의 코인 정보 가져오기
        "page": 1  # 첫 번째 페이지
    }
    response = requests.get(url, params=params)
    return response.json()

# 데이터 출력
top_300_coins = get_top_300_coins()
for coin in top_300_coins:
    print(f"{coin['name']} ({coin['symbol']}),", end=' ')
## 업비트 모든 티커 가져오기
import pyupbit

tickers = pyupbit.get_tickers(fiat="KRW")
symbols = [ticker.split('-')[1] for ticker in tickers]

# 디버깅용 출력
print(f"📝 업비트 상장 코인 수: {len(symbols)}개")
print(f"심볼 목록: {', '.join(symbols)}")




## 코인게코 - 업비트 연동하기

import pyupbit
import requests

# 업비트 상장 코인 목록 가져오기 (USD 마켓)
tickers = pyupbit.get_tickers(fiat="KRW")
symbols = [ticker.split('-')[1] for ticker in tickers]

# CoinGecko에서 상위 300위 코인 목록 가져오기 (USD 기준)
def get_top_300_coins():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",  # USD 기준
        "order": "market_cap_desc",  # 시가총액 내림차순 정렬
        "per_page": 300,  # 한 번에 300개의 코인 정보 가져오기
        "page": 1  # 첫 번째 페이지
    }
    response = requests.get(url, params=params)
    response.raise_for_status()  # 요청이 성공했는지 확인
    return response.json()

top_300_coins = get_top_300_coins()

# CoinGecko 심볼을 기준으로 코인 데이터 매핑
coin_gecko_symbol_map = {coin['symbol'].lower(): coin for coin in top_300_coins}

# 업비트 코인들의 시가총액 가져오기
market_caps = {}
for symbol in symbols:
    symbol_lower = symbol.lower()
    if symbol_lower in coin_gecko_symbol_map:
        coin_data = coin_gecko_symbol_map[symbol_lower]
        market_cap = coin_data['market_cap']
        # 시가총액을 억 달러 단위로 변환
        market_cap_eok = market_cap / 100_000_000  # 1억 달러 = 100,000,000 USD
        market_caps[symbol] = market_cap_eok
    else:
        # CoinGecko 상위 300위에 없는 코인은 제외
        pass

# 결과 출력
print("\n📊 업비트 상장 코인들의 시가총액 (억 달러 기준):")
for symbol, market_cap_eok in market_caps.items():
    print(f"{symbol}: {market_cap_eok:,.2f} 억 달러")

##

