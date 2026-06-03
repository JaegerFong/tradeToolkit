"""增强筛选服务桩 - PG 迁移后待实现"""


def get_enhanced_screening_service():
    from app.services.screening_service import ScreeningService
    return ScreeningService()
