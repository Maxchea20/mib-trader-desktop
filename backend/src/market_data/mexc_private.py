"""MEXC Futures PRIVATE REST client — account, positions, order submit/cancel.

Mirrors the DNS-forced HTTPS pattern in mexc_market_data.py (normal DNS to
contract.mexc.com times out on this machine).

Auth (per https://mexcdevelop.github.io/apidocs/contract_v1_en/#authentication-method):
  Headers: ApiKey, Request-Time (ms), Signature, Content-Type: application/json
  signStr = accessKey + requestTimeMs + paramString
    - GET/DELETE: paramString = sorted "key=value&key2=value2" (dict order, url-encoded values)
    - POST:       paramString = the exact JSON string sent as the body
  Signature = HMAC-SHA256(secretKey, signStr) as lowercase hex (NOT base64).

IMPORTANT — read before relying on this:
  MEXC's own contract-v1 docs list the order submit/cancel endpoints under a
  section titled "(Under maintenance)". In practice this usually means order
  placement needs futures-trading permission explicitly enabled on the API
  key (separate from read permission), not that the endpoint is dead. The
  first call you make should be `get_assets()` (read-only, always safe) to
  confirm the signature is correct, THEN a tiny `submit_order` to confirm
  trading permission is actually granted before trusting anything bigger.
  Error codes 701-704 in the response mean a permission problem, not a bug
  in this signing code.
"""

import hashlib
import hmac
import http.client
import json
import os
import socket
import time
from typing import Dict, List, Optional

import dns.resolver

MEXC_HOST = "contract.mexc.com"
DNS_SERVERS = ["8.8.8.8", "8.8.4.4"]
REQUEST_TIMEOUT = 15


class MexcPrivateError(Exception):
    pass


class MexcPermissionError(MexcPrivateError):
    """Raised for MEXC error codes 701-704 — API key lacks the needed permission."""
    pass


def _resolve_ips() -> List[str]:
    resolver = dns.resolver.Resolver()
    resolver.nameservers = DNS_SERVERS
    return [str(a) for a in resolver.resolve(MEXC_HOST, "A")]


