from fastapi import APIRouter

from app.api import analytics, decisions, feedback, health, issues

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(feedback.router)
api_router.include_router(issues.router)
api_router.include_router(decisions.router)
api_router.include_router(analytics.router)
