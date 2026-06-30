from django.db import models


class Device(models.Model):
    """核心设备表 - 记录网络设备的全局基础信息."""
    hostname = models.CharField(max_length=255, unique=True, verbose_name="设备名称",
                                help_text="例如 MTG-DEU-FRA15-VXLAN-6863-01")
    vendor = models.CharField(max_length=128, blank=True, default="Huawei", verbose_name="厂商")
    os_version = models.CharField(max_length=255, blank=True, verbose_name="软件版本",
                                  help_text="例如 V200R024C00SPC500")
    chassis_type = models.CharField(max_length=255, blank=True, verbose_name="设备型号",
                                    help_text="例如 CE6863-48S6CQ")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="入库时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "网络设备"
        verbose_name_plural = "网络设备"
        ordering = ["hostname"]

    def __str__(self):
        return self.hostname


class Interface(models.Model):
    """物理/逻辑接口表 - 核心枢纽表，所有端口属性都关联到此表."""
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="interfaces", verbose_name="归属设备"
    )
    name = models.CharField(max_length=128, verbose_name="接口名称",
                            help_text="例如 100GE1/0/1, Eth-Trunk0, Loop100")
    interface_type = models.CharField(
        max_length=16, choices=[("physical", "物理接口"), ("logical", "逻辑接口")],
        default="physical", verbose_name="接口类型",
        help_text="physical=物理数据端口 logical=Eth-Trunk/Loop/NULL等"
    )
    description = models.CharField(max_length=512, blank=True, verbose_name="端口描述")
    phy_status = models.CharField(max_length=32, blank=True, verbose_name="物理层状态",
                                  help_text="up / down")
    protocol_status = models.CharField(max_length=32, blank=True, verbose_name="协议层状态",
                                       help_text="up / down")

    class Meta:
        verbose_name = "接口"
        verbose_name_plural = "接口"
        ordering = ["device", "name"]
        unique_together = [("device", "name")]

    def __str__(self):
        return f"{self.device.hostname} - {self.name}"


class Transceiver(models.Model):
    """光模块信息表 - 一对一绑定到物理接口."""
    interface = models.OneToOneField(
        Interface, on_delete=models.CASCADE, related_name="transceiver", verbose_name="关联接口"
    )
    module_type = models.CharField(max_length=255, blank=True, verbose_name="模块类型",
                                   help_text="例如 100GBASE_Active_Optical_Cable")
    vendor_name = models.CharField(max_length=128, blank=True, verbose_name="模块厂商")
    part_number = models.CharField(max_length=128, blank=True, verbose_name="部件编号")
    serial_number = models.CharField(max_length=128, blank=True, verbose_name="序列号")
    transfer_distance = models.CharField(max_length=128, blank=True, verbose_name="传输距离",
                                         help_text="例如 16(Optical Cable)")
    connector_type = models.CharField(max_length=64, blank=True, verbose_name="连接器类型",
                                      help_text="例如 LC, -")
    wavelength = models.CharField(max_length=64, blank=True, verbose_name="波长(nm)")
    manufacturing_date = models.CharField(max_length=64, blank=True, verbose_name="生产日期",
                                          help_text="例如 2019-5-26")

    class Meta:
        verbose_name = "光模块"
        verbose_name_plural = "光模块"

    def __str__(self):
        return f"Transceiver @ {self.interface.name} - {self.serial_number or 'N/A'}"


class LLDPNeighbor(models.Model):
    """LLDP 邻居表 - 用于描绘物理拓扑连线."""
    local_interface = models.OneToOneField(
        Interface, on_delete=models.CASCADE, related_name="lldp_neighbor", verbose_name="本端接口"
    )
    remote_device = models.CharField(max_length=255, blank=True, verbose_name="对端设备名")
    remote_interface = models.CharField(max_length=128, blank=True, verbose_name="对端接口名")

    class Meta:
        verbose_name = "LLDP邻居"
        verbose_name_plural = "LLDP邻居"

    def __str__(self):
        return f"{self.local_interface.name} -> {self.remote_device} {self.remote_interface}"
