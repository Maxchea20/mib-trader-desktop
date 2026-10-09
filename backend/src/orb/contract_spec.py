"""MEXC BTC_USDT perpetual constraints used by the ORB engine.

Captured from the public contract detail endpoint on 2026-10-09:

    GET https://contract.mexc.com/api/v1/contract/detail?symbol=BTC_USDT

The historical database does not store contract specs. This snapshot is what
the existing adapter would read live (`contractSize`, `volUnit`, `minVol`,
`maxVol`, `priceUnit`, `priceScale`, leverage tiers, taker fee). It is not a
substitute for a live read at order time. The live gateway still expects the
caller to pass the spec it intends to trade.

Fee treatment: entries are marketable, so the taker fee is charged on entry
and on exit. The snapshot's maker fee is 0 and is not used. Attached
stopLossPrice / takeProfitPrice on the existing private client are treated as
taker exits because the adapter does not submit them as resting maker limits.
"""

from __future__ import annotations

from dataclasses import dataclass

SPEC_AS_OF = "2026-10-09T11:38:00Z"
SPEC_SOURCE = "https://contract.mexc.com/api/v1/contract/detail?symbol=BTC_USDT"

# (max volume in contracts, max leverage at that tier). Level 1 is first.
_TIERS = (
    (120_000, 500),
    (310_000, 200),
    (480_000, 100),
    (2_800_000, 50),
    (17_500_000, 20),
    (19_000_000, 10),
)


@dataclass(frozen=True)
class ContractSpec:
    symbol: str = "BTC_USDT"
    contract_size: float = 0.0001
    price_unit: float = 0.1
    price_scale: int = 1
    vol_unit: float = 1.0
    vol_scale: int = 0
    min_vol: float = 1.0
    max_vol: float = 400_000.0
    min_leverage: float = 1.0
    max_leverage: float = 500.0
    taker_fee_rate: float = 0.0002
    maker_fee_rate: float = 0.0
    maintenance_margin_rate: float = 0.001
    liquidation_fee_rate: float = 0.0004
    isolated: bool = True
    order_type_limit: int = 1
    order_type_market: int = 5
    open_type_isolated: int = 1
    side_open_long: int = 1
    side_open_short: int = 3
    as_of: str = SPEC_AS_OF
    source: str = SPEC_SOURCE

    def max_leverage_for_qty(self, qty: float) -> float:
        for max_vol, lev in _TIERS:
            if qty <= max_vol:
                return float(lev)
        return float(_TIERS[-1][1])

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "contract_size": self.contract_size,
            "price_unit": self.price_unit,
            "price_scale": self.price_scale,
            "vol_unit": self.vol_unit,
            "vol_scale": self.vol_scale,
            "min_vol": self.min_vol,
            "max_vol": self.max_vol,
            "min_leverage": self.min_leverage,
            "max_leverage": self.max_leverage,
            "taker_fee_rate": self.taker_fee_rate,
            "maker_fee_rate": self.maker_fee_rate,
            "maintenance_margin_rate": self.maintenance_margin_rate,
            "liquidation_fee_rate": self.liquidation_fee_rate,
            "isolated": self.isolated,
            "risk_tiers": [{"max_vol": v, "max_leverage": lev} for v, lev in _TIERS],
            "as_of": self.as_of,
            "source": self.source,
            "adapter_order_types": {
                "limit": self.order_type_limit,
                "market": self.order_type_market,
            },
            "adapter_gap": (
                "mexc_private.submit_order supports market and limit with attached "
                "stopLossPrice/takeProfitPrice. It does not implement MEXC plan orders, "
                "so a resting intrabar stop-entry is not live-wired."
            ),
        }


BTC_USDT = ContractSpec()
