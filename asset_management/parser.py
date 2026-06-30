"""
华为 CE 系列交换机日志解析引擎

支持从纯文本巡检日志中提取：
  - 设备基础信息（sysname, 版本）
  - 接口描述与状态（display interface description）
  - LLDP 邻居关系（display lldp nei bri）
  - 光模块资产信息（display interface transceiver）

使用策略：
  按命令块特征定位，编写正则表达式逐块提取。
  返回结构化 Python 字典，供 views / management commands 消费。
"""

import re



RE_STATUS_CLEAN = re.compile(r"^[\*\(\)a-z\^]*down|^up\([a-z]+\)$")



def clean_transfer_distance(distance: str) -> str:
    """提取传输距离的纯数字部分, 去掉括号内容.
    10000(9um/125um SMF) -> 10000,  16(Optical Cable) -> 16
    """
    if not distance:
        return ""
    m = re.match(r"^(\d+(?:\.\d+)?)", distance.strip())
    return m.group(1) if m else distance


def clean_status(status: str) -> str:
    """清理状态标记, e.g. *down -> down, up(s) -> up."""
    s = status.strip().lower()
    # *down, ^down, (b), (e), (d), (p), (dl), (c), (sd), (ed), (l), (s)
    s = re.sub(r"^[*^]", "", s)
    s = re.sub(r"\([a-z]+\)$", "", s)
    return s


# ──────────────────────────────────────────────
# 1. 设备信息
# ──────────────────────────────────────────────

RE_HOSTNAME = re.compile(r"<(.*?)>")
RE_SYSNAME = re.compile(r"sysname\s+(\S+)")
RE_VERSION = re.compile(r"!Software Version\s+(\S+)")
RE_CHASSIS = re.compile(r"board-type\s+(\S+)")


def parse_device_info(text: str) -> dict:
    """从日志文本中提取设备全局信息.

    Returns:
        {"hostname": ..., "os_version": ..., "chassis_type": ...}
    """
    info = {"hostname": "", "vendor": "Huawei", "os_version": "", "chassis_type": ""}

    # hostname from <...> pattern
    m = RE_HOSTNAME.search(text)
    if m:
        info["hostname"] = m.group(1)

    # sysname line overrides
    m = RE_SYSNAME.search(text)
    if m:
        info["hostname"] = m.group(1)

    # software version
    m = RE_VERSION.search(text)
    if m:
        info["os_version"] = m.group(1)

    # chassis type
    m = RE_CHASSIS.search(text)
    if m:
        info["chassis_type"] = m.group(1)

    return info


# ──────────────────────────────────────────────
# 2. 接口描述与状态
#    display interface description | no-more
# ──────────────────────────────────────────────

RE_IFACE_DESC_LINE = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(.*)$"
)


def parse_interface_descriptions(text: str) -> list[dict]:
    """从 'display interface description' 块提取接口列表.

    Returns:
        [{"name": ..., "phy_status": ..., "protocol_status": ..., "description": ...}, ...]
    """
    results = []
    # locate the block
    start_marker = "display interface description"
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return results

    block = text[start_idx:]

    # find the table — skip header lines until we hit the column header line
    lines = block.splitlines()
    capture = False
    for line in lines:
        # The line that starts the actual data is after:
        # "Interface                     PHY     Protocol Description"
        # and the separator line "-------------------..."
        if "Protocol Description" in line:
            capture = True
            continue
        if not capture:
            continue
        # skip separator lines (all dashes)
        if re.match(r"^[\s\-]+$", line):
            continue
        # stop at next command prompt
        if line.startswith("<") and "display" not in line and ">" in line:
            break
        if line.strip() == "":
            continue

        m = RE_IFACE_DESC_LINE.match(line)
        if m:
            name, phy, proto, desc = m.groups()
            results.append({
                "name": name.strip(),
                "interface_type": classify_interface_type(name.strip()),
                "phy_status": clean_status(phy),
                "protocol_status": clean_status(proto),
                "description": desc.strip() if desc.strip() != "-" else "",
            })

    return results



# ──────────────────────────────────────────────
# 2b. 接口状态（display interface bri 格式）
# ──────────────────────────────────────────────

RE_BRI_HEADER = re.compile(r"Interface\s+PHY\s+Protocol")
RE_BRI_LINE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)")


def parse_interface_brief(text: str) -> list[dict]:
    """从 'display interface bri' 块提取接口列表.

    display interface bri 格式:
      Interface                  PHY      Protocol  InUti OutUti   inErrors  outErrors
      100GE3/0/1                 down     down         0%     0%          0          0
      100GE4/0/5                 up       up        0.01%  0.01%          0          0

    注意: bri 格式没有 Description 列, 而且有 InUti/OutUti/errors 列.
    Member 端口 (以空格开头) 也会被捕获, 但拥有独立的状态.
    """
    results = []
    start_marker = "display interface bri"
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return results

    block = text[start_idx:]
    lines = block.splitlines()
    capture = False
    for line in lines:
        # Header detection
        if "Interface" in line and "PHY" in line and "Protocol" in line and "InUti" in line:
            capture = True
            continue
        if not capture:
            continue
        if re.match(r"^[\s\-]+$", line):
            continue
        if line.startswith("<") and ">" in line:
            break
        if line.strip() == "":
            continue
        # Skip the legend lines (InUti/OutUti: ...)
        if line.strip().startswith("InUti/OutUti"):
            continue

        m = RE_BRI_LINE.match(line)
        if m:
            name, phy, protocol = m.groups()
            name = name.strip()
            results.append({
                "name": name.strip(),
                "interface_type": classify_interface_type(name.strip()),
                "phy_status": clean_status(phy),
                "protocol_status": clean_status(protocol),
                "description": "",
            })

    return results


