"""
Main API router for Optimizarr.

This module assembles all sub-routers into a single router that
main.py includes under the /api prefix.  Each sub-router lives
in its own file for maintainability:

  profile_routes    — encoding profiles CRUD, import/export, seed
  scan_routes       — scan roots CRUD, scanning
  queue_routes      — queue CRUD, encoding control, stats
  connection_routes — Sonarr / Radarr / Stash connections, webhooks
  system_routes     — resources, settings, health, logs, backup,
                      schedule, folder watches, library types
  upscaler_routes   — AI upscaler + stereo 3D detection, downloads
"""
from fastapi import APIRouter

from app.api.profile_routes import router as profile_router
from app.api.scan_routes import router as scan_router
from app.api.queue_routes import router as queue_router
from app.api.connection_routes import router as connection_router
from app.api.system_routes import router as system_router
from app.api.upscaler_routes import router as upscaler_router

router = APIRouter()

router.include_router(profile_router)
router.include_router(scan_router)
router.include_router(queue_router)
router.include_router(connection_router)
router.include_router(system_router)
router.include_router(upscaler_router)
