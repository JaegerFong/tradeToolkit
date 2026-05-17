"""
AKShare Network & Proxy Manager
参考 VeKiner/akshare-stock-data-fetcher 的代理池方案优化

核心改进:
- 顺序轮转代理池 (而非随机)，确保每个 IP 均匀分担请求
- 代理健康评分，自动淘汰不可用代理
- 支持认证代理 (ip:port:user:password 格式)
- 按域名限速 + 退避重试
- 交易日判断
"""
import logging
import os
import random
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# 抑制 urllib3 重试日志 (每条失败重试都打 WARNING，噪音太大)
# 东财反爬会导致大量 RemoteDisconnected 重试，这是正常现象
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
logging.getLogger("urllib3.util.retry").setLevel(logging.ERROR)

# AKShare 网络层日志级别 (可通过 AKSHARE_NETWORK_LOG_LEVEL 控制)
_NETWORK_LOG_LEVEL = os.getenv("AKSHARE_NETWORK_LOG_LEVEL", "WARNING").upper()
_VERBOSE = _NETWORK_LOG_LEVEL == "DEBUG"

# ── 配置常量 ──────────────────────────────────────────────

_REQUEST_TIMEOUT = float(os.getenv("AKSHARE_REQUEST_TIMEOUT", "20"))
_REQUEST_RETRIES = max(1, int(os.getenv("AKSHARE_REQUEST_RETRIES", "4")))
_PROXY_ROUNDS = max(1, int(os.getenv("AKSHARE_PROXY_ROUNDS", "2")))
_MIN_REQUEST_INTERVAL = float(
    os.getenv("AKSHARE_MIN_REQUEST_INTERVAL", os.getenv("AKSHARE_RATE_LIMIT_DELAY", "0.8"))
)
_USE_CURL_CFFI = os.getenv("AKSHARE_USE_CURL_CFFI", "true").lower() not in {"0", "false", "no"}
_PROXY_API_URL = os.getenv("AKSHARE_PROXY_API_URL", "").strip()
_PROXY_CACHE_SECONDS = float(os.getenv("AKSHARE_PROXY_CACHE_SECONDS", "60"))
_INCLUDE_DIRECT = os.getenv("AKSHARE_PROXY_INCLUDE_DIRECT", "true").lower() not in {"0", "false", "no"}
_PROXY_MODE = os.getenv("AKSHARE_PROXY_MODE", "strong").lower()  # off / basic / strong

# 浏览器 User-Agent 池
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# ── 交易日缓存 ────────────────────────────────────────────

_trading_calendar_cache: Dict[str, bool] = {}
_calendar_cache_date: Optional[str] = None


def is_trading_day(date_obj: Optional[datetime] = None) -> bool:
    """判断是否为 A 股交易日 (简单规则: 周一至周五, 非节假日)

    使用 exchange_calendars 库进行精确判断 (如果已安装),
    否则使用简单规则 (周一至周五).
    """
    if date_obj is None:
        date_obj = datetime.now()

    date_str = date_obj.strftime("%Y-%m-%d")
    if date_str in _trading_calendar_cache:
        return _trading_calendar_cache[date_str]

    # 周末一定不是交易日
    if date_obj.weekday() >= 5:
        _trading_calendar_cache[date_str] = False
        return False

    # 尝试使用 exchange_calendars 精确判断
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XSHG")
        ts = pd.Timestamp(date_str)
        is_trading = cal.is_session(ts)
        _trading_calendar_cache[date_str] = is_trading
        return is_trading
    except ImportError:
        pass

    # 简单判断: 工作日默认是交易日
    _trading_calendar_cache[date_str] = True
    return True


# ── 代理池管理 ────────────────────────────────────────────