# ──────────────────────────────────────────────
# 3. LLDP 邻居
#    display lldp nei bri | no-more
# ──────────────────────────────────────────────

RE_LLDP_LINE = re.compile(
    r"^(\S+)\s+\d+\s+(\S+)\s+(\S.*)$"
)


def parse_lldp_neighbors(text: str) -> list[dict]:
    """从 'display lldp nei bri' 块提取 LLDP 邻居.

    Returns:
        [{"local_interface": ..., "remote_device": ..., "remote_interface": ...}, ...]
    """
    results = []
    start_marker = "display lldp nei"
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return results

    block = text[start_idx:]
    lines = block.splitlines()
    capture = False
    for line in lines:
        # Header line: "  Local Interface         Exptime(s) Neighbor Interface            Neighbor Device"
        if "Neighbor Interface" in line and "Neighbor Device" in line:
            capture = True
            continue
        if not capture:
            continue
        if re.match(r"^[\s\-]+$", line):
            continue
        if line.startswith("<") and ">" in line:
            break
        if line.strip() == "":
            continue

        m = RE_LLDP_LINE.match(line)
        if m:
            local_iface, remote_iface, remote_dev = m.groups()
            results.append({
                "local_interface": local_iface.strip(),
                "remote_interface": remote_iface.strip(),
                "remote_device": remote_dev.strip(),
            })

    return results


# ──────────────────────────────────────────────
# 4. 光模块资产
#    display interface transceiver | no-more
# ──────────────────────────────────────────────

RE_XCVR_IFACE_HEADER = re.compile(r"^\s*(\S+)\s+transceiver information:")


def parse_transceivers(text: str) -> list[dict]:
    """从 'display interface transceiver' 块提取光模块信息.

    以接口名为分隔，提取每个接口下的 Common information 和 Manufacture information.

    Returns:
        [{"interface_name": ..., "module_type": ..., "vendor_name": ...,
          "part_number": ..., "serial_number": ..., "transfer_distance": ...,
          "connector_type": ..., "wavelength": ...}, ...]
    """
    results = []
    start_marker = "display interface transceiver"
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return results

    block = text[start_idx:]
    lines = block.splitlines()

    current_iface = None
    current_section = None  # "common" or "manufacture"
    current_record = {}

    INTERESTING_KEYS = {
        "Manufacturing Date": "manufacturing_date",
        "Transceiver Type": "module_type",
        "Vendor Name": "vendor_name",
        "Vendor Part Number": "part_number",
        "Manu. Serial Number": "serial_number",
        "Transfer Distance (m)": "transfer_distance",
        "Connector Type": "connector_type",
        "Wavelength (nm)": "wavelength",
    }

    for line in lines:
        # new interface header
        m = RE_XCVR_IFACE_HEADER.match(line)
        if m:
            if current_iface and current_record:
                results.append({**current_record, "interface_name": current_iface})
            current_iface = m.group(1)
            current_section = None
            current_record = {}
            continue

        if current_iface:
            # track which section we're in
            stripped = line.strip()
            if stripped == "Common information:":
                current_section = "common"
                continue
            if stripped == "Manufacture information:":
                current_section = "manufacture"
                continue
            if stripped in ("Alarm information:", "Warning information:"):
                current_section = None
                continue

            if current_section and ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                mapped = INTERESTING_KEYS.get(key)
                if mapped and not current_record.get(mapped):
                    # For Serial Number, use the one from Manufacture section
                    # For other fields, use the one from Common section
                    if mapped == "serial_number" and current_section != "manufacture":
                        pass  # skip serial from common
                    else:
                        current_record[mapped] = value

    # flush last record
    if current_iface and current_record:
        results.append({**current_record, "interface_name": current_iface})

    return results


# ──────────────────────────────────────────────
# 5. 综合解析入口
# ──────────────────────────────────────────────

RE_PHYSICAL_IFACE = re.compile(r"^\d*GE\d+/\d+/[\d:]+(?:\[[^\]]*\])?$")


def classify_interface_type(name: str) -> str:
    """根据接口名称判断是 physical 还是 logical."""
    if RE_PHYSICAL_IFACE.match(name):
        return "physical"
    return "logical"


def parse_log_text(text: str) -> dict:
    """综合解析整段日志文本，返回所有提取的结构化数据.

    Returns:
        {
            "device": {...},
            "interfaces": [...],
            "lldp_neighbors": [...],
            "transceivers": [...],
        }
    """
    # 先尝试 display interface description, 如果没有则尝试 display interface bri
    interfaces = parse_interface_descriptions(text)
    if not interfaces:
        interfaces = parse_interface_brief(text)

    return {
        "device": parse_device_info(text),
        "interfaces": interfaces,
        "lldp_neighbors": parse_lldp_neighbors(text),
        "transceivers": parse_transceivers(text),
    }
