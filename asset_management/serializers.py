"""
序列化器 — OSPF 拓扑扩展接口预留
当前为 LLDP 物理拓扑提供序列化，未来可扩展 OSPF 路由拓扑数据。
"""
from rest_framework import serializers

from .models import Device, Interface, Transceiver, LLDPNeighbor


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = "__all__"


class InterfaceSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True)

    class Meta:
        model = Interface
        fields = "__all__"


class TransceiverSerializer(serializers.ModelSerializer):
    interface_name = serializers.CharField(source="interface.name", read_only=True)
    device_hostname = serializers.CharField(source="interface.device.hostname", read_only=True)

    class Meta:
        model = Transceiver
        fields = "__all__"


class LLDPNeighborSerializer(serializers.ModelSerializer):
    local_interface_name = serializers.CharField(source="local_interface.name", read_only=True)
    device_hostname = serializers.CharField(
        source="local_interface.device.hostname", read_only=True
    )

    class Meta:
        model = LLDPNeighbor
        fields = "__all__"


# ──────────────────────────────────────────────
# 拓扑接口（用于 D3.js / ECharts 前端的 Node/Edge 格式）
# ──────────────────────────────────────────────

class TopologyNode:
    """拓扑图节点 — 可以是物理设备，未来可扩展为 OSPF 路由节点."""
    def __init__(self, id_: str, label: str, node_type: str = "device"):
        self.id = id_
        self.label = label
        self.node_type = node_type


class TopologyEdge:
    """拓扑图边 — 目前基于 LLDP，未来可扩展为 OSPF 邻接."""
    def __init__(self, source: str, target: str, source_iface: str, target_iface: str,
                 link_type: str = "lldp"):
        self.source = source
        self.target = target
        self.source_iface = source_iface
        self.target_iface = target_iface
        self.link_type = link_type
