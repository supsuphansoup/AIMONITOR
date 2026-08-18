from backend.app.routers.traces import router as traces_router
from backend.app.routers.metrics import router as metrics_router
from backend.app.routers.alerts import router as alerts_router

__all__ = ["traces_router", "metrics_router", "alerts_router"]
