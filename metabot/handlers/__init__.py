"""
Регистрация всех роутеров.

Порядок ВАЖЕН:
1. error_router — перехватывает все исключения
2. start_router — /start
3. payment_router — req:approve/reject (до admin, чтобы уведомлял юзера)
4. admin_router — /admin, admin:* (кроме req:approve/reject)
5. osint_router — menu:osint callback
6. menu_router — reply-кнопки главного меню + inline-навигация
7. Остальные по порядку
"""
from aiogram import Router

from .start import router as start_router
from .menu import router as menu_router
from .metadata_handler import router as metadata_router
from .username_handler import router as username_router
from .telegram_info import router as tg_info_router
from .osint_handler import router as osint_router
from .profile import router as profile_router
from .subscription_handler import router as sub_router
from .payment_handler import router as payment_router
from .admin_handler import router as admin_router
from .errors import router as error_router


def setup_routers() -> Router:
    """Собрать все роутеры в один корневой."""
    root = Router(name="root")

    root.include_router(error_router)
    root.include_router(start_router)
    root.include_router(payment_router)     # req:approve/reject ДО admin
    root.include_router(admin_router)
    root.include_router(osint_router)
    root.include_router(menu_router)
    root.include_router(metadata_router)
    root.include_router(username_router)
    root.include_router(tg_info_router)
    root.include_router(profile_router)
    root.include_router(sub_router)

    return root