class ProxyPool:
    """代理池管理器 - 顺序轮转, 健康评分"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pool: List[Optional[Dict[str, str]]] = []
        self._index: int = 0
        self._scores: Dict[str, int] = {}  # proxy_key -> score (越高越好)
        self._failures: Dict[str, int] = {}  # proxy_key -> 连续失败次数
        self._last_refresh: float = 0
        self._max_consecutive_failures: int = 3

    @staticmethod
    def _normalize_proxy(raw: str) -> Optional[Dict[str, str]]:
        """解析代理字符串, 支持格式:
        - http://ip:port
        - socks5://ip:port
        - ip:port
        - ip:port:user:password
        """
        raw = raw.strip()
        if not raw:
            return None

        # 检查是否已有 scheme
        if raw.startswith(("http://", "https://", "socks5://", "socks5h://")):
            proxy_url = raw
        elif raw.startswith("socks4://"):
            proxy_url = raw
        else:
            # 解析 ip:port[:user:password]
            parts = raw.split(":")
            if len(parts) == 2:
                proxy_url = f"http://{raw}"
            elif len(parts) == 4:
                # ip:port:user:password
                ip, port, user, pwd = parts
                proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
            else:
                logger.warning(f"无法解析代理格式: {raw}")
                return None

        return {"http": proxy_url, "https": proxy_url}

    @staticmethod
    def _proxy_key(proxy: Optional[Dict[str, str]]) -> str:
        if proxy is None:
            return "__direct__"
        return proxy.get("http", "") or proxy.get("https", "")

    def refresh(self, force: bool = False):
        """刷新代理池"""
        now = time.time()
        if not force and now - self._last_refresh < _PROXY_CACHE_SECONDS:
            return

        with self._lock:
            new_pool: List[Optional[Dict[str, str]]] = []

            # 1. 静态代理列表
            static_proxies = os.getenv("AKSHARE_PROXIES", "").strip()
            if static_proxies:
                for item in static_proxies.replace("\n", ",").split(","):
                    proxy = self._normalize_proxy(item)
                    if proxy:
                        new_pool.append(proxy)

            # 2. 动态代理 API (仅在 strong 模式下)
            if _PROXY_MODE == "strong" and _PROXY_API_URL:
                try:
                    resp = requests.get(
                        _PROXY_API_URL,
                        timeout=min(_REQUEST_TIMEOUT, 10),
                        headers={"User-Agent": random.choice(_USER_AGENTS)},
                    )
                    resp.raise_for_status()
                    text = resp.text.strip()
                    items = [
                        item.strip()
                        for item in text.replace("\r", "\n").replace(",", "\n").split("\n")
                        if item.strip()
                    ]
                    for item in items:
                        proxy = self._normalize_proxy(item)
                        if proxy:
                            new_pool.append(proxy)
                    logger.info(f"代理池已刷新: API 返回 {len(items)} 个, 解析成功 {len(new_pool)} 个")
                except Exception as e:
                    logger.warning(f"刷新代理 API 失败: {e}")

            # 3. 直连兜底
            if _INCLUDE_DIRECT or not new_pool:
                new_pool.append(None)

            # 保留旧代理中健康评分较高的
            old_keys = {self._proxy_key(p) for p in self._pool}
            for p in new_pool:
                key = self._proxy_key(p)
                if key in self._scores:
                    continue
                self._scores[key] = 0  # 新代理初始分数为 0

            self._pool = new_pool
            self._index = 0
            self._last_refresh = now

    def next_proxy(self) -> Optional[Dict[str, str]]:
        """获取下一个代理 (顺序轮转, 跳过不健康的)"""
        self.refresh()

        with self._lock:
            if not self._pool:
                return None

            # 尝试从当前索引开始找一个健康的代理
            for _ in range(len(self._pool)):
                proxy = self._pool[self._index]
                self._index = (self._index + 1) % len(self._pool)

                key = self._proxy_key(proxy)
                failures = self._failures.get(key, 0)
                if failures < self._max_consecutive_failures:
                    return proxy

            # 所有代理都失败过, 重置失败计数再试
            logger.warning("所有代理都有失败记录, 重置失败计数")
            self._failures.clear()
            self._scores = {k: max(0, v - 2) for k, v in self._scores.items()}

            proxy = self._pool[self._index]
            self._index = (self._index + 1) % len(self._pool)
            return proxy

    def report_success(self, proxy: Optional[Dict[str, str]]):
        """报告代理成功"""
        key = self._proxy_key(proxy)
        with self._lock:
            self._scores[key] = self._scores.get(key, 0) + 1
            self._failures[key] = 0

    def report_failure(self, proxy: Optional[Dict[str, str]]):
        """报告代理失败"""
        key = self._proxy_key(proxy)
        with self._lock:
            self._scores[key] = self._scores.get(key, 0) - 1
            self._failures[key] = self._failures.get(key, 0) + 1

    @property
    def pool_size(self) -> int:
        return len(self._pool)

    def get_stats(self) -> Dict:
        """获取代理池统计"""
        with self._lock:
            return {
                "pool_size": len(self._pool),
                "current_index": self._index,
                "scores": dict(self._scores),
                "failures": dict(self._failures),
            }


# ── 全局代理池单例 ────────────────────────────────────────

_proxy_pool = ProxyPool()


def get_proxy_pool() -> ProxyPool:
    return _proxy_pool


# ── 请求会话管理 ──────────────────────────────────────────

class AKShareSessionManager:
    """管理带重试和代理的 requests.Session"""

    def __init__(self):
        self._last_request_time: Dict[str, float] = {}  # domain -> last request timestamp

    def _build_headers(self, existing: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构建浏览器风格的请求头"""
        headers = dict(existing or {})
        headers.setdefault("User-Agent", random.choice(_USER_AGENTS))
        headers.setdefault("Accept", "application/json,text/plain,*/*")
        headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        headers.setdefault("Referer", "https://quote.eastmoney.com/")
        headers["Connection"] = "close"
        return headers

    def _rate_limit(self, domain: str):
        """按域名限速"""
        now = time.time()
        last = self._last_request_time.get(domain, 0)
        elapsed = now - last
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time[domain] = time.time()

    def request(
        self,
        url: str,
        method: str = "GET",
        use_proxy: bool = True,
        **kwargs,
    ) -> requests.Response:
        """发送请求, 自动处理代理轮转和重试

        Args:
            url: 请求 URL
            method: HTTP 方法
            use_proxy: 是否使用代理 (非 eastmoney 域名可关闭)

        Returns:
            requests.Response

        Raises:
            RuntimeError: 所有代理和重试均失败
        """
        domain = urlparse(url).hostname or "unknown"
        is_eastmoney = "eastmoney.com" in domain

        # 仅对 eastmoney 限速
        if is_eastmoney:
            self._rate_limit(domain)

        # 仅对 eastmoney 使用代理
        should_use_proxy = use_proxy and is_eastmoney

        # 重试配置
        retry = Retry(
            total=_REQUEST_RETRIES - 1,
            connect=_REQUEST_RETRIES - 1,
            read=_REQUEST_RETRIES - 1,
            status=_REQUEST_RETRIES - 1,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )

        headers = self._build_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", _REQUEST_TIMEOUT)

        # 对于非 eastmoney 域名或无代理模式, 直接请求
        if not should_use_proxy or _PROXY_MODE == "off":
            session = requests.Session()
            session.trust_env = False
            session.mount("http://", HTTPAdapter(max_retries=retry))
            session.mount("https://", HTTPAdapter(max_retries=retry))
            try:
                resp = session.request(
                    method, url, headers=headers, timeout=timeout, **kwargs
                )
                return resp
            finally:
                session.close()

        # 代理轮转请求
        last_error = None
        pool = get_proxy_pool()

        for round_idx in range(_PROXY_ROUNDS):
            for _ in range(pool.pool_size):
                proxy = pool.next_proxy()
                if proxy is None:
                    # 没有代理可用, 直连
                    proxy = None

                session = requests.Session()
                session.trust_env = False
                session.mount("http://", HTTPAdapter(max_retries=retry))
                session.mount("https://", HTTPAdapter(max_retries=retry))

                req_kwargs = {"headers": headers, "timeout": timeout, **kwargs}
                if proxy:
                    req_kwargs["proxies"] = proxy

                try:
                    resp = session.request(method, url, **req_kwargs)
                    if resp.status_code in {403, 429, 500, 502, 503, 504}:
                        raise requests.HTTPError(
                            f"HTTP {resp.status_code}: {url}", response=resp
                        )
                    pool.report_success(proxy)
                    return resp
                except requests.ConnectionError as e:
                    last_error = e
                    pool.report_failure(proxy)
                    proxy_label = (
                        "直连" if proxy is None else proxy.get("http", "")[:50]
                    )
                    logger.debug(
                        f"AKShare 连接失败 [{proxy_label}] r={round_idx + 1}: {e}"
                    )
                except requests.Timeout as e:
                    last_error = e
                    pool.report_failure(proxy)
                    proxy_label = (
                        "直连" if proxy is None else proxy.get("http", "")[:50]
                    )
                    logger.debug(
                        f"AKShare 超时 [{proxy_label}] r={round_idx + 1}: {e}"
                    )
                except requests.HTTPError as e:
                    last_error = e
                    pool.report_failure(proxy)
                    proxy_label = (
                        "直连" if proxy is None else proxy.get("http", "")[:50]
                    )
                    status = (
                        e.response.status_code if hasattr(e, "response") and e.response else "?"
                    )
                    logger.debug(
                        f"AKShare HTTP {status} [{proxy_label}] r={round_idx + 1}"
                    )
                except Exception as e:
                    last_error = e
                    pool.report_failure(proxy)
                    proxy_label = (
                        "直连" if proxy is None else proxy.get("http", "")[:50]
                    )
                    logger.debug(
                        f"AKShare 请求异常 [{proxy_label}] r={round_idx + 1}: {e}"
                    )
                finally:
                    session.close()

        raise last_error if last_error else RuntimeError(f"AKShare 请求失败: {url}")


