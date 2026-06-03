"""
速率限制器
用于控制API调用频率，避免超过数据源的限流限制
"""
import asyncio
import time
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    滑动窗口速率限制器
    
    使用滑动窗口算法精确控制API调用频率
    """
    
    def __init__(self, max_calls: int, time_window: float, name: str = "RateLimiter"):
        """
        初始化速率限制器
        
        Args:
            max_calls: 时间窗口内最大调用次数
            time_window: 时间窗口大小（秒）
            name: 限制器名称（用于日志）
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.name = name
        self.calls = deque()  # 存储调用时间戳
        self.lock = asyncio.Lock()  # 确保线程安全
        
        # 统计信息
        self.total_calls = 0
        self.total_waits = 0
        self.total_wait_time = 0.0
        
        logger.info(f"🔧 {self.name} 初始化: {max_calls}次/{time_window}秒")
    
    async def acquire(self):
        """
        获取调用许可
        如果超过速率限制，会等待直到可以调用
        """
        async with self.lock:
            now = time.time()
            
            # 移除时间窗口外的旧调用记录
            while self.calls and self.calls[0] <= now - self.time_window:
                self.calls.popleft()
            
            # 如果当前窗口内调用次数已达上限，需要等待
            if len(self.calls) >= self.max_calls:
                # 计算需要等待的时间
                oldest_call = self.calls[0]
                wait_time = oldest_call + self.time_window - now + 0.01  # 加一点缓冲
                
                if wait_time > 0:
                    self.total_waits += 1
                    self.total_wait_time += wait_time
                    
                    logger.debug(f"⏳ {self.name} 达到速率限制，等待 {wait_time:.2f}秒")
                    await asyncio.sleep(wait_time)
                    
                    # 重新获取当前时间
                    now = time.time()
                    
                    # 再次清理旧记录
                    while self.calls and self.calls[0] <= now - self.time_window:
                        self.calls.popleft()
            
            # 记录本次调用
            self.calls.append(now)
            self.total_calls += 1
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "name": self.name,
            "max_calls": self.max_calls,
            "time_window": self.time_window,
            "current_calls": len(self.calls),
            "total_calls": self.total_calls,
            "total_waits": self.total_waits,
            "total_wait_time": self.total_wait_time,
            "avg_wait_time": self.total_wait_time / self.total_waits if self.total_waits > 0 else 0
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self.total_calls = 0
        self.total_waits = 0
        self.total_wait_time = 0.0
        logger.info(f"🔄 {self.name} 统计信息已重置")


class AKShareRateLimiter(RateLimiter):
    """
    AKShare专用速率限制器
    
    AKShare没有明确的限流规则，使用保守的限流策略
    """
    
    def __init__(self, max_calls: int = 60, time_window: float = 60):
        """
        初始化AKShare速率限制器
        
        Args:
            max_calls: 时间窗口内最大调用次数（默认60次/分钟）
            time_window: 时间窗口大小（秒）
        """
        super().__init__(
            max_calls=max_calls,
            time_window=time_window,
            name="AKShareRateLimiter"
        )


class BaoStockRateLimiter(RateLimiter):
    """
    BaoStock专用速率限制器
    
    BaoStock没有明确的限流规则，使用保守的限流策略
    """
    
    def __init__(self, max_calls: int = 100, time_window: float = 60):
        """
        初始化BaoStock速率限制器
        
        Args:
            max_calls: 时间窗口内最大调用次数（默认100次/分钟）
            time_window: 时间窗口大小（秒）
        """
        super().__init__(
            max_calls=max_calls,
            time_window=time_window,
            name="BaoStockRateLimiter"
        )


# 全局速率限制器实例
_akshare_limiter: Optional[AKShareRateLimiter] = None
_baostock_limiter: Optional[BaoStockRateLimiter] = None


def get_akshare_rate_limiter() -> AKShareRateLimiter:
    """获取AKShare速率限制器（单例）"""
    global _akshare_limiter
    if _akshare_limiter is None:
        _akshare_limiter = AKShareRateLimiter()
    return _akshare_limiter


def get_baostock_rate_limiter() -> BaoStockRateLimiter:
    """获取BaoStock速率限制器（单例）"""
    global _baostock_limiter
    if _baostock_limiter is None:
        _baostock_limiter = BaoStockRateLimiter()
    return _baostock_limiter


def reset_all_limiters():
    """重置所有速率限制器"""
    global _akshare_limiter, _baostock_limiter
    _akshare_limiter = None
    _baostock_limiter = None
    logger.info("🔄 所有速率限制器已重置")

