#!/usr/bin/env python3
"""
创建默认管理员用户 (PostgreSQL 版本)

使用方法：
    python scripts/create_default_admin.py
    python scripts/create_default_admin.py --overwrite
    python scripts/create_default_admin.py --username myuser --password mypass123
"""

import sys
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import argparse

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from app.core.database import db_manager
db_manager.init_postgres_sync()

from app.core.database import sync_session_factory
from app.core.pg_models import User as UserModel
from sqlalchemy import select


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_admin_user(
    username: str,
    password: str,
    email: str,
    overwrite: bool = False
) -> bool:
    session = sync_session_factory()
    try:
        existing = session.execute(
            select(UserModel).where(UserModel.username == username)
        ).scalar_one_or_none()

        if existing:
            if not overwrite:
                print(f"⚠️  用户 '{username}' 已存在")
                print(f"   如需覆盖，请使用 --overwrite 参数")
                return False
            else:
                print(f"⚠️  用户 '{username}' 已存在，将覆盖")
                session.delete(existing)
                session.commit()

        now = datetime.now(timezone.utc)
        user = UserModel(
            username=username,
            email=email,
            hashed_password=hash_password(password),
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
        session.add(user)
        session.commit()

        print(f"✅ 管理员用户创建成功")
        print(f"   用户名: {username}")
        print(f"   密码: {password}")
        print(f"   邮箱: {email}")
        print(f"   角色: 管理员")
        return True
    finally:
        session.close()


def list_users():
    session = sync_session_factory()
    try:
        users = session.execute(select(UserModel)).scalars().all()

        if not users:
            print("📋 当前没有用户")
            return

        print(f"📋 当前用户列表 ({len(users)} 个):")
        print(f"{'用户名':<15} {'邮箱':<30} {'角色':<10} {'状态':<10} {'创建时间'}")
        print("-" * 90)

        for u in users:
            role = "管理员" if u.is_admin else "普通用户"
            status = "激活" if u.is_active else "禁用"
            created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "N/A"
            print(f"{u.username:<15} {u.email or 'N/A':<30} {role:<10} {status:<10} {created}")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="创建默认管理员用户")
    parser.add_argument("--username", default="admin", help="用户名（默认: admin）")
    parser.add_argument("--password", default="admin123", help="密码（默认: admin123）")
    parser.add_argument("--email", help="邮箱（默认: <username>@tradingagents.cn）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的用户")
    parser.add_argument("--list", action="store_true", help="列出所有用户")

    args = parser.parse_args()

    if not args.email:
        args.email = f"{args.username}@tradingagents.cn"

    print("=" * 80)
    print("👤 创建默认管理员用户 (PostgreSQL)")
    print("=" * 80)
    print()

    if args.list:
        list_users()
        return

    success = create_admin_user(args.username, args.password, args.email, args.overwrite)

    print()
    list_users()

    if success:
        print()
        print("=" * 80)
        print("✅ 操作完成！")
        print("=" * 80)
        print()
        print("🔐 登录信息:")
        print(f"   用户名: {args.username}")
        print(f"   密码: {args.password}")
        print()
        print("📝 后续步骤:")
        print("   1. 访问前端并使用上述账号登录")
        print("   2. 建议登录后立即修改密码")


if __name__ == "__main__":
    main()
