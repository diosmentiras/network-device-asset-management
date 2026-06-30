from django.urls import path

from . import views
from django.views.decorators.http import require_POST

app_name = "asset_management"

urlpatterns = [
    # 1. 文件上传
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("upload/", views.UploadLogView.as_view(), name="upload"),

    # 2. 数据导出
    path("export/csv/", views.export_csv, name="export_csv"),

    # 3. REST API (为 Ansible/自动化对接预留)
    path("api/devices/", views.DeviceListAPI.as_view(), name="api_devices"),
    path("api/interfaces/", views.InterfaceListAPI.as_view(), name="api_interfaces"),
    path("api/transceivers/", views.TransceiverListAPI.as_view(), name="api_transceivers"),
    path("api/lldp-neighbors/", views.LLDPNeighborListAPI.as_view(), name="api_lldp"),

    # 4. 拓扑接口 (为 D3.js / ECharts 前端预留, 目前基于 LLDP)
    path("api/clear-data/", views.clear_data),
    path("api/topology/", views.TopologyAPI.as_view(), name="api_topology"),
]
