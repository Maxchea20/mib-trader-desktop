"""MEXC Futures market-data client (REST + ticker + latest candle sync).

Uses explicit Google DNS resolution and IP-forced HTTPS connections because
the normal DNS route to contract.mexc.com times out on this machine.
TLS/SNI still uses contract.mexc.com.
"""

import http.client
import json
import socket
import time
from typing import List, Dict, Optional

import dns.resolver

from ..config import TF_MEXC, TF_SECONDS


BASE = "https://contract.mexc.com/api/v1/contract"
MEXC_HOST = "contract.mexc.com"

DNS_SERVERS = [
    "8.8.8.8",
    "8.8.4.4",
]

REQUEST_TIMEOUT = 15


class MexcError(Exception):
    pass


def resolve_mexc_ips() -> List[str]:
    resolver = dns.resolver.Resolver()
    resolver.nameservers = DNS_SERVERS

    answers = resolver.resolve(MEXC_HOST, "A")

    return [str(answer) for answer in answers]


class MexcHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that connects to a specific IP while preserving TLS SNI."""

    def __init__(
        self,
        ip: str,
        hostname: str,
        timeout: int = REQUEST_TIMEOUT,
    ):
        super().__init__(
            hostname,
            timeout=timeout,
        )

        self.mexc_ip = ip
        self.mexc_hostname = hostname

    def connect(self):
        self.sock = socket.create_connection(
            (
                self.mexc_ip,
                self.port,
            ),
            self.timeout,
        )

        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self.mexc_hostname,
        )


def rest_get(path: str) -> dict:
    ips = resolve_mexc_ips()
    last_error = None

    for ip in ips:
        connection = None

        try:
            print(
                f"MEXC REST connecting to {MEXC_HOST} via {ip}",
                flush=True,
            )

            connection = MexcHTTPSConnection(
                ip=ip,
                hostname=MEXC_HOST,
                timeout=REQUEST_TIMEOUT,
            )

            connection.request(
                "GET",
                path,
                headers={
                    "Host": MEXC_HOST,
                    "User-Agent": "MIB-Trader/1.0",
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )

            response = connection.getresponse()
            body = response.read()

            if response.status != 200:
                raise MexcError(
                    f"HTTP {response.status}: "
                    f"{body.decode('utf-8', errors='replace')}"
                )

            return json.loads(
                body.decode("utf-8")
            )

        except Exception as e:
            last_error = e

            print(
                f"MEXC REST via {ip} failed: {e}",
                flush=True,
            )

        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    raise MexcError(
        f"Unable to reach MEXC REST API: {last_error}"
    )


async def ping() -> bool:
    try:
        data = rest_get("/api/v1/contract/ping")
        return data.get("success", False)

    except Exception:
        return False


async def get_ticker(symbol: str) -> Optional[Dict]:
    try:
        data = rest_get(
            f"/api/v1/contract/ticker?symbol={symbol}"
        )

        if not data.get("success"):
            return None

        d = data["data"]

        return {
            "symbol": d["symbol"],
            "last": float(d["lastPrice"]),
            "bid": float(
                d.get(
                    "bid1",
                    d["lastPrice"],
                )
            ),
            "ask": float(
                d.get(
                    "ask1",
                    d["lastPrice"],
                )
            ),
            "high24": float(
                d.get(
                    "high24Price",
                    0,
                )
            ),
            "low24": float(
                d.get(
                    "lower24Price",
                    0,
                )
            ),
            "volume24": float(
                d.get(
                    "volume24",
                    0,
                )
            ),
            "amount24": float(
                d.get(
                    "amount24",
                    0,
                )
            ),
            "change_rate": float(
                d.get(
                    "riseFallRate",
                    0,
                )
            ),
            "change_value": float(
                d.get(
                    "riseFallValue",
                    0,
                )
            ),
            "funding_rate": float(
                d.get(
                    "fundingRate",
                    0,
                )
            ),
            "index_price": float(
                d.get(
                    "indexPrice",
                    d["lastPrice"],
                )
            ),
            "ts": int(
                d.get(
                    "timestamp",
                    int(time.time() * 1000),
                )
            ),
        }

    except MexcError:
        raise

    except Exception as e:
        raise MexcError(str(e))


def _parse_kline(data: Dict) -> List[Dict]:
    t = data.get("time", [])
    o = data.get("open", [])
    c = data.get("close", [])
    h = data.get("high", [])
    low = data.get("low", [])
    vol = data.get(
        "vol",
        data.get("volume", []),
    )

    out = []

    for i in range(len(t)):
        try:
            out.append(
                {
                    "ts": int(t[i]),
                    "open": float(o[i]),
                    "high": float(h[i]),
                    "low": float(low[i]),
                    "close": float(c[i]),
                    "volume": (
                        float(vol[i])
                        if i < len(vol)
                        else 0.0
                    ),
                }
            )

        except (
            IndexError,
            ValueError,
            TypeError,
        ):
            continue

    out.sort(
        key=lambda x: x["ts"]
    )

    return out


async def get_klines(
    symbol: str,
    timeframe: str,
    limit: int = 1000,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> List[Dict]:

    interval = TF_MEXC[timeframe]
    step = TF_SECONDS[timeframe]

    if end is None:
        end = int(time.time())

    if start is None:
        start = end - step * (limit + 2)

    path = (
        f"/api/v1/contract/kline/{symbol}"
        f"?interval={interval}"
        f"&start={start}"
        f"&end={end}"
    )

    try:
        data = rest_get(path)

        if not data.get("success"):
            raise MexcError(
                f"kline not success: "
                f"{data.get('code')}"
            )

        return _parse_kline(
            data["data"]
        )

    except MexcError:
        raise

    except Exception as e:
        raise MexcError(str(e))