# Upbit Momentum Trading Bot

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Upbit API](https://img.shields.io/badge/Upbit-API-green.svg)](https://docs.upbit.com/)
[![CoinGecko API](https://img.shields.io/badge/CoinGecko-API-yellow.svg)](https://www.coingecko.com/en/api)

자동화된 암호화폐 모멘텀 트레이딩 봇으로, 업비트 거래소에서 시가총액과 모멘텀 기반의 알고리즘 트레이딩을 수행합니다.

## 주요 기능

- BTC 120일 이동평균선 기반의 리스크 관리
- CoinGecko API 기반 실시간 시가총액 순위 추적
- 7일 모멘텀 기반 상위 3개 코인 자동 매매
- 텔레그램 실시간 알림 시스템
- 동적 리밸런싱 (손실률, 보유기간 기반)

## 전략 개요

1. **매수 조건**
   - BTC가 120일 이동평균선 상단
   - 시가총액 상위 20개 중 7일 수익률 상위 3개 선정
   - 최대 2주 보유, 3회 연속 보유 제한

2. **매도 조건**
   - BTC가 120일 이동평균선 하단 시 전량 매도
   - 개별 코인 -10% 손실 시 리밸런싱
   - 보유 기간 2주 초과 시 매도

## 설정

`config.json` 파일 구성:
```json
{
    "upbit": {
        "access_key": "YOUR_ACCESS_KEY",
        "secret_key": "YOUR_SECRET_KEY"
    },
    "telegram": {
        "bot_token": "YOUR_BOT_TOKEN",
        "channel_id": "YOUR_CHANNEL_ID"
    },
    "trading": {
        "manual_holdings": ["BTC", "MANA"],
        "exclude_coins": ["USDT", "USDC", "XRP", "FIL", "TRX", "LTC"],
        "max_slots": 3,
        "rebalancing_interval": 10080
    }
}
```

## 실행 방법

```bash
python momentum_bot.py
```

## 주의사항

### 제한사항
- 업비트 거래소 상장 코인으로 제한됨
- 일부 시가총액 상위 코인(LTC 등)이 업비트 미상장으로 제외
- CoinGecko API 호출 제한 존재

### 리스크
- 급격한 시장 변동 시 슬리피지 발생 가능
- 거래량이 적은 코인의 경우 진입/청산 시 가격 영향 가능
- API 오류나 네트워크 지연으로 인한 주문 실패 가능성

## 기여하기

버그 리포트와 개선 제안은 Issue를 통해 제출해 주세요.

## 라이선스

MIT License

## 면책조항

이 프로그램은 투자 손실에 대한 책임을 지지 않습니다. 실제 거래에 사용 시 충분한 테스트와 리스크 관리가 필요합니다.