# ── 全局会话管理器 ────────────────────────────────────────

_session_manager = AKShareSessionManager()


def get_session_manager() -> AKShareSessionManager:
    return _session_manager


# ── curl_cffi 支持 ────────────────────────────────────────

_curl_cffi_available = False
try:
    from curl_cffi import requests as curl_requests

    _curl_cffi_available = True
except ImportError:
    pass


def _try_curl_cffi(url: str, **kwargs) -> Optional[requests.Response]:
    """尝试使用 curl_cffi 发送请求 (模拟 Chrome 120 TLS 指纹)"""
    if not _curl_cffi_available or not _USE_CURL_CFFI:
        return None

    try:
        curl_kwargs = {
            "timeout": kwargs.get("timeout", _REQUEST_TIMEOUT),
            "impersonate": "chrome120",
        }
        if "params" in kwargs:
            curl_kwargs["params"] = kwargs["params"]
        if "data" in kwargs:
            curl_kwargs["data"] = kwargs["data"]
        if "json" in kwargs:
            curl_kwargs["json"] = kwargs["json"]

        response = curl_requests.get(url, **curl_kwargs)
        return response
    except Exception as e:
        error_msg = str(e)
        if "invalid library" not in error_msg and "400" not in error_msg:
            logger.debug(f"curl_cffi 请求失败, 回退到标准 requests: {e}")
        return None


