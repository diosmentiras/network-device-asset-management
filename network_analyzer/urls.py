"""
项目级 URL 配置 — 预留 /api/topology/ 用于未来 OSPF 拓扑扩展.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("asset_management.urls")),
]
