from backend.app.routers.alerts import router as alerts_router
from backend.app.routers.metrics import router as metrics_router
from backend.app.routers.system import router as system_router
from backend.app.routers.traces import router as traces_router

__all__ = ["traces_router", "metrics_router", "alerts_router", "system_router"]
