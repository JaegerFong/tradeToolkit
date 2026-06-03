# 导入日志模块
from tradingagents.utils.logging_manager import get_logger
logger = get_logger('dataflows')

# 导入技术指标模块
try:
    from .technical import StockstatsUtils, STOCKSTATS_AVAILABLE
except ImportError as e:
    logger.debug(f"stockstats 模块不可用: {e}")
    StockstatsUtils = None
    STOCKSTATS_AVAILABLE = False

# 从 interface 安全导入 (部分功能可能因模块裁剪不可用)
try:
    from .interface import (
        get_stock_stats_indicators_window,
        get_stockstats_indicator,
        get_china_stock_data_unified,
        get_china_stock_info_unified,
        switch_china_data_source,
        get_current_china_data_source,
    )
except ImportError as e:
    logger.debug(f"interface 部分导入失败: {e}")

__all__ = [
    "get_stock_stats_indicators_window",
    "get_stockstats_indicator",
    "get_china_stock_data_unified",
    "get_china_stock_info_unified",
    "switch_china_data_source",
    "get_current_china_data_source",
]