# ── requests.get 猴子补丁 ─────────────────────────────────

_patch_applied = False
_original_get = None


def patch_akshare_requests():
    """替换 requests.get, 注入代理轮转和浏览器指纹

    参考 VeKiner/akshare-stock-data-fetcher 的 monkey-patch 方案:
    - 仅对 eastmoney.com 域名启用代理轮转
    - 优先尝试 curl_cffi (如果可用)
    - 失败时自动回退到标准 requests + 代理轮转
    - 对非 eastmoney 请求透明直通

    该补丁是幂等的: 多次调用不会重复应用.
    """
    global _patch_applied, _original_get

    if _patch_applied:
        return

    _original_get = requests.get

    def patched_get(url, **kwargs):
        domain = urlparse(url).hostname or ""
        is_eastmoney = "eastmoney.com" in domain

        # 非 eastmoney 请求: 仅添加浏览器头, 不代理
        if not is_eastmoney:
            mgr = get_session_manager()
            headers = mgr._build_headers(kwargs.get("headers"))
            kwargs["headers"] = headers
            kwargs["timeout"] = kwargs.get("timeout", _REQUEST_TIMEOUT)
            return _original_get(url, **kwargs)

        # eastmoney 请求: curl_cffi 优先
        if _USE_CURL_CFFI and _curl_cffi_available:
            response = _try_curl_cffi(url, **kwargs)
            if response is not None:
                return response

        # 回退到标准 requests + 代理轮转
        mgr = get_session_manager()
        return mgr.request(url, "GET", use_proxy=True, **kwargs)

    requests.get = patched_get
    requests._akshare_original_get = _original_get
    requests._akshare_headers_patched = True
    _patch_applied = True

    mode_labels = {"off": "直连模式", "basic": "基础代理模式", "strong": "强代理模式"}
    mode_label = mode_labels.get(_PROXY_MODE, _PROXY_MODE)
    curl_label = "curl_cffi + " if (_USE_CURL_CFFI and _curl_cffi_available) else ""
    logger.info(
        f"AKShare 网络层已初始化: {curl_label}{mode_label}, "
        f"代理池大小={_proxy_pool.pool_size}, "
        f"超时={_REQUEST_TIMEOUT}s, 重试={_REQUEST_RETRIES}次"
    )


def unpatch_akshare_requests():
    """恢复原始 requests.get"""
    global _patch_applied, _original_get
    if _patch_applied and _original_get is not None:
        requests.get = _original_get
        _patch_applied = False
        logger.info("AKShare 网络层已卸载")


def is_patch_applied() -> bool:
    return _patch_applied


# ── 便捷初始化 ────────────────────────────────────────────


def init_akshare_network():
    """初始化 AKShare 网络层 (在导入 akshare 之前调用)

    调用 patch_akshare_requests() 并预热代理池.
    该函数是幂等的.
    """
    # 预热代理池
    if _PROXY_MODE != "off":
        _proxy_pool.refresh(force=True)

    # 应用猴子补丁
    patch_akshare_requests()


def get_network_stats() -> Dict:
    """获取网络层统计信息"""
    return {
        "patch_applied": _patch_applied,
        "curl_cffi_available": _curl_cffi_available,
        "use_curl_cffi": _USE_CURL_CFFI and _curl_cffi_available,
        "proxy_mode": _PROXY_MODE,
        "proxy_pool": _proxy_pool.get_stats(),
        "request_timeout": _REQUEST_TIMEOUT,
        "request_retries": _REQUEST_RETRIES,
        "min_interval": _MIN_REQUEST_INTERVAL,
    }
