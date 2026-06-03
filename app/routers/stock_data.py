"""
股票数据API路由 - 基于扩展数据模型
提供标准化的股票数据访问接口
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status

from app.routers.auth_db import get_current_user
from app.services.stock_data_service import get_stock_data_service
from app.models import (
    StockBasicInfoResponse,
    MarketQuotesResponse,
    StockListResponse,
    StockBasicInfoExtended,
    MarketQuotesExtended,
    MarketType
)

router = APIRouter(prefix="/api/stock-data", tags=["股票数据"])


@router.get("/basic-info/{symbol}", response_model=StockBasicInfoResponse)
async def get_stock_basic_info(
    symbol: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取股票基础信息

    Args:
        symbol: 股票代码 (支持6位A股代码)

    Returns:
        StockBasicInfoResponse: 包含扩展字段的股票基础信息
    """
    try:
        service = get_stock_data_service()
        stock_info = await service.get_stock_basic_info(symbol)

        if not stock_info:
            return StockBasicInfoResponse(
                success=False,
                message=f"未找到股票代码 {symbol} 的基础信息"
            )

        return StockBasicInfoResponse(
            success=True,
            data=stock_info,
            message="获取成功"
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取股票基础信息失败: {str(e)}"
        )


@router.get("/quotes/{symbol}", response_model=MarketQuotesResponse)
async def get_market_quotes(
    symbol: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取实时行情数据

    Args:
        symbol: 股票代码 (支持6位A股代码)

    Returns:
        MarketQuotesResponse: 包含扩展字段的实时行情数据
    """
    try:
        service = get_stock_data_service()
        quotes = await service.get_market_quotes(symbol)

        if not quotes:
            return MarketQuotesResponse(
                success=False,
                message=f"未找到股票代码 {symbol} 的行情数据"
            )

        return MarketQuotesResponse(
            success=True,
            data=quotes,
            message="获取成功"
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取实时行情失败: {str(e)}"
        )


@router.get("/list", response_model=StockListResponse)
async def get_stock_list(
    market: Optional[str] = Query(None, description="市场筛选"),
    industry: Optional[str] = Query(None, description="行业筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取股票列表

    Args:
        market: 市场筛选 (可选)
        industry: 行业筛选 (可选)
        page: 页码 (从1开始)
        page_size: 每页大小 (1-100)

    Returns:
        StockListResponse: 股票列表数据
    """
    try:
        service = get_stock_data_service()
        stock_list = await service.get_stock_list(
            market=market,
            industry=industry,
            page=page,
            page_size=page_size
        )

        # 计算总数 (简化实现，实际应该单独查询)
        total = len(stock_list)

        return StockListResponse(
            success=True,
            data=stock_list,
            total=total,
            page=page,
            page_size=page_size,
            message="获取成功"
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取股票列表失败: {str(e)}"
        )


@router.get("/combined/{symbol}")
async def get_combined_stock_data(
    symbol: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取股票综合数据 (基础信息 + 实时行情)

    Args:
        symbol: 股票代码

    Returns:
        dict: 包含基础信息和实时行情的综合数据
    """
    try:
        service = get_stock_data_service()

        # 并行获取基础信息和行情数据
        import asyncio
        basic_info_task = service.get_stock_basic_info(symbol)
        quotes_task = service.get_market_quotes(symbol)

        basic_info, quotes = await asyncio.gather(
            basic_info_task,
            quotes_task,
            return_exceptions=True
        )

        # 处理异常
        if isinstance(basic_info, Exception):
            basic_info = None
        if isinstance(quotes, Exception):
            quotes = None

        if not basic_info and not quotes:
            return {
                "success": False,
                "message": f"未找到股票代码 {symbol} 的任何数据"
            }

        return {
            "success": True,
            "data": {
                "basic_info": basic_info.dict() if basic_info else None,
                "quotes": quotes.dict() if quotes else None,
                "symbol": symbol,
                "timestamp": quotes.updated_at if quotes else None
            },
            "message": "获取成功"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取股票综合数据失败: {str(e)}"
        )


@router.get("/search")
async def search_stocks(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(10, ge=1, le=50, description="返回数量限制"),
    current_user: dict = Depends(get_current_user)
):
    """
    搜索股票

    Args:
        keyword: 搜索关键词 (股票代码或名称)
        limit: 返回数量限制

    Returns:
        dict: 搜索结果
    """
    try:
        from app.core.database import async_session_factory
        from app.core.unified_config import UnifiedConfigManager
        from app.core.pg_models import StockBasicInfo
        from sqlalchemy import select, or_

        # 获取数据源优先级配置
        config = UnifiedConfigManager()
        data_source_configs = await config.get_data_source_configs_async()

        enabled_sources = [
            ds.type.lower() for ds in data_source_configs
            if ds.enabled and ds.type.lower() in ['akshare', 'baostock']
        ]

        if not enabled_sources:
            enabled_sources = ['akshare', 'baostock']

        preferred_source = enabled_sources[0] if enabled_sources else 'tushare'

        async with async_session_factory() as session:
            # 构建搜索条件
            conditions = []

            # 如果是6位数字，按代码精确匹配
            if keyword.isdigit() and len(keyword) == 6:
                conditions.append(StockBasicInfo.symbol == keyword)
                conditions.append(StockBasicInfo.code == keyword)
            else:
                # 按名称模糊匹配
                conditions.append(StockBasicInfo.name.ilike(f"%{keyword}%"))
                # 如果包含数字，也尝试代码匹配
                if any(c.isdigit() for c in keyword):
                    conditions.append(StockBasicInfo.symbol.ilike(f"%{keyword}%"))
                    conditions.append(StockBasicInfo.code.ilike(f"%{keyword}%"))

            query = select(StockBasicInfo).where(
                or_(*conditions),
                StockBasicInfo.source == preferred_source
            ).limit(limit)

            result = await session.execute(query)
            docs = result.scalars().all()

        # 数据标准化
        service = get_stock_data_service()
        standardized_results = []
        for doc in docs:
            doc_dict = _model_to_dict(doc)
            standardized_doc = service._standardize_basic_info(doc_dict)
            standardized_results.append(standardized_doc)

        return {
            "success": True,
            "data": standardized_results,
            "total": len(standardized_results),
            "keyword": keyword,
            "source": preferred_source,
            "message": "搜索完成"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"搜索股票失败: {str(e)}"
        )


@router.get("/markets")
async def get_market_summary(
    current_user: dict = Depends(get_current_user)
):
    """
    获取市场概览

    Returns:
        dict: 各市场的股票数量统计
    """
    try:
        from app.core.database import async_session_factory
        from app.core.pg_models import StockBasicInfo
        from sqlalchemy import select, func

        async with async_session_factory() as session:
            # 按 market 分组统计 (替代 MongoDB aggregation)
            result = await session.execute(
                select(
                    StockBasicInfo.market,
                    func.count(StockBasicInfo.id)
                ).group_by(StockBasicInfo.market).order_by(func.count(StockBasicInfo.id).desc())
            )
            market_stats = [{"_id": row[0], "count": row[1]} for row in result.all()]

            # 总计数
            total_result = await session.execute(
                select(func.count(StockBasicInfo.id))
            )
            total_count = total_result.scalar()

        return {
            "success": True,
            "data": {
                "total_stocks": total_count,
                "market_breakdown": market_stats,
                "supported_markets": ["CN"],
                "last_updated": None
            },
            "message": "获取成功"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取市场概览失败: {str(e)}"
        )


@router.get("/sync-status/quotes")
async def get_quotes_sync_status(
    current_user: dict = Depends(get_current_user)
):
    """
    获取实时行情同步状态

    Returns:
        dict: 同步状态数据
    """
    try:
        from app.services.quotes_ingestion_service import QuotesIngestionService

        service = QuotesIngestionService()
        status_data = await service.get_sync_status()

        return {
            "success": True,
            "data": status_data,
            "message": "获取成功"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取同步状态失败: {str(e)}"
        )


def _model_to_dict(model_instance):
    """将 SQLAlchemy 模型实例转换为字典"""
    if model_instance is None:
        return {}
    return {c.name: getattr(model_instance, c.name)
            for c in model_instance.__table__.columns}
