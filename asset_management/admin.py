from django.contrib import admin

from .models import Device, Interface, Transceiver, LLDPNeighbor


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ["hostname", "vendor", "os_version", "chassis_type", "created_at"]
    search_fields = ["hostname", "os_version"]
    list_filter = ["vendor"]


@admin.register(Interface)
class InterfaceAdmin(admin.ModelAdmin):
    list_display = ["name", "device", "interface_type", "phy_status", "protocol_status", "description"]
    list_filter = ["interface_type", "phy_status", "protocol_status"]
    search_fields = ["name", "description", "device__hostname"]



@admin.register(Transceiver)
class TransceiverAdmin(admin.ModelAdmin):
    list_display = ["interface", "module_type", "vendor_name", "serial_number", "manufacturing_date"]
    search_fields = ["serial_number", "vendor_name", "interface__name"]


@admin.register(LLDPNeighbor)
class LLDPNeighborAdmin(admin.ModelAdmin):
    list_display = ["local_interface", "remote_device", "remote_interface"]
    search_fields = ["remote_device", "local_interface__name"]
