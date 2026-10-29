from abc import ABC, abstractmethod
from typing import Optional
from fastapi import HTTPException, Request
from core.database import UserORM

class BasePermission(ABC):
  """权限基类"""

  @abstractmethod
  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      pass

  def __call__(self, request: Request, user_id: Optional[int] = None) -> bool:
      return self.has_permission(request, user_id)


class IsAuthenticated(BasePermission):
  """已登录用户"""

  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      return user_id is not None


class IsAdmin(BasePermission):
  """管理员权限"""

  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      if not user_id:
          return False
      from core.database import UserORM, get_db
      with get_db() as db:
          user = db.query(UserORM).filter(UserORM.id == user_id).first()
          return user and getattr(user, 'role', '') == 'admin'


class IsStaff(BasePermission):
  """员工权限（admin 或 staff）"""

  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      if not user_id:
          return False
      from core.database import UserORM, get_db
      with get_db() as db:
          user = db.query(UserORM).filter(UserORM.id == user_id).first()
          return user and getattr(user, 'role', '') in ('admin', 'staff')


class IsOwner(BasePermission):
    """所有者权限（只能操作自己的数据）"""

    def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
        import logging
        logger = logging.getLogger(__name__)

        if not user_id:
            logger.warning("[IsOwner] user_id is None")
            return False

        path_params = request.path_params
        user_id_str = str(user_id)

        # 检查 companion_id
        if 'companion_id' in path_params:
            companion_id = path_params['companion_id']
            from core.state import get_companion_manager
            companion = get_companion_manager().get(companion_id)
            if companion:
                created_by = (companion.profile.created_by or '').strip()
                logger.info("[IsOwner] companion_id=%s, created_by='%s', user_id=%s, user_id_str='%s'", companion_id, created_by, user_id, user_id_str)
                if created_by == user_id_str:
                    return True
                from core.database import UserORM, get_db
                with get_db() as db:
                    user = db.query(UserORM).filter(UserORM.id == user_id).first()
                    if user:
                        uname = (user.username or '').strip()
                        nick = (user.nickname or '').strip()
                        logger.info("[IsOwner] username='%s', nickname='%s'", uname, nick)
                        return created_by in (uname, nick)
            else:
                logger.warning("[IsOwner] companion %s not found in memory", companion_id)
            return False

        # 检查 moment_id
        if 'moment_id' in path_params:
            moment_id = path_params['moment_id']
            from core.database import MomentORM, CompanionORM, get_db
            with get_db() as db:
                moment = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
                if moment:
                    companion = db.query(CompanionORM).filter(CompanionORM.id == moment.companion_id).first()
                    if companion:
                        created_by = (companion.created_by or '').strip()
                        if created_by == user_id_str:
                            return True
                        user = db.query(UserORM).filter(UserORM.id == user_id).first()
                        if user:
                            return created_by in (user.username, (user.nickname or '').strip())
            return False

        return False


class IsOwnerOrReadOnly(BasePermission):
  """所有者可写，其他人只读"""

  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      if request.method in ('GET', 'HEAD', 'OPTIONS'):
          return True
      return IsOwner().has_permission(request, user_id)


class IsAdminOrReadOnly(BasePermission):
  """管理员可写，其他人只读"""

  def has_permission(self, request: Request, user_id: Optional[int] = None) -> bool:
      if request.method in ('GET', 'HEAD', 'OPTIONS'):
          return True
      return IsAdmin().has_permission(request, user_id)