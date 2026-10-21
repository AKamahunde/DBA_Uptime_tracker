import logging
import os
import requests
from datetime import date

logger = logging.getLogger(__name__)

# Default Prometheus endpoint (Grafana/Prometheus server at grafana.nms.go.ug:9090)
DEFAULT_PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://grafana.nms.go.ug:9090')
PROMETHEUS_URL = DEFAULT_PROMETHEUS_URL

# OLKVM Nodes monitored via node_exporter (job="node")
OLKVM_NODES = [
    {
        'node_name': 'OLKVM',
        'server': 'nmsinfra.nms.go.ug',
        'instance': os.getenv('PROMETHEUS_NMSINFRA_INSTANCE', '192.168.30.26:9100'),
        'nodename': 'nmsinfra.nms.go.ug',
        'hosted_systems': 'Oracle DB (nmsinfra)',
        'database': 'Oracle',
    },
    {
        'node_name': 'OLKVM',
        'server': 'nmsebs.nms.go.ug',
        'instance': os.getenv('PROMETHEUS_NMSEBS_INSTANCE', '192.168.30.25:9100'),
        'nodename': 'nmsebs.nms.go.ug',
        'hosted_systems': 'Oracle EBS App (nmsebs)',
        'database': 'Oracle',
    },
]

# Proxmox Cluster Nodes monitored via pve_exporter (job="proxmox", instance="10.10.17.34")
PROXMOX_NODES = [
    {
        'node_name': 'Proxmox',
        'server': 'pvenode1',
        'pve_node': 'pvenode1',
        'hosted_systems': 'tomcatvm & Internal Systems',
        'database': 'MySQL',
    },
    {
        'node_name': 'Proxmox',
        'server': 'pvenode2',
        'pve_node': 'pvenode2',
        'hosted_systems': 'jumphostvm & monitoringvm',
        'database': 'MySQL',
    },
    {
        'node_name': 'Proxmox',
        'server': 'pvenode3',
        'pve_node': 'pvenode3',
        'hosted_systems': 'databasevm (MySQL / Pentana)',
        'database': 'MySQL',
    },
]


def fetch_json_from_api(url, headers=None, params=None, timeout=10):
    """Generic helper to fetch JSON data from a REST API endpoint."""
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching from API {url}: {e}")
        return None


def query_prometheus(promql, prometheus_url=None, timeout=8):
    """
    Execute an instant PromQL query against Prometheus HTTP API (/api/v1/query).
    Returns the list of result vectors, or an empty list on error.
    """
    base_url = (prometheus_url or DEFAULT_PROMETHEUS_URL).rstrip('/')
    endpoint = f"{base_url}/api/v1/query"
    try:
        response = requests.get(endpoint, params={'query': promql}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') == 'success':
            return payload.get('data', {}).get('result', [])
        logger.warning(f"Prometheus query returned non-success status: {payload}")
    except Exception as exc:
        logger.error(f"Prometheus query failed ({promql}): {exc}")
    return []


def _extract_scalar(results, default=0.0):
    """Extract the first float value from a Prometheus instant vector result."""
    if not results:
        return default
    try:
        val = float(results[0]['value'][1])
        if val != val:  # NaN check
            return default
        return val
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def _classify_olkvm_3_disks(instance, prometheus_url=None):
    """
    Queries Prometheus node_filesystem_* metrics for an OLKVM host (nmsinfra.nms.go.ug or nmsebs.nms.go.ug)
    and classifies its filesystems into the 3 disk tiers by size and storage type:
      - Disk 1: Shared NFS Backup Storage (fstype="nfs", e.g. /fra/nmsddbkp or /nmsddbkp ~31.2 TB)
      - Disk 2: LVM XFS Volume (device=~"/dev/mapper/.*", e.g. /fra 4 TB or /stagearea 2 TB)
      - Disk 3: Local VirtIO Block Disk (device=~"/dev/vda.*", e.g. /data, /u01, /fmw, /)
    Falls back to sorting the top 3 non-virtual filesystems by size if any tier is missing.
    """
    size_query = (
        f'node_filesystem_size_bytes{{instance="{instance}",'
        f'fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|autofs",mountpoint!~"/boot.*|/run.*|/swap.*"}}'
    )
    avail_query = (
        f'node_filesystem_avail_bytes{{instance="{instance}",'
        f'fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|autofs",mountpoint!~"/boot.*|/run.*|/swap.*"}}'
    )

    size_results = query_prometheus(size_query, prometheus_url=prometheus_url)
    avail_results = query_prometheus(avail_query, prometheus_url=prometheus_url)

    avail_map = {}
    for item in avail_results:
        m = item.get('metric', {})
        key = (m.get('mountpoint', ''), m.get('device', ''))
        try:
            avail_map[key] = float(item['value'][1])
        except (KeyError, IndexError, ValueError, TypeError):
            pass

    nfs_disks = []
    lvm_disks = []
    local_disks = []
    all_disks = []

    for item in size_results:
        m = item.get('metric', {})
        mountpoint = m.get('mountpoint', '/')
        device = m.get('device', 'disk')
        fstype = m.get('fstype', '')
        try:
            size_bytes = float(item['value'][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue

        if size_bytes <= 1024 * 1024 * 1024:  # Ignore < 1 GiB
            continue

        avail_bytes = avail_map.get((mountpoint, device), 0.0)
        used_bytes = max(0.0, size_bytes - avail_bytes)
        size_gb = round(size_bytes / (1024 ** 3), 2)
        used_gb = round(used_bytes / (1024 ** 3), 2)
        pct = round((used_gb / size_gb) * 100.0, 2) if size_gb > 0 else 0.0

        entry = {
            'mountpoint': mountpoint,
            'device': device,
            'fstype': fstype,
            'size_gb': size_gb,
            'used_gb': used_gb,
            'percent': min(100.0, max(0.0, pct)),
        }
        all_disks.append(entry)

        if fstype.lower() == 'nfs' or ':' in device:
            nfs_disks.append(entry)
        elif 'mapper' in device.lower() or 'lv_' in device.lower() or 'vg_' in device.lower():
            lvm_disks.append(entry)
        else:
            local_disks.append(entry)

    tiers = []

    # Tier 1: Shared NFS Backup (Largest)
    if nfs_disks:
        nfs_disks.sort(key=lambda d: d['size_gb'], reverse=True)
        top_nfs = nfs_disks[0]
        tiers.append({
            'type': f"NFS Backup ({top_nfs['mountpoint']})",
            'size_gb': top_nfs['size_gb'],
            'used_gb': top_nfs['used_gb'],
            'percent': top_nfs['percent'],
        })

    # Tier 2: LVM Volume (/fra or /stagearea)
    if lvm_disks:
        lvm_disks.sort(key=lambda d: d['size_gb'], reverse=True)
        top_lvm = lvm_disks[0]
        tiers.append({
            'type': f"LVM XFS ({top_lvm['mountpoint']})",
            'size_gb': top_lvm['size_gb'],
            'used_gb': top_lvm['used_gb'],
            'percent': top_lvm['percent'],
        })

    # Tier 3: Local VirtIO XFS Disk (/data, /u01, /fmw, /)
    if local_disks:
        local_disks.sort(key=lambda d: d['size_gb'], reverse=True)
        # Exclude /home if larger data mounts exist
        primary_locals = [d for d in local_disks if d['mountpoint'] != '/home'] or local_disks
        tot_size = round(sum(d['size_gb'] for d in primary_locals), 2)
        tot_used = round(sum(d['used_gb'] for d in primary_locals), 2)
        tot_pct = round((tot_used / tot_size) * 100.0, 2) if tot_size > 0 else 0.0
        mounts_label = ', '.join(d['mountpoint'] for d in primary_locals[:3])
        tiers.append({
            'type': f"Local XFS ({mounts_label})",
            'size_gb': tot_size,
            'used_gb': tot_used,
            'percent': min(100.0, max(0.0, tot_pct)),
        })

    # Fallback if fewer than 3 tiers were matched
    if len(tiers) < 3 and all_disks:
        all_disks.sort(key=lambda d: d['size_gb'], reverse=True)
        for d in all_disks:
            if len(tiers) >= 3:
                break
            label = f"{d['fstype'].upper()} ({d['mountpoint']})"
            if not any(t['type'].endswith(f"({d['mountpoint']})") for t in tiers):
                tiers.append({
                    'type': label,
                    'size_gb': d['size_gb'],
                    'used_gb': d['used_gb'],
                    'percent': d['percent'],
                })

    defaults = [
        {'type': 'NFS Backup', 'size_gb': 0.0, 'used_gb': 0.0, 'percent': 0.0},
        {'type': 'LVM XFS Volume', 'size_gb': 0.0, 'used_gb': 0.0, 'percent': 0.0},
        {'type': 'Local XFS Disk', 'size_gb': 0.0, 'used_gb': 0.0, 'percent': 0.0},
    ]
    while len(tiers) < 3:
        tiers.append(defaults[len(tiers)])

    return tiers[:3]


def fetch_olkvm_node_from_prometheus(node_cfg, prometheus_url=None):
    """
    Fetches live node_exporter metrics from Prometheus for an OLKVM host
    (nmsinfra.nms.go.ug or nmsebs.nms.go.ug).
    """
    instance = node_cfg['instance']

    # Check reachability / up metric
    up_res = query_prometheus(f'up{{instance="{instance}"}}', prometheus_url=prometheus_url)
    if not up_res:
        # Try lookup by nodename in node_uname_info
        uname_res = query_prometheus(
            f'node_uname_info{{nodename="{node_cfg["nodename"]}"}}',
            prometheus_url=prometheus_url,
        )
        if uname_res:
            instance = uname_res[0].get('metric', {}).get('instance', instance)
            up_res = query_prometheus(f'up{{instance="{instance}"}}', prometheus_url=prometheus_url)
        if not up_res:
            return None

    is_up = _extract_scalar(up_res, default=1.0) >= 1.0

    # Logical CPUs & CPU Utilization (%)
    logical_cpus = int(_extract_scalar(
        query_prometheus(f'count(node_cpu_seconds_total{{instance="{instance}",mode="idle"}})', prometheus_url), 0.0
    ))
    cpu_q = f'100 - (avg(irate(node_cpu_seconds_total{{instance="{instance}",mode="idle"}}[5m])) * 100)'
    cpu_pct = round(min(100.0, max(0.0, _extract_scalar(query_prometheus(cpu_q, prometheus_url), 0.0))), 2)

    # Memory Utilization (GiB & %)
    mem_tot_bytes = _extract_scalar(
        query_prometheus(f'node_memory_MemTotal_bytes{{instance="{instance}"}}', prometheus_url), 0.0
    )
    mem_avail_bytes = _extract_scalar(
        query_prometheus(f'node_memory_MemAvailable_bytes{{instance="{instance}"}}', prometheus_url), 0.0
    )
    if mem_tot_bytes > 0:
        mem_used_bytes = max(0.0, mem_tot_bytes - mem_avail_bytes)
        memory_total_gb = round(mem_tot_bytes / (1024 ** 3), 2)
        memory_used_gb = round(mem_used_bytes / (1024 ** 3), 2)
        memory_pct = round((mem_used_bytes / mem_tot_bytes) * 100.0, 2)
    else:
        memory_total_gb = 0.0
        memory_used_gb = 0.0
        memory_pct = 0.0

    # Uptime (days) & 30-day Uptime %
    uptime_sec = _extract_scalar(
        query_prometheus(
            f'node_time_seconds{{instance="{instance}"}} - node_boot_time_seconds{{instance="{instance}"}}',
            prometheus_url,
        ),
        0.0,
    )
    uptime_days = round(max(0.0, uptime_sec / 86400.0), 2)

    uptime_pct = _extract_scalar(
        query_prometheus(f'avg_over_time(up{{instance="{instance}"}}[30d]) * 100', prometheus_url),
        100.0 if is_up else 0.0,
    )
    uptime_pct = round(min(100.0, max(0.0, uptime_pct)), 2)

    # Node-Local Storage Growth (GB & %)
    growth_gb_q = f'sum(-delta(node_filesystem_avail_bytes{{instance="{instance}",fstype="xfs"}}[24h])) / (1024^3)'
    storage_growth_gb = round(_extract_scalar(query_prometheus(growth_gb_q, prometheus_url), 0.0), 2)
    growth_q = (
        f'100 * sum(-delta(node_filesystem_avail_bytes{{instance="{instance}",fstype="xfs"}}[24h])) '
        f'/ sum(node_filesystem_size_bytes{{instance="{instance}",fstype="xfs"}})'
    )
    storage_growth_pct = round(_extract_scalar(query_prometheus(growth_q, prometheus_url), 0.0), 3)

    # 3 Disk Tiers
    disks = _classify_olkvm_3_disks(instance, prometheus_url=prometheus_url)
    d1, d2, d3 = disks[0], disks[1], disks[2]
    max_disk_pct = round(max(d1['percent'], d2['percent'], d3['percent']), 2)

    # Status calculation
    if not is_up or cpu_pct >= 90 or memory_pct >= 90 or max_disk_pct >= 90 or uptime_pct < 95:
        status = 'Critical'
    elif cpu_pct >= 75 or memory_pct >= 80 or max_disk_pct >= 80 or uptime_pct < 99.0:
        status = 'Warning'
    else:
        status = 'Healthy'

    return {
        'node_name': 'OLKVM',
        'server': node_cfg['server'],
        'hosted_systems': node_cfg['hosted_systems'],
        'database': node_cfg['database'],
        'entered_by': 'Prometheus',
        'uptime_percent': uptime_pct,
        'uptime_days': uptime_days,
        'logical_cpus': logical_cpus,
        'cpu_percent': cpu_pct,
        'memory_percent': memory_pct,
        'memory_total_gb': memory_total_gb,
        'memory_used_gb': memory_used_gb,
        'disk_percent': max_disk_pct,
        'storage_growth_gb': storage_growth_gb,
        'storage_growth_pct': storage_growth_pct,
        'disk_1_type': d1['type'],
        'disk_1_size_gb': d1['size_gb'],
        'disk_1_used_gb': d1['used_gb'],
        'disk_1_percent': d1['percent'],
        'disk_2_type': d2['type'],
        'disk_2_size_gb': d2['size_gb'],
        'disk_2_used_gb': d2['used_gb'],
        'disk_2_percent': d2['percent'],
        'disk_3_type': d3['type'],
        'disk_3_size_gb': d3['size_gb'],
        'disk_3_used_gb': d3['used_gb'],
        'disk_3_percent': d3['percent'],
        'tablespace_percent': d2['percent'],  # /fra or /stagearea LVM volume
        'connections': 0,
        'slow_queries': 0,
        'threads': logical_cpus,
        'status': status,
    }


def fetch_proxmox_node_from_prometheus(node_cfg, prometheus_url=None):
    """
    Fetches live Proxmox VE Exporter (pve_*) metrics from Prometheus (job="proxmox")
    for a Proxmox cluster node (pvenode1, pvenode2, pvenode3), including its 3 storage tiers:
      - Disk 1: VMSAN (NFS Shared Storage - storage/pvenodeX/VMSAN)
      - Disk 2: local-lvm (LVM-Thin Storage - storage/pvenodeX/local-lvm)
      - Disk 3: local (Local Directory Storage - storage/pvenodeX/local)
    """
    pve_node = node_cfg['pve_node']
    node_id = f"node/{pve_node}"

    # Verify node exists in pve_up or pve_cpu_usage_ratio
    cpu_res = query_prometheus(f'100 * pve_cpu_usage_ratio{{id="{node_id}",job="proxmox"}}', prometheus_url)
    if not cpu_res:
        return None

    cpu_pct = round(min(100.0, max(0.0, _extract_scalar(cpu_res, 0.0))), 2)
    logical_cpus = int(_extract_scalar(
        query_prometheus(f'pve_cpu_usage_limit{{id="{node_id}",job="proxmox"}}', prometheus_url), 0.0
    ))

    # Check pve_up if present
    up_res = query_prometheus(f'pve_up{{id="{node_id}",job="proxmox"}}', prometheus_url)
    is_up = (_extract_scalar(up_res, 1.0) >= 1.0) if up_res else True

    # Memory (GiB & %)
    mem_tot_bytes = _extract_scalar(
        query_prometheus(f'pve_memory_size_bytes{{id="{node_id}",job="proxmox"}}', prometheus_url), 0.0
    )
    mem_used_bytes = _extract_scalar(
        query_prometheus(f'pve_memory_usage_bytes{{id="{node_id}",job="proxmox"}}', prometheus_url), 0.0
    )
    if mem_tot_bytes > 0:
        memory_total_gb = round(mem_tot_bytes / (1024 ** 3), 2)
        memory_used_gb = round(mem_used_bytes / (1024 ** 3), 2)
        memory_pct = round((mem_used_bytes / mem_tot_bytes) * 100.0, 2)
    else:
        memory_total_gb = 0.0
        memory_used_gb = 0.0
        memory_pct = 0.0

    # Uptime (days) & 30-day availability (%)
    uptime_sec = _extract_scalar(
        query_prometheus(f'pve_uptime_seconds{{id="{node_id}",job="proxmox"}}', prometheus_url), 0.0
    )
    uptime_days = round(max(0.0, uptime_sec / 86400.0), 2)

    avail_res = query_prometheus(f'100 * avg_over_time(pve_up{{id="{node_id}",job="proxmox"}}[30d])', prometheus_url)
    if avail_res:
        uptime_pct = round(min(100.0, max(0.0, _extract_scalar(avail_res, 100.0))), 2)
    else:
        uptime_pct = 100.0 if (is_up and uptime_days > 0) else 0.0

    # Node-Local Storage Growth (GB & %) matching Grafana 'Storage Growth'
    growth_gb_q = f'sum(delta(pve_disk_usage_bytes{{job="proxmox",id=~"storage/{pve_node}/(local|local-lvm)"}}[24h])) / (1024^3)'
    storage_growth_gb = round(_extract_scalar(query_prometheus(growth_gb_q, prometheus_url), 0.0), 2)
    growth_q = (
        f'100 * sum(delta(pve_disk_usage_bytes{{job="proxmox",id=~"storage/{pve_node}/(local|local-lvm)"}}[24h])) '
        f'/ sum(pve_disk_size_bytes{{job="proxmox",id=~"storage/{pve_node}/(local|local-lvm)"}})'
    )
    storage_growth_pct = round(_extract_scalar(query_prometheus(growth_q, prometheus_url), 0.0), 3)

    # Dynamically discover hosted guest VMs on this Proxmox node
    guest_res = query_prometheus(f'pve_guest_info{{job="proxmox",node="{pve_node}"}}', prometheus_url)
    if guest_res:
        guests = []
        for g in guest_res:
            gm = g.get('metric', {})
            gname = gm.get('name', '')
            gid = gm.get('id', '')
            if gname:
                guests.append(f"{gname} ({gid})" if gid else gname)
        hosted_systems = ', '.join(guests) if guests else node_cfg['hosted_systems']
    else:
        hosted_systems = node_cfg['hosted_systems']

    # Fetch the 3 Proxmox storage pools for this node: VMSAN (NFS), local-lvm (LVM-Thin), local (Dir)
    def _get_pve_storage(storage_name, label):
        sid = f"storage/{pve_node}/{storage_name}"
        sz_bytes = _extract_scalar(
            query_prometheus(f'pve_disk_size_bytes{{id="{sid}",job="proxmox"}}', prometheus_url), 0.0
        )
        us_bytes = _extract_scalar(
            query_prometheus(f'pve_disk_usage_bytes{{id="{sid}",job="proxmox"}}', prometheus_url), 0.0
        )
        sz_gb = round(sz_bytes / (1024 ** 3), 2)
        us_gb = round(us_bytes / (1024 ** 3), 2)
        pct = round((us_gb / sz_gb) * 100.0, 2) if sz_gb > 0 else 0.0
        return {
            'type': label,
            'size_gb': sz_gb,
            'used_gb': us_gb,
            'percent': min(100.0, max(0.0, pct)),
        }

    d1 = _get_pve_storage('VMSAN', 'VMSAN (Shared)')
    d2 = _get_pve_storage('local-lvm', 'local-lvm (LVM-Thin)')
    d3 = _get_pve_storage('local', 'local (Root / ISO)')

    max_disk_pct = round(max(d1['percent'], d2['percent'], d3['percent']), 2)

    if not is_up or cpu_pct >= 90 or memory_pct >= 90 or max_disk_pct >= 90 or uptime_pct < 95:
        status = 'Critical'
    elif cpu_pct >= 75 or memory_pct >= 80 or max_disk_pct >= 80 or uptime_pct < 99.0:
        status = 'Warning'
    else:
        status = 'Healthy'

    return {
        'node_name': 'Proxmox',
        'server': node_cfg['server'],
        'hosted_systems': hosted_systems,
        'database': node_cfg['database'],
        'entered_by': 'Prometheus',
        'uptime_percent': uptime_pct,
        'uptime_days': uptime_days,
        'logical_cpus': logical_cpus,
        'cpu_percent': cpu_pct,
        'memory_percent': memory_pct,
        'memory_total_gb': memory_total_gb,
        'memory_used_gb': memory_used_gb,
        'disk_percent': max_disk_pct,
        'storage_growth_gb': storage_growth_gb,
        'storage_growth_pct': storage_growth_pct,
        'disk_1_type': d1['type'],
        'disk_1_size_gb': d1['size_gb'],
        'disk_1_used_gb': d1['used_gb'],
        'disk_1_percent': d1['percent'],
        'disk_2_type': d2['type'],
        'disk_2_size_gb': d2['size_gb'],
        'disk_2_used_gb': d2['used_gb'],
        'disk_2_percent': d2['percent'],
        'disk_3_type': d3['type'],
        'disk_3_size_gb': d3['size_gb'],
        'disk_3_used_gb': d3['used_gb'],
        'disk_3_percent': d3['percent'],
        'tablespace_percent': d1['percent'],
        'connections': len(guest_res) if guest_res else 0,
        'slow_queries': 0,
        'threads': logical_cpus,
        'status': status,
    }


def sync_all_nodes_from_prometheus(prometheus_url=None, base_url=None, target_date=None):
    """
    Pulls metrics for all OLKVM nodes (nmsinfra.nms.go.ug, nmsebs.nms.go.ug)
    and all Proxmox nodes (pvenode1, pvenode2, pvenode3) from Prometheus
    and creates/updates today's ServerHealth records.
    Returns (synced_records, errors).
    """
    from api.models import ServerHealth

    effective_url = prometheus_url or base_url or DEFAULT_PROMETHEUS_URL
    sync_date = target_date or date.today()
    synced = []
    errors = []

    all_payloads = []

    # 1. Fetch OLKVM Nodes (nmsinfra.nms.go.ug, nmsebs.nms.go.ug)
    for node_cfg in OLKVM_NODES:
        data = fetch_olkvm_node_from_prometheus(node_cfg, prometheus_url=effective_url)
        if data:
            all_payloads.append(data)
        else:
            errors.append(
                f"OLKVM node {node_cfg['server']} ({node_cfg['instance']}): No metrics returned from Prometheus."
            )

    # 2. Fetch Proxmox Nodes (pvenode1, pvenode2, pvenode3)
    for node_cfg in PROXMOX_NODES:
        data = fetch_proxmox_node_from_prometheus(node_cfg, prometheus_url=effective_url)
        if data:
            all_payloads.append(data)
        else:
            errors.append(
                f"Proxmox node {node_cfg['server']}: No pve_exporter metrics returned from Prometheus."
            )

    # Save/update records in ServerHealth
    for data in all_payloads:
        try:
            existing = (
                ServerHealth.objects.filter(
                    date=sync_date,
                    node_name=data['node_name'],
                    server=data['server'],
                )
                .order_by('-id')
                .first()
            )
            if existing:
                for field, val in data.items():
                    setattr(existing, field, val)
                existing.save()
                synced.append(existing)
            else:
                record = ServerHealth.objects.create(date=sync_date, **data)
                synced.append(record)
        except Exception as exc:
            logger.error(f"Error saving Prometheus health record for {data.get('server')}: {exc}")
            errors.append(f"{data.get('server')}: {exc}")

    # Check for node reboots / outage gaps in Prometheus and auto-log direct 24/7 incidents
    try:
        new_incidents = scan_and_ingest_prometheus_outages(prometheus_url=effective_url)
        if new_incidents:
            logger.info(f"Auto-logged {len(new_incidents)} Prometheus node outage incident(s): {', '.join(i.ticket for i in new_incidents)}")
    except Exception as exc:
        logger.warning(f"Error during Prometheus outage scan: {exc}")

    return synced, errors


def consolidate_olkvm_incidents():
    """
    Consolidates any duplicate or VM-level infrastructure incident/staging records
    (e.g., nmsinfra and nmsebs having identical or overlapping outage times on OLKVM)
    into a single host node-level incident ('OLKVM').
    """
    from datetime import timedelta
    from django.db.models import Q
    from api.models import IncidentRegister, IncidentStaging, IncidentChangeLog

    vm_indicators = ['nmsinfra', 'nmsebs', 'olkvm']

    def is_olkvm_target(node_str, sys_str):
        n = (node_str or '').lower()
        s = (sys_str or '').lower()
        return any(k in n or k in s for k in vm_indicators)

    # 1. Consolidate IncidentRegister
    infra_regs = list(IncidentRegister.objects.filter(
        Q(category='Infrastructure') | Q(database='Infrastructure') | 
        Q(node_name__icontains='olkvm') | Q(node_name__icontains='nmsinfra') | Q(node_name__icontains='nmsebs') |
        Q(system__icontains='olkvm') | Q(system__icontains='nmsinfra') | Q(system__icontains='nmsebs')
    ).order_by('start'))

    processed_regs = set()
    for i, reg in enumerate(infra_regs):
        if reg.id in processed_regs:
            continue
        
        if not is_olkvm_target(reg.node_name, reg.system):
            continue

        # Look for duplicate host outages within 15 minutes tolerance
        duplicates = [
            r for r in infra_regs[i+1:]
            if r.id not in processed_regs and 
            is_olkvm_target(r.node_name, r.system) and
            abs((r.start - reg.start).total_seconds()) <= 900
        ]

        # Normalize the primary record to the host node level
        reg.node_name = 'OLKVM'
        reg.system = 'OLKVM Host Node (Oracle DB & EBS)'
        reg.category = 'Infrastructure'
        reg.database = 'Infrastructure'
        reg.save()
        processed_regs.add(reg.id)

        # Merge duplicate incidents
        for dup in duplicates:
            IncidentStaging.objects.filter(incident_record=dup).update(incident_record=reg)
            IncidentChangeLog.objects.filter(incident=dup).update(incident=reg)
            dup.delete()
            processed_regs.add(dup.id)

    # 2. Consolidate IncidentStaging
    staging_records = list(IncidentStaging.objects.filter(
        Q(category='Infrastructure') | Q(database='Infrastructure') | 
        Q(node_name__icontains='olkvm') | Q(node_name__icontains='nmsinfra') | Q(node_name__icontains='nmsebs') |
        Q(system__icontains='olkvm') | Q(system__icontains='nmsinfra') | Q(system__icontains='nmsebs')
    ).order_by('start'))

    processed_stg = set()
    for i, stg in enumerate(staging_records):
        if stg.id in processed_stg:
            continue

        if not is_olkvm_target(stg.node_name, stg.system):
            continue

        duplicates = [
            s for s in staging_records[i+1:]
            if s.id not in processed_stg and 
            is_olkvm_target(s.node_name, s.system) and
            abs((s.start - stg.start).total_seconds()) <= 900
        ]

        stg.node_name = 'OLKVM'
        stg.system = 'OLKVM Host Node (Oracle DB & EBS)'
        stg.category = 'Infrastructure'
        stg.database = 'Infrastructure'
        stg.save()
        processed_stg.add(stg.id)

        for dup in duplicates:
            if dup.incident_record and not stg.incident_record:
                stg.incident_record = dup.incident_record
                stg.status = dup.status
                stg.save()
            dup.delete()
            processed_stg.add(dup.id)


def scan_and_ingest_prometheus_outages(prometheus_url=None, lookback_hours=36):
    """
    Scans OLKVM and Proxmox node metrics in Prometheus for reboot events and telemetry gaps.
    If an outage occurred outside of a registered maintenance window, it is automatically
    logged directly into IncidentRegister on a 24/7 SLA basis, awaiting SysAdmin assessment.
    Consolidates VM-level telemetry to the host node level (e.g., OLKVM) to avoid duplicate
    alerts when a host goes down.
    """
    import time
    from datetime import datetime, timedelta, timezone as dt_timezone
    from django.utils import timezone
    from django.db.models import Q
    from api.models import IncidentRegister, IncidentStaging, MaintenanceRegister, IncidentChangeLog

    effective_url = prometheus_url or DEFAULT_PROMETHEUS_URL
    now_ts = time.time()
    created_incidents = []

    # Run auto-consolidation of any existing legacy VM records
    try:
        consolidate_olkvm_incidents()
    except Exception as exc:
        logger.warning(f"Error during OLKVM incident consolidation: {exc}")

    # 1. Check OLKVM Nodes (consolidated to single host node: OLKVM)
    seen_olkvm_outages = []
    for cfg in OLKVM_NODES:
        inst = cfg['instance']
        srv = cfg['server']
        short_name = srv.split('.')[0]
        try:
            boot_res = query_prometheus(f'node_boot_time_seconds{{instance="{inst}"}}', prometheus_url=effective_url)
            boot_ts = _extract_scalar(boot_res, 0.0)
            if boot_ts > 0 and (now_ts - boot_ts) < (lookback_hours * 3600):
                range_url = f"{effective_url}/api/v1/query_range"
                params = {
                    'query': f'up{{instance="{inst}"}}',
                    'start': int(boot_ts - 86400),
                    'end': int(boot_ts),
                    'step': 15
                }
                res = requests.get(range_url, params=params, timeout=10).json()
                results = res.get('data', {}).get('result', [])
                if results and results[0].get('values'):
                    pts = results[0]['values']
                    last_pt = pts[-1]
                    last_up_ts = float(last_pt[0])
                    gap_seconds = boot_ts - last_up_ts
                    if gap_seconds >= 120:  # Downtime >= 2 minutes
                        start_dt = datetime.fromtimestamp(last_up_ts, tz=dt_timezone.utc)
                        end_dt = datetime.fromtimestamp(boot_ts, tz=dt_timezone.utc)
                        duration_hrs = round(gap_seconds / 3600.0, 2)
                        
                        # Deduplicate: check if this host outage was already processed during this run or in the database
                        is_duplicate = any(abs((start_dt - seen_s).total_seconds()) <= 900 for seen_s in seen_olkvm_outages)
                        if not is_duplicate:
                            window_start = start_dt - timedelta(minutes=15)
                            window_end = start_dt + timedelta(minutes=15)
                            if IncidentRegister.objects.filter(
                                category='Infrastructure',
                                node_name__in=['OLKVM', 'nmsinfra', 'nmsebs', 'nmsinfra.nms.go.ug', 'nmsebs.nms.go.ug'],
                                start__range=(window_start, window_end)
                            ).exists() or IncidentStaging.objects.filter(
                                category='Infrastructure',
                                node_name__in=['OLKVM', 'nmsinfra', 'nmsebs', 'nmsinfra.nms.go.ug', 'nmsebs.nms.go.ug'],
                                start__range=(window_start, window_end)
                            ).exists():
                                is_duplicate = True

                        if is_duplicate:
                            logger.info(f"Prometheus telemetry outage detected on {srv} matches existing OLKVM host outage around {start_dt}; deduplicated.")
                            continue

                        seen_olkvm_outages.append(start_dt)

                        target_node = 'OLKVM'
                        target_system = 'OLKVM Host Node (Oracle DB & EBS)'

                        maint = MaintenanceRegister.objects.filter(
                            Q(start__lte=start_dt, end__gte=end_dt) &
                            (Q(database='Infrastructure') | Q(system__icontains='OLKVM') | Q(activity__icontains='OLKVM') |
                             Q(system__icontains=short_name) | Q(activity__icontains=short_name))
                        ).first()

                        mins_down = int((gap_seconds % 3600) // 60)
                        hrs_down = int(gap_seconds // 3600)
                        human_dur = f"{hrs_down}h {mins_down}m" if hrs_down > 0 else f"{mins_down}m"
                        root_cause = (
                            f"PROMETHEUS AUTO-DETECTED: Host power outage / abrupt reboot on OLKVM host node. "
                            f"Host node lost telemetry at {start_dt.astimezone().strftime('%I:%M:%S %p')} and booted back up at {end_dt.astimezone().strftime('%I:%M:%S %p')} ({human_dur} downtime). "
                            f"Telemetry verified via {srv}."
                        )
                        
                        if maint:
                            IncidentStaging.objects.get_or_create(
                                category='Infrastructure',
                                node_name=target_node,
                                start=start_dt,
                                defaults={
                                    'database': 'Infrastructure',
                                    'system': target_system,
                                    'end': end_dt,
                                    'duration_hrs': duration_hrs,
                                    'root_cause': f"AUTO-LOGGED: Outage occurred within scheduled Maintenance Window ({maint.change_id}).",
                                    'status': 'Promoted to Maintenance',
                                    'maintenance_record': maint,
                                    'processed_at': timezone.now()
                                }
                            )
                        else:
                            is_intermittent = (duration_hrs < 0.25)
                            rc_text = root_cause
                            if is_intermittent and not rc_text.startswith("[POTENTIAL NETWORK INTERMITTENCE"):
                                rc_text = f"[POTENTIAL NETWORK INTERMITTENCE (< 15m)] {root_cause}"
                            staging_obj, created = IncidentStaging.objects.update_or_create(
                                category='Infrastructure',
                                node_name=target_node,
                                start=start_dt,
                                defaults={
                                    'database': 'Infrastructure',
                                    'system': target_system,
                                    'end': end_dt,
                                    'duration_hrs': duration_hrs,
                                    'root_cause': rc_text,
                                    'status': 'Awaiting Review',
                                    'incident_record': None,
                                    'maintenance_record': None,
                                    'processed_at': None
                                }
                            )
                            if created:
                                created_incidents.append(staging_obj)
        except Exception as e:
            logger.warning(f"Error checking Prometheus outage for {srv}: {e}")

    # 2. Check Proxmox Nodes
    for cfg in PROXMOX_NODES:
        pnode = cfg['pve_node']
        try:
            up_res = query_prometheus(f'pve_node_uptime{{id="node/{pnode}"}}', prometheus_url=effective_url)
            uptime_sec = _extract_scalar(up_res, 0.0)
            if 0 < uptime_sec < (lookback_hours * 3600):
                boot_ts = now_ts - uptime_sec
                range_url = f"{effective_url}/api/v1/query_range"
                params = {
                    'query': f'pve_up{{id="node/{pnode}"}}',
                    'start': int(boot_ts - 86400),
                    'end': int(boot_ts),
                    'step': 30
                }
                res = requests.get(range_url, params=params, timeout=10).json()
                results = res.get('data', {}).get('result', [])
                if results and results[0].get('values'):
                    pts = results[0]['values']
                    last_pt = pts[-1]
                    last_up_ts = float(last_pt[0])
                    gap_seconds = boot_ts - last_up_ts
                    if gap_seconds >= 120:
                        start_dt = datetime.fromtimestamp(last_up_ts, tz=dt_timezone.utc)
                        end_dt = datetime.fromtimestamp(boot_ts, tz=dt_timezone.utc)
                        duration_hrs = round(gap_seconds / 3600.0, 2)
                        
                        window_start = start_dt - timedelta(minutes=15)
                        window_end = start_dt + timedelta(minutes=15)
                        if not IncidentRegister.objects.filter(category='Infrastructure', node_name=pnode, start__range=(window_start, window_end)).exists() and not IncidentStaging.objects.filter(category='Infrastructure', node_name=pnode, start__range=(window_start, window_end)).exists():
                            maint = MaintenanceRegister.objects.filter(
                                Q(start__lte=start_dt, end__gte=end_dt) &
                                (Q(database='Infrastructure') | Q(system__icontains=pnode) | Q(activity__icontains=pnode))
                            ).first()

                            mins_down = int((gap_seconds % 3600) // 60)
                            hrs_down = int(gap_seconds // 3600)
                            human_dur = f"{hrs_down}h {mins_down}m" if hrs_down > 0 else f"{mins_down}m"
                            root_cause = (
                                f"PROMETHEUS AUTO-DETECTED: Proxmox node reboot / power outage. "
                                f"Node lost telemetry at {start_dt.astimezone().strftime('%I:%M:%S %p')} and rebooted at {end_dt.astimezone().strftime('%I:%M:%S %p')} ({human_dur} downtime)."
                            )
                            
                            if maint:
                                IncidentStaging.objects.get_or_create(
                                    category='Infrastructure',
                                    node_name=pnode,
                                    start=start_dt,
                                    defaults={
                                        'database': 'Infrastructure',
                                        'system': f"Proxmox Hypervisor ({pnode})",
                                        'end': end_dt,
                                        'duration_hrs': duration_hrs,
                                        'root_cause': f"AUTO-LOGGED: Outage occurred within scheduled Maintenance Window ({maint.change_id}).",
                                        'status': 'Promoted to Maintenance',
                                        'maintenance_record': maint,
                                        'processed_at': timezone.now()
                                    }
                                )
                            else:
                                is_intermittent = (duration_hrs < 0.25)
                                rc_text = root_cause
                                if is_intermittent and not rc_text.startswith("[POTENTIAL NETWORK INTERMITTENCE"):
                                    rc_text = f"[POTENTIAL NETWORK INTERMITTENCE (< 15m)] {root_cause}"
                                staging_obj, created = IncidentStaging.objects.update_or_create(
                                    category='Infrastructure',
                                    node_name=pnode,
                                    start=start_dt,
                                    defaults={
                                        'database': 'Infrastructure',
                                        'system': f"Proxmox Hypervisor ({pnode})",
                                        'end': end_dt,
                                        'duration_hrs': duration_hrs,
                                        'root_cause': rc_text,
                                        'status': 'Awaiting Review',
                                        'incident_record': None,
                                        'maintenance_record': None,
                                        'processed_at': None
                                    }
                                )
                                if created:
                                    created_incidents.append(staging_obj)
        except Exception as e:
            logger.warning(f"Error checking Prometheus outage for {pnode}: {e}")

    return created_incidents
