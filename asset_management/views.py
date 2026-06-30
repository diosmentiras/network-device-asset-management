"""
核心视图模块

包含：
  1. DashboardView        — 综合数据展示（DataTables）
  2. UploadLogView         — 文件批量上传与解析
  3. export_csv            — CSV 导出
  4. REST API views        — 为 Ansible 自动化预留
  5. TopologyAPI           — 基于 LLDP 的物理拓扑（预留 OSPF 扩展）
"""

import csv
from urllib.parse import urlencode
import os

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import TemplateView
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import LogUploadForm
from .models import Device, Interface, LLDPNeighbor, Transceiver
from .parser import parse_log_text
from .serializers import (
    DeviceSerializer,
    InterfaceSerializer,
    LLDPNeighborSerializer,
    TransceiverSerializer,
)


# ──────────────────────────────────────────────
# 1. 综合仪表板 - DataTables 展示
# ──────────────────────────────────────────────

class DashboardView(TemplateView):
    """综合视图，将 Device/Interface/Transceiver/LLDPNeighbor 联表展示."""
    template_name = "asset_management/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        devices = Device.objects.all()

        interface_qs = Interface.objects.select_related("device").prefetch_related(
            "transceiver", "lldp_neighbor"
        )

        # 设备筛选
        device_filter = self.request.GET.get("device", "")
        if device_filter:
            interface_qs = interface_qs.filter(device__hostname__startswith=device_filter)

        # 接口类型筛选
        type_filter = self.request.GET.get("type", "physical")
        if type_filter in ("physical", "logical"):
            interface_qs = interface_qs.filter(interface_type=type_filter)

        # 状态筛选
        phy_filter = self.request.GET.get("phy", "")
        if phy_filter in ("up", "down"):
            interface_qs = interface_qs.filter(phy_status=phy_filter)
        proto_filter = self.request.GET.get("proto", "")
        if proto_filter in ("up", "down"):
            interface_qs = interface_qs.filter(protocol_status=proto_filter)

        interfaces = interface_qs.all()

        ctx["devices"] = devices
        ctx["interfaces"] = interfaces
        ctx["total_devices"] = Device.objects.count()
        ctx["total_interfaces"] = Interface.objects.count()
        ctx["total_transceivers"] = Transceiver.objects.count()
        ctx["total_lldp"] = LLDPNeighbor.objects.count()
        ctx["upload_form"] = LogUploadForm()
        ctx["current_type_filter"] = type_filter
        ctx["current_device_filter"] = device_filter
        ctx["current_phy_filter"] = phy_filter
        ctx["current_proto_filter"] = proto_filter

        # 构造 CSV 导出 URL (带当前筛选条件)
        export_params = {}
        if type_filter in ("physical", "logical"):
            export_params["type"] = type_filter
        if device_filter:
            export_params["device"] = device_filter
        if phy_filter:
            export_params["phy"] = phy_filter
        if proto_filter:
            export_params["proto"] = proto_filter
        ctx["export_csv_url"] = "/export/csv/?" + urlencode(export_params)

        return ctx


# ──────────────────────────────────────────────
# 2. 文件批量上传与解析
# ──────────────────────────────────────────────

class UploadLogView(View):
    """处理日志文件上传、解析、入库."""

    def post(self, request):
        form = LogUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "表单验证失败，请重试。")
            return redirect("asset_management:dashboard")

        uploaded = form.cleaned_data["files"]
        files = uploaded if isinstance(uploaded, (list, tuple)) else [uploaded]
        success_count = 0
        error_count = 0

        for f in files:
            try:
                text = f.read().decode("utf-8", errors="replace")
                self._process_single_log(text, f.name)
                success_count += 1
            except Exception as e:
                error_count += 1
                messages.warning(request, f"文件 {f.name} 解析失败: {e}")

        if success_count:
            messages.success(request, f"成功解析 {success_count} 个文件。")
        if error_count:
            messages.warning(request, f"{error_count} 个文件解析失败。")

        return redirect("asset_management:dashboard")

    @transaction.atomic
    def _process_single_log(self, text: str, filename: str):
        """解析单份日志并 upsert 入库."""
        data = parse_log_text(text)
        device_info = data["device"]

        if not device_info.get("hostname"):
            raise ValueError(f"无法从文件 {filename} 中提取设备名称，请确认日志格式。")

        # 1. 设备
        device, _ = Device.objects.update_or_create(
            hostname=device_info["hostname"],
            defaults={
                "vendor": device_info.get("vendor", "Huawei"),
                "os_version": device_info.get("os_version", ""),
                "chassis_type": device_info.get("chassis_type", ""),
            },
        )

        # 覆盖模式: 删除该设备所有旧接口/光模块/LLDP数据, 从零重建
        device.interfaces.all().delete()

        # 2. 接口
        iface_map = {}  # name -> Interface instance
        for iface_data in data["interfaces"]:
            iface, _ = Interface.objects.update_or_create(
                device=device,
                name=iface_data["name"],
                defaults={
                    "description": iface_data.get("description", ""),
                    "phy_status": iface_data.get("phy_status", ""),
                    "protocol_status": iface_data.get("protocol_status", ""),
                },
            )
            iface_map[iface.name] = iface

        # 3. 光模块 (只关联物理接口 — 不含子接口/Trunk/Loop)
        for xcvr_data in data["transceivers"]:
            iface_name = xcvr_data["interface_name"]
            iface = iface_map.get(iface_name)
            if not iface:
                # 如果接口不在 description 列表中（极少见），尝试按名查找
                iface, _ = Interface.objects.get_or_create(
                    device=device,
                    name=iface_name,
                    defaults={"description": "", "phy_status": "", "protocol_status": ""},
                )
            Transceiver.objects.update_or_create(
                interface=iface,
                defaults={
                    "module_type": xcvr_data.get("module_type", ""),
                    "vendor_name": xcvr_data.get("vendor_name", xcvr_data.get("vendor_name", "")),
                    "part_number": xcvr_data.get("part_number", ""),
                    "serial_number": xcvr_data.get("serial_number", ""),
                    "transfer_distance": xcvr_data.get("transfer_distance", ""),
                    "connector_type": xcvr_data.get("connector_type", ""),
                    "wavelength": xcvr_data.get("wavelength", ""),
                },
            )

        # 4. LLDP 邻居
        for lldp_data in data["lldp_neighbors"]:
            local_name = lldp_data["local_interface"]
            iface = iface_map.get(local_name)
            if not iface:
                iface, _ = Interface.objects.get_or_create(
                    device=device,
                    name=local_name,
                    defaults={"description": "", "phy_status": "", "protocol_status": ""},
                )
            LLDPNeighbor.objects.update_or_create(
                local_interface=iface,
                defaults={
                    "remote_device": lldp_data.get("remote_device", ""),
                    "remote_interface": lldp_data.get("remote_interface", ""),
                },
            )


# ──────────────────────────────────────────────
# 3. CSV 导出
# ──────────────────────────────────────────────

def export_csv(request):
    """根据当前筛选条件导出 CSV (支持 scope/device/columns)."""
    scope = request.GET.get("scope", "filtered")
    device_name = request.GET.get("device", "")
    export_columns = request.GET.getlist("columns")
    if not export_columns:
        export_columns = [
            "device__hostname", "name", "phy_status", "protocol_status",
            "description", "transceiver__module_type", "transceiver__vendor_name",
            "transceiver__serial_number", "lldp_neighbor__remote_device",
            "lldp_neighbor__remote_interface",
        ]

    iface_type = request.GET.get("type", "physical")
    qs = Interface.objects.select_related(
        "device", "transceiver", "lldp_neighbor"
    )
    if iface_type in ("physical", "logical"):
        qs = qs.filter(interface_type=iface_type)
    qs = qs.all()

    # 简单搜索过滤
    search = request.GET.get("search", "")
    if search:
        qs = qs.filter(
            name__icontains=search
        ) | qs.filter(
            description__icontains=search
        ) | qs.filter(
            device__hostname__icontains=search
        )

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="network_assets.csv"'

    writer = csv.writer(response)

    # Header map
    header_map = {
        "device__hostname": "设备名称",
        "name": "接口名称",
        "phy_status": "物理状态",
        "protocol_status": "协议状态",
        "description": "端口描述",
        "transceiver__module_type": "模块类型",
        "transceiver__vendor_name": "模块厂商",
        "transceiver__part_number": "部件编号",
        "transceiver__serial_number": "序列号",
        "transceiver__transfer_distance": "传输距离",
        "transceiver__connector_type": "连接器类型",
        "transceiver__wavelength": "波长(nm)",
        "lldp_neighbor__remote_device": "对端设备",
        "lldp_neighbor__remote_interface": "对端接口",
    }

    writer.writerow([header_map.get(c, c) for c in export_columns])

    for iface in qs.iterator(chunk_size=200):
        row = []
        for col in export_columns:
            val = _resolve_nested_attr(iface, col)
            row.append(str(val) if val is not None else "")
        writer.writerow(row)

    return response


def _resolve_nested_attr(obj, path: str):
    """按双下划线分割路径访问嵌套属性, e.g. 'transceiver__module_type'."""
    parts = path.split("__")
    current = obj
    for p in parts:
        if hasattr(current, p):
            current = getattr(current, p)
        elif hasattr(current, "all"):
            # prefetch_related 返回的 RelatedManager — 取第一条
            items = current.all()
            if items:
                current = getattr(items[0], p, "")
            else:
                return ""
        else:
            return ""
        if callable(current):
            current = current()
    return current


# ──────────────────────────────────────────────
# 4. REST API — 为 Ansible 自动化对接预留
# ──────────────────────────────────────────────

class DeviceListAPI(generics.ListCreateAPIView):
    queryset = Device.objects.all()
    serializer_class = DeviceSerializer


class InterfaceListAPI(generics.ListCreateAPIView):
    queryset = Interface.objects.select_related("device").all()
    serializer_class = InterfaceSerializer


class TransceiverListAPI(generics.ListCreateAPIView):
    queryset = Transceiver.objects.select_related("interface__device").all()
    serializer_class = TransceiverSerializer


class LLDPNeighborListAPI(generics.ListCreateAPIView):
    queryset = LLDPNeighbor.objects.select_related("local_interface__device").all()
    serializer_class = LLDPNeighborSerializer


# ──────────────────────────────────────────────
# 5. 拓扑接口 — 为 D3.js / ECharts 预留
# ──────────────────────────────────────────────

class TopologyAPI(APIView):
    """基于 LLDP 数据生成物理拓扑 Nodes / Edges.

    未来可扩展：在 edges 中添加 'type' 字段区分物理（lldp）和逻辑（ospf）链路.
    """

    def get(self, request):
        nodes = {}
        edges = []

        lldp_qs = LLDPNeighbor.objects.select_related("local_interface__device").all()

        for neighbor in lldp_qs:
            local_dev = neighbor.local_interface.device.hostname
            remote_dev = neighbor.remote_device

            # 本端节点
            if local_dev not in nodes:
                nodes[local_dev] = {"id": local_dev, "label": local_dev, "type": "device"}

            # 对端节点
            if remote_dev and remote_dev not in nodes:
                nodes[remote_dev] = {"id": remote_dev, "label": remote_dev, "type": "device"}

            edges.append({
                "source": local_dev,
                "target": remote_dev,
                "source_interface": neighbor.local_interface.name,
                "target_interface": neighbor.remote_interface,
                "link_type": "lldp",
            })

        return Response({
            "nodes": list(nodes.values()),
            "edges": edges,
        })

def clean_distance(d):
    import re
    if d and '(' in d:
        m = re.match(r"^(\d+(?:\.\d+)?)", d)
        if m: return m.group(1)
    return d or ""


def interface_table_data(request):
    draw = int(request.GET.get("draw", 1))
    start = int(request.GET.get("start", 0))
    length = int(request.GET.get("length", 10))
    search_value = request.GET.get("search[value]", "").strip()
    order_col_idx = request.GET.get("order[0][column]", "0")
    order_dir = request.GET.get("order[0][dir]", "asc")

    col_names = [
        "device__hostname", "name", "phy_status", "protocol_status", "description",
        "transceiver__module_type", "transceiver__vendor_name", "transceiver__part_number",
        "transceiver__serial_number", "transceiver__manufacturing_date", "transceiver__transfer_distance",
        "transceiver__connector_type", "transceiver__wavelength",
        "lldp_neighbor__remote_device", "lldp_neighbor__remote_interface",
    ]
    idx = int(order_col_idx) if order_col_idx.isdigit() and int(order_col_idx) < len(col_names) else 1
    order_col = col_names[idx]
    if order_dir == "desc":
        order_col = "-" + order_col

    type_filter = request.GET.get("type", "physical")
    device_filter = request.GET.get("device", "")
    phy_filter = request.GET.get("phy", "")
    proto_filter = request.GET.get("proto", "")

    qs = Interface.objects.select_related("device").all()
    if type_filter in ("physical", "logical"):
        qs = qs.filter(interface_type=type_filter)
    if device_filter:
        qs = qs.filter(device__hostname__startswith=device_filter)
    if phy_filter in ("up", "down"):
        qs = qs.filter(phy_status=phy_filter)
    if proto_filter in ("up", "down"):
        qs = qs.filter(protocol_status=proto_filter)
    records_total = qs.count()

    if search_value:
        qs = qs.filter(
            Q(name__icontains=search_value) | Q(description__icontains=search_value) |
            Q(device__hostname__icontains=search_value) | Q(transceiver__vendor_name__icontains=search_value) |
            Q(transceiver__serial_number__icontains=search_value) | Q(lldp_neighbor__remote_device__icontains=search_value)
        )
    records_filtered = qs.count()
    qs = qs.order_by(order_col)[start:start + length]

    data = []
    for iface in qs:
        try:
            tr = iface.transceiver
        except:
            tr = None
        try:
            ll = iface.lldp_neighbor
        except:
            ll = None
        data.append({
            "device_hostname": iface.device.hostname, "name": iface.name,
            "phy_status": iface.phy_status, "protocol_status": iface.protocol_status,
            "description": iface.description or "",
            "transceiver_module_type": tr.module_type if tr else "",
            "transceiver_vendor_name": tr.vendor_name if tr else "",
            "transceiver_part_number": tr.part_number if tr else "",
            "transceiver_serial_number": tr.serial_number if tr else "",
            "transceiver_manufacturing_date": tr.manufacturing_date if tr else "",
            "transceiver_transfer_distance": clean_distance(tr.transfer_distance) if tr else "",
            "transceiver_connector_type": tr.connector_type if tr else "",
            "transceiver_wavelength": tr.wavelength if tr else "",
            "lldp_remote_device": ll.remote_device if ll else "",
            "lldp_remote_interface": ll.remote_interface if ll else "",
        })
    return JsonResponse({"draw": draw, "recordsTotal": records_total, "recordsFiltered": records_filtered, "data": data})

def clear_data(request):
    try:
        from .models import Interface, Transceiver, LLDPNeighbor
        cnt_i, _ = Interface.objects.all().delete()
        cnt_x, _ = Transceiver.objects.all().delete()
        cnt_l, _ = LLDPNeighbor.objects.all().delete()
        return JsonResponse({"status":"ok"})
    except Exception as e:
        return JsonResponse({"status":"error","message":str(e)}, status=500)
