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
def get_top_200_coins():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "krw",  # 원화 기준
        "order": "market_cap_desc",  # 시가총액 내림차순 정렬
        "per_page": 200,  # 한 번에 100개의 코인 정보 가져오기
        "page": 1  # 첫 번째 페이지
    }
    response = requests.get(url, params=params)
    return response.json()

# 데이터 출력
top_100_coins = get_top_200_coins()
for coin in top_100_coins:
    print(f"{coin['name']} ({coin['symbol']}),", end=' ')
## 업비트 모든 티커 가져오기
import pyupbit

tickers = pyupbit.get_tickers(fiat="KRW")
symbols = [ticker.split('-')[1] for ticker in tickers]

# 디버깅용 출력
print(f"📝 업비트 상장 코인 수: {len(symbols)}개")
print(f"심볼 목록: {', '.join(symbols)}")




##

