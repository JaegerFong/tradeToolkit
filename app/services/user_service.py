"""
用户服务 - PostgreSQL 版本
"""

import hashlib
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from sqlalchemy import select, update, delete, func

from app.core.database import sync_session_factory
from app.core.pg_models import User as UserModel

try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('user_service')


class UserService:
    """用户服务类"""

    @staticmethod
    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return UserService.hash_password(plain_password) == hashed_password

    async def create_user(self, user_data) -> Optional[Any]:
        try:
            session = sync_session_factory()
            try:
                # 检查用户名是否已存在
                existing = session.execute(
                    select(UserModel).where(UserModel.username == user_data.username)
                ).scalar_one_or_none()
                if existing:
                    logger.warning(f"用户名已存在: {user_data.username}")
                    return None

                # 检查邮箱是否已存在
                existing_email = session.execute(
                    select(UserModel).where(UserModel.email == user_data.email)
                ).scalar_one_or_none()
                if existing_email:
                    logger.warning(f"邮箱已存在: {user_data.email}")
                    return None

                now = datetime.now(timezone.utc)
                user = UserModel(
                    username=user_data.username,
                    email=user_data.email,
                    hashed_password=self.hash_password(user_data.password),
                    is_active=True,
                    is_verified=False,
                    is_admin=False,
                    created_at=now,
                    updated_at=now,
                    preferences={
                        "default_market": "A股",
                        "default_depth": "3",
                        "default_analysts": ["市场分析师", "基本面分析师"],
                        "auto_refresh": True,
                        "refresh_interval": 30,
                        "ui_theme": "light",
                        "sidebar_width": 240,
                        "language": "zh-CN",
                        "notifications_enabled": True,
                        "email_notifications": False,
                        "desktop_notifications": True,
                        "analysis_complete_notification": True,
                        "system_maintenance_notification": True,
                    },
                    daily_quota=1000,
                    concurrent_limit=3,
                )
                session.add(user)
                session.commit()
                logger.info(f"✅ 用户创建成功: {user_data.username}")
                return user
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 创建用户失败: {e}")
            return None

    async def authenticate_user(self, username: str, password: str) -> Optional[Any]:
        try:
            session = sync_session_factory()
            try:
                user = session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()

                if not user:
                    logger.warning(f"❌ 用户不存在: {username}")
                    return None

                if not self.verify_password(password, user.hashed_password):
                    logger.warning(f"❌ 密码错误: {username}")
                    return None

                if not user.is_active:
                    logger.warning(f"❌ 用户已禁用: {username}")
                    return None

                # 更新最后登录时间
                user.last_login = datetime.now(timezone.utc)
                session.commit()

                logger.info(f"✅ 用户认证成功: {username}")
                return user
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 用户认证失败: {e}")
            return None

    async def get_user_by_username(self, username: str) -> Optional[Any]:
        try:
            session = sync_session_factory()
            try:
                return session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 获取用户失败: {e}")
            return None

    async def get_user_by_id(self, user_id) -> Optional[Any]:
        try:
            uid = int(user_id) if user_id else 0
            session = sync_session_factory()
            try:
                return session.execute(
                    select(UserModel).where(UserModel.id == uid)
                ).scalar_one_or_none()
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 获取用户失败: {e}")
            return None

    async def update_user(self, username: str, user_data) -> Optional[Any]:
        try:
            session = sync_session_factory()
            try:
                user = session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()

                if not user:
                    return None

                if user_data.email:
                    existing = session.execute(
                        select(UserModel).where(
                            UserModel.email == user_data.email,
                            UserModel.username != username,
                        )
                    ).scalar_one_or_none()
                    if existing:
                        logger.warning(f"邮箱已被使用: {user_data.email}")
                        return None
                    user.email = user_data.email

                if user_data.preferences:
                    user.preferences = user_data.preferences.model_dump()

                if user_data.daily_quota is not None:
                    user.daily_quota = user_data.daily_quota
                if user_data.concurrent_limit is not None:
                    user.concurrent_limit = user_data.concurrent_limit

                user.updated_at = datetime.now(timezone.utc)
                session.commit()
                logger.info(f"✅ 用户信息更新成功: {username}")
                return user
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 更新用户信息失败: {e}")
            return None

    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        try:
            user = await self.authenticate_user(username, old_password)
            if not user:
                return False

            session = sync_session_factory()
            try:
                u = session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()
                if u:
                    u.hashed_password = self.hash_password(new_password)
                    u.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    logger.info(f"✅ 密码修改成功: {username}")
                    return True
            finally:
                session.close()
            return False
        except Exception as e:
            logger.error(f"❌ 修改密码失败: {e}")
            return False

    async def reset_password(self, username: str, new_password: str) -> bool:
        try:
            session = sync_session_factory()
            try:
                u = session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()
                if u:
                    u.hashed_password = self.hash_password(new_password)
                    u.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    logger.info(f"✅ 密码重置成功: {username}")
                    return True
            finally:
                session.close()
            return False
        except Exception as e:
            logger.error(f"❌ 重置密码失败: {e}")
            return False

    async def create_admin_user(self, username: str = "admin", password: str = "admin123", email: str = "admin@tradingagents.cn") -> Optional[Any]:
        try:
            session = sync_session_factory()
            try:
                existing = session.execute(
                    select(UserModel).where(UserModel.username == username)
                ).scalar_one_or_none()
                if existing:
                    logger.info(f"管理员用户已存在: {username}")
                    return existing

                now = datetime.now(timezone.utc)
                admin = UserModel(
                    username=username,
                    email=email,
                    hashed_password=self.hash_password(password),
                    is_active=True,
                    is_verified=True,
                    is_admin=True,
                    created_at=now,
                    updated_at=now,
                    preferences={
                        "default_market": "A股",
                        "default_depth": "深度",
                        "ui_theme": "light",
                        "language": "zh-CN",
                        "notifications_enabled": True,
                        "email_notifications": False,
                    },
                    daily_quota=10000,
                    concurrent_limit=10,
                )
                session.add(admin)
                session.commit()
                logger.info(f"✅ 管理员用户创建成功: {username}")
                logger.info(f"   密码: {password}")
                logger.info("   ⚠️  请立即修改默认密码！")
                return admin
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 创建管理员用户失败: {e}")
            return None

    async def list_users(self, skip: int = 0, limit: int = 100) -> List[Any]:
        try:
            from app.models.user import UserResponse
            session = sync_session_factory()
            try:
                result = session.execute(
                    select(UserModel).offset(skip).limit(limit)
                )
                users = result.scalars().all()
                return [
                    UserResponse(
                        id=str(u.id),
                        username=u.username,
                        email=u.email,
                        is_active=u.is_active,
                        is_verified=u.is_verified,
                        created_at=u.created_at,
                        last_login=u.last_login,
                        preferences=u.preferences,
                        daily_quota=u.daily_quota,
                        concurrent_limit=u.concurrent_limit,
                        total_analyses=0,
                        successful_analyses=0,
                        failed_analyses=0,
                    )
                    for u in users
                ]
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 获取用户列表失败: {e}")
            return []

    async def deactivate_user(self, username: str) -> bool:
        try:
            session = sync_session_factory()
            try:
                result = session.execute(
                    update(UserModel)
                    .where(UserModel.username == username)
                    .values(is_active=False, updated_at=datetime.now(timezone.utc))
                )
                session.commit()
                return result.rowcount > 0
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 禁用用户失败: {e}")
            return False

    async def activate_user(self, username: str) -> bool:
        try:
            session = sync_session_factory()
            try:
                result = session.execute(
                    update(UserModel)
                    .where(UserModel.username == username)
                    .values(is_active=True, updated_at=datetime.now(timezone.utc))
                )
                session.commit()
                return result.rowcount > 0
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ 激活用户失败: {e}")
            return False


# 全局用户服务实例
user_service = UserService()
