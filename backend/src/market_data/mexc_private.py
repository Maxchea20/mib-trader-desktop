"""MEXC Futures PRIVATE REST client — account, positions, order submit/cancel.

Mirrors the DNS-forced HTTPS pattern in mexc_market_data.py (normal DNS to
contract.mexc.com times out on this machine).

Read calls cache briefly and fall back to the last good payload for ~30s so a
single timeout does not flip the Live panel to disconnected.
Writes (submit/cancel/leverage) are never served from cache.
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
REQUEST_TIMEOUT = 12
DNS_TTL = 120.0
ASSETS_TTL = 4.0
ASSETS_STALE_TTL = 30.0
POSITIONS_TTL = 3.0
POSITIONS_STALE_TTL = 20.0
HISTORY_TTL = 8.0

_IP_CACHE = {"ips": [], "at": 0.0}
_ASSETS_CACHE = {"data": None, "at": 0.0}
_POS_CACHE = {}
_HIST_CACHE = {"data": None, "at": 0.0, "key": None}


class MexcPrivateError(Exception):
    pass


class MexcPermissionError(MexcPrivateError):
    """Raised for MEXC error codes 701-704 — API key lacks the needed permission."""
    pass


def _resolve_ips() -> List[str]:
    now = time.time()
    if _IP_CACHE["ips"] and (now - _IP_CACHE["at"]) < DNS_TTL:
        return list(_IP_CACHE["ips"])
    resolver = dns.resolver.Resolver()
    resolver.nameservers = DNS_SERVERS
    resolver.lifetime = 3.0
    try:
        ips = [str(a) for a in resolver.resolve(MEXC_HOST, "A")]
        if ips:
            _IP_CACHE["ips"] = ips
            _IP_CACHE["at"] = now
            return ips
    except Exception:
        if _IP_CACHE["ips"]:
            return list(_IP_CACHE["ips"])
        raise
    if _IP_CACHE["ips"]:
        return list(_IP_CACHE["ips"])
    raise MexcPrivateError("no IPs for contract.mexc.com")


class _MexcHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, ip: str, hostname: str, timeout: int = REQUEST_TIMEOUT):
        super().__init__(hostname, timeout=timeout)
        self.mexc_ip = ip
        self.mexc_hostname = hostname

    def connect(self):
        self.sock = socket.create_connection((self.mexc_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.mexc_hostname)


def _sorted_param_string(params: Dict) -> str:
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
            "Packaged desktop: tray → Show Data Folder → create .env there. "
            "Dev: put them in backend/.env. Then Restart Trading Engine."
        )
    return api_key, secret_key


def keys_present() -> bool:
    return bool(os.environ.get("MEXC_API_KEY")) and bool(os.environ.get("MEXC_API_SECRET"))


def _json_num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        return float(f"{v:.10f}".rstrip("0").rstrip(".") or "0")
    return v


def _request(method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None) -> dict:
    api_key, secret_key = _get_keys()
    req_time = str(int(time.time() * 1000))

    query = ""
    if method in ("GET", "DELETE"):
        param_str = _sorted_param_string(params or {})
        query = f"?{param_str}" if param_str else ""
        signature = _sign(secret_key, api_key, req_time, param_str)
        body_bytes = None
    else:
        clean = {}
        for k, v in (body or {}).items():
            if v is None:
                continue
            clean[k] = _json_num(v) if isinstance(v, (int, float)) else v
        body_json = json.dumps(clean, separators=(",", ":"))
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


def get_assets() -> List[Dict]:
    now = time.time()
    if _ASSETS_CACHE["data"] is not None and (now - _ASSETS_CACHE["at"]) < ASSETS_TTL:
        return _ASSETS_CACHE["data"]
    try:
        data = _request("GET", "/account/assets").get("data", []) or []
        _ASSETS_CACHE["data"] = data
        _ASSETS_CACHE["at"] = now
        return data
    except Exception:
        if _ASSETS_CACHE["data"] is not None and (now - _ASSETS_CACHE["at"]) < ASSETS_STALE_TTL:
            return _ASSETS_CACHE["data"]
        raise


def get_asset(currency: str = "USDT") -> Dict:
    return _request("GET", f"/account/asset/{currency}").get("data", {})


def get_open_positions(symbol: Optional[str] = None) -> List[Dict]:
    key = symbol or "*"
    now = time.time()
    hit = _POS_CACHE.get(key)
    if hit and (now - hit["at"]) < POSITIONS_TTL:
        return hit["data"]
    params = {"symbol": symbol} if symbol else {}
    try:
        data = _request("GET", "/position/open_positions", params=params).get("data", []) or []
        _POS_CACHE[key] = {"data": data, "at": now}
        return data
    except Exception:
        if hit and (now - hit["at"]) < POSITIONS_STALE_TTL:
            return hit["data"]
        raise


def get_history_positions(symbol: Optional[str] = None, page_num: int = 1, page_size: int = 50) -> List[Dict]:
    """GET /api/v1/private/position/list/history_positions — closed tickets."""
    key = f"{symbol or '*'}:{page_num}:{page_size}"
    now = time.time()
    if _HIST_CACHE["data"] is not None and _HIST_CACHE["key"] == key and (now - _HIST_CACHE["at"]) < HISTORY_TTL:
        return _HIST_CACHE["data"]
    params = {"page_num": page_num, "page_size": page_size}
    if symbol:
        params["symbol"] = symbol
    raw = _request("GET", "/position/list/history_positions", params=params).get("data")
    if isinstance(raw, dict):
        rows = raw.get("resultList") or raw.get("result") or raw.get("data") or []
    else:
        rows = raw or []
    _HIST_CACHE["data"] = rows
    _HIST_CACHE["at"] = now
    _HIST_CACHE["key"] = key
    return rows


def get_open_orders(symbol: str, page_num: int = 1, page_size: int = 20) -> List[Dict]:
    return _request(
        "GET", f"/order/list/open_orders/{symbol}",
        params={"page_num": page_num, "page_size": page_size},
    ).get("data", [])


SIDE_OPEN_LONG = 1
SIDE_CLOSE_SHORT = 2
SIDE_OPEN_SHORT = 3
SIDE_CLOSE_LONG = 4

OPEN_TYPE_ISOLATED = 1
OPEN_TYPE_CROSS = 2

ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 5

POSITION_TYPE_LONG = 1
POSITION_TYPE_SHORT = 2


def change_leverage(
    symbol: str,
    leverage: int,
    position_type: int,
    open_type: int = OPEN_TYPE_ISOLATED,
) -> Dict:
    return _request("POST", "/position/change_leverage", body={
        "symbol": symbol,
        "leverage": int(leverage),
        "openType": int(open_type),
        "positionType": int(position_type),
    })


def submit_order(
    symbol: str,
    side: int,
    vol: float,
    price: Optional[float] = None,
    order_type: int = ORDER_TYPE_MARKET,
    open_type: int = OPEN_TYPE_ISOLATED,
    leverage: Optional[int] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    external_oid: Optional[str] = None,
) -> Dict:
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
        body["leverage"] = int(leverage)
    if stop_loss_price is not None:
        body["stopLossPrice"] = stop_loss_price
    if take_profit_price is not None:
        body["takeProfitPrice"] = take_profit_price
    if external_oid is not None:
        body["externalOid"] = external_oid
    return _request("POST", "/order/submit", body=body)


def close_position(
    symbol: str,
    opened_side: str,
    vol: float,
    price: Optional[float] = None,
    open_type: int = OPEN_TYPE_ISOLATED,
) -> Dict:
    close_side = SIDE_CLOSE_LONG if str(opened_side).upper() == "LONG" else SIDE_CLOSE_SHORT
    return submit_order(
        symbol=symbol,
        side=close_side,
        vol=vol,
        price=price,
        order_type=ORDER_TYPE_MARKET,
        open_type=open_type,
    )


def cancel_orders(order_ids: List[str]) -> Dict:
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