class _MexcHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, ip: str, hostname: str, timeout: int = REQUEST_TIMEOUT):
        super().__init__(hostname, timeout=timeout)
        self.mexc_ip = ip
        self.mexc_hostname = hostname

    def connect(self):
        self.sock = socket.create_connection((self.mexc_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.mexc_hostname)


def _sorted_param_string(params: Dict) -> str:
    """GET/DELETE signing string: dict-order key=value&... with url-encoded values."""
    from urllib.parse import quote
    if not params:
        return ""
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if v is None or v == "":
            continue
        parts.append(f"{k}={quote(str(v), safe='')}")
    return "&".join(parts)


def _sign(secret_key: str, access_key: str, req_time_ms: str, param_str: str) -> str:
    to_sign = f"{access_key}{req_time_ms}{param_str}"
    return hmac.new(secret_key.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_keys() -> tuple:
    api_key = os.environ.get("MEXC_API_KEY")
    secret_key = os.environ.get("MEXC_API_SECRET")
    if not api_key or not secret_key:
        raise MexcPrivateError(
            "MEXC_API_KEY / MEXC_API_SECRET not set in environment. "
            "Create a futures API key at https://www.mexc.com/ucenter/openapi "
            "and set both env vars before calling any private endpoint."
        )
    return api_key, secret_key


def _request(method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None) -> dict:
    """method: GET, POST, or DELETE. params -> query string (GET/DELETE). body -> JSON (POST)."""
    api_key, secret_key = _get_keys()
    req_time = str(int(time.time() * 1000))

    query = ""
    if method in ("GET", "DELETE"):
        param_str = _sorted_param_string(params or {})
        query = f"?{param_str}" if param_str else ""
        signature = _sign(secret_key, api_key, req_time, param_str)
        body_bytes = None
    else:  # POST
        body_json = json.dumps(body or {}, separators=(",", ":"))
        signature = _sign(secret_key, api_key, req_time, body_json)
        body_bytes = body_json.encode("utf-8")

    full_path = f"/api/v1/private{path}{query}"
    headers = {
        "Host": MEXC_HOST,
        "ApiKey": api_key,
        "Request-Time": req_time,
        "Signature": signature,
        "Content-Type": "application/json",
        "User-Agent": "MIB-Trader/1.0",
        "Accept": "application/json",
        "Connection": "close",
    }

    ips = _resolve_ips()
    last_error = None
    for ip in ips:
        conn = None
        try:
            conn = _MexcHTTPSConnection(ip, MEXC_HOST, REQUEST_TIMEOUT)
            conn.request(method, full_path, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise MexcPrivateError(f"HTTP {resp.status}: {raw.decode('utf-8', errors='replace')}")
            data = json.loads(raw.decode("utf-8"))
            if not data.get("success", True) and data.get("code") not in (0, None):
                code = data.get("code")
                msg = data.get("message") or data.get("msg") or ""
                if code in (701, 702, 703, 704):
                    raise MexcPermissionError(f"code {code}: {msg or 'permission denied — check API key trading permission on MEXC'}")
                raise MexcPrivateError(f"code {code}: {msg}")
            return data
        except (MexcPrivateError, MexcPermissionError):
            raise
        except Exception as e:
            last_error = e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise MexcPrivateError(f"Unable to reach MEXC private API: {last_error}")


# ---- Account / positions (read-only — safe to call anytime) ----

def get_assets() -> List[Dict]:
    """GET /api/v1/private/account/assets — all currencies, balances, equity."""
    return _request("GET", "/account/assets").get("data", [])


def get_asset(currency: str = "USDT") -> Dict:
    return _request("GET", f"/account/asset/{currency}").get("data", {})


def get_open_positions(symbol: Optional[str] = None) -> List[Dict]:
    """GET /api/v1/private/position/open_positions"""
    params = {"symbol": symbol} if symbol else {}
    return _request("GET", "/position/open_positions", params=params).get("data", [])


def get_open_orders(symbol: str, page_num: int = 1, page_size: int = 20) -> List[Dict]:
    return _request(
        "GET", f"/order/list/open_orders/{symbol}",
        params={"page_num": page_num, "page_size": page_size},
    ).get("data", [])


# ---- Order submit / cancel (real money — treat every call as live) ----

# side: 1 open long, 2 close short, 3 open short, 4 close long
# openType: 1 isolated, 2 cross
# orderType: 1 limit, 5 market

SIDE_OPEN_LONG = 1
SIDE_CLOSE_SHORT = 2
SIDE_OPEN_SHORT = 3
SIDE_CLOSE_LONG = 4

OPEN_TYPE_ISOLATED = 1
OPEN_TYPE_CROSS = 2

ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 5


def submit_order(
    symbol: str,
    side: int,
    vol: float,
    price: Optional[float] = None,
    order_type: int = ORDER_TYPE_MARKET,
    open_type: int = OPEN_TYPE_CROSS,
    leverage: Optional[int] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    external_oid: Optional[str] = None,
) -> Dict:
    """POST /api/v1/private/order/submit — places a real order. vol is in CONTRACTS
    (not USD notional) — check contractSize on /api/v1/contract/detail before sizing.
    Market orders (order_type=5) still require a `price` field for slippage protection
    on MEXC's side; pass the current mark price."""
    body = {
        "symbol": symbol,
        "vol": vol,
        "side": side,
        "type": order_type,
        "openType": open_type,
    }
    if price is not None:
        body["price"] = price
    if leverage is not None:
        body["leverage"] = leverage
    if stop_loss_price is not None:
        body["stopLossPrice"] = stop_loss_price
    if take_profit_price is not None:
        body["takeProfitPrice"] = take_profit_price
    if external_oid is not None:
        body["externalOid"] = external_oid
    return _request("POST", "/order/submit", body=body)


def cancel_orders(order_ids: List[str]) -> Dict:
    """POST /api/v1/private/order/cancel — body is a JSON array of order id strings."""
    api_key, secret_key = _get_keys()
    req_time = str(int(time.time() * 1000))
    body_json = json.dumps(order_ids, separators=(",", ":"))
    signature = _sign(secret_key, api_key, req_time, body_json)
    headers = {
        "Host": MEXC_HOST, "ApiKey": api_key, "Request-Time": req_time,
        "Signature": signature, "Content-Type": "application/json",
        "User-Agent": "MIB-Trader/1.0", "Accept": "application/json", "Connection": "close",
    }
    ips = _resolve_ips()
    last_error = None
    for ip in ips:
        conn = None
        try:
            conn = _MexcHTTPSConnection(ip, MEXC_HOST, REQUEST_TIMEOUT)
            conn.request("POST", "/api/v1/private/order/cancel", body=body_json.encode("utf-8"), headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise MexcPrivateError(f"HTTP {resp.status}: {raw.decode('utf-8', errors='replace')}")
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            last_error = e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise MexcPrivateError(f"Unable to reach MEXC private API: {last_error}")