"""初始化业务表及首个管理员；密码只从隐藏输入或环境变量读取。"""

import argparse
import getpass
import os

from app.db.session import initialize_database, session_factory
from app.services.identity_service import bootstrap_admin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass(
        "管理员密码（至少12位，不回显）："
    )
    initialize_database()
    with session_factory()() as db:
        bootstrap_admin(db, args.tenant, args.username, password)
        db.commit()
    print("管理员已初始化；已有租户不会被覆盖。")


if __name__ == "__main__":
    main()
