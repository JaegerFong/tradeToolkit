"""
中国市场数据提供器
包含 A股、港股等中国市场的数据源
"""

# 导入 AKShare 提供器
try:
    from .akshare import AKShareProvider
    AKSHARE_AVAILABLE = True
except ImportError:
    AKShareProvider = None
    AKSHARE_AVAILABLE = False

# 导入 Baostock 提供器
try:
    from .baostock import BaostockProvider
    BAOSTOCK_AVAILABLE = True
except ImportError:
    BaostockProvider = None
    BAOSTOCK_AVAILABLE = False

# 导入 TDX 提供器
try:
    from .tdx import TDXProvider, get_tdx_provider
    TDX_AVAILABLE = True
except ImportError:
    TDXProvider = None
    get_tdx_provider = None
    TDX_AVAILABLE = False

# 导入基本面快照工具
try:
    from .fundamentals_snapshot import get_fundamentals_snapshot
    FUNDAMENTALS_SNAPSHOT_AVAILABLE = True
except ImportError:
    get_fundamentals_snapshot = None
    FUNDAMENTALS_SNAPSHOT_AVAILABLE = False

__all__ = [
    'AKShareProvider',
    'AKSHARE_AVAILABLE',
    'BaostockProvider',
    'BAOSTOCK_AVAILABLE',
    'TDXProvider',
    'get_tdx_provider',
    'TDX_AVAILABLE',
    'get_fundamentals_snapshot',
    'FUNDAMENTALS_SNAPSHOT_AVAILABLE',
]

