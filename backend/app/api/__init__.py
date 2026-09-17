from fastapi import APIRouter

from . import candidates, costs, creators, exports, jobs, status, videos

router = APIRouter(prefix="/api")
router.include_router(status.router)
router.include_router(creators.router)
router.include_router(videos.router)
router.include_router(candidates.router)
router.include_router(exports.router)
router.include_router(jobs.router)
router.include_router(costs.router)
