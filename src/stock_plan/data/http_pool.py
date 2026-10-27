"""HTTP 连接复用（线程本地 Session + 超时）—— 给增量更新提速用。

为什么需要这个模块（2026-10-28 实测）：
    全市场增量更新每只股票都要向新浪发 3 个请求（历史 blob / outstanding_share /
    前复权因子），akshare 内部一律 `requests.get(...)`，**每次新建连接**：
    TCP 三次握手 + TLS 握手约 0.6~0.7 秒，占了单只总耗时（约 1.1 秒）的大头。
    实测同一批股票：默认每只 729 ms → 复用连接后 121 ms（串行，6 倍）；
    16 线程并发 8.78 只/秒 → 15.6 只/秒（2 倍）；32 线程共享池 18.3 只/秒。
    时间分布是「网络 88.6% / V8+pandas 11.4% / parquet 1%」，所以省握手的收益最直接。

为什么用「线程本地 Session」而不是一个共享 Session：
    requests.Session 的连接池与 cookie 罐不是为跨线程并发设计的。每个工作线程
    持有自己的 Session（各自 2 条连接），既拿到 keep-alive 的收益，又避免共享状态。
    实测两者速度相当（线程本地 15.6 vs 共享池 18.3，后者并发更高时才略快），
    但线程本地没有「跨线程共享可变对象」的风险。

为什么顺带注入 timeout：
    akshare 的请求没有超时，一条半死连接能把整个 worker 卡几分钟；增量更新有
    5550 个任务，只要卡住几个 worker 进度就明显变慢。默认 30 秒足够宽松。

Revision History:
    2026-10-28  新建：线程本地 keep-alive Session + 默认超时，供 updater 使用
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

import requests

_LOCAL = threading.local()


def _request_with_timeout(session: requests.Session, timeout: float):
    """给 Session.request 套一层默认超时（调用方显式传了就不覆盖）。"""
    original = session.request

    def request(method, url, **kwargs):  # noqa: ANN001, ANN003
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original(method, url, **kwargs)

    return request


def thread_session(timeout: float = 30.0) -> requests.Session:
    """取当前线程的复用 Session（首次调用时创建）。"""
    session = getattr(_LOCAL, "session", None)
    if session is None or getattr(_LOCAL, "timeout", None) != timeout:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2, pool_maxsize=2, max_retries=0
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.request = _request_with_timeout(session, timeout)
        _LOCAL.session = session
        _LOCAL.timeout = timeout
    return session


@contextmanager
def keepalive_requests(timeout: float = 30.0):
    """把 `requests.get/post` 临时指向线程本地复用 Session。

    只在这个 with 块内生效（退出时还原），所以不会影响页面里其它随手调用的代码。
    典型用法：包住增量更新的线程池。

        with keepalive_requests(timeout=30):
            with ThreadPoolExecutor(max_workers=32) as pool:
                ...

    注意：块内每个线程各自持有一条连接，线程退出后不主动 close（进程退出回收）。
    单个 worker 的 Session 最多 2 条连接，不会把文件描述符耗尽。
    """
    original_get = requests.get
    original_post = requests.post

    def _get(url, **kwargs):  # noqa: ANN001, ANN003
        return thread_session(timeout).get(url, **kwargs)

    def _post(url, **kwargs):  # noqa: ANN001, ANN003
        return thread_session(timeout).post(url, **kwargs)

    requests.get = _get
    requests.post = _post
    try:
        yield
    finally:
        requests.get = original_get
        requests.post = original_post
        session = getattr(_LOCAL, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                pass
            _LOCAL.session = None
