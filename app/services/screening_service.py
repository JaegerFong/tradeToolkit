"""筛选服务桩 - PG 迁移后待实现"""


class ScreeningParams:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class ScreeningService:
    """股票筛选服务（PG 版本待实现）"""
    def __init__(self):
        pass

    async def screen_stocks(self, **kwargs):
        return {"total": 0, "items": [], "took_ms": 0, "optimization_used": "none"}
