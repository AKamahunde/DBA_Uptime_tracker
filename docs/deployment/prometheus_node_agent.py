#!/usr/bin/env python3
"""
DBTRAC - Prometheus Node-Level Telemetry Agent (OLKVM & Proxmox + 3 Disk Tiers)
Queries the Prometheus HTTP API (http://grafana.nms.go.ug:9090/api/v1/query) for:
  - OLKVM Nodes (node_exporter):
      1. nmsinfra.nms.go.ug (192.168.30.26:9100) - Oracle DB Node
      2. nmsebs.nms.go.ug (192.168.30.25:9100) - Oracle EBS App Node
  - Proxmox Cluster Nodes (pve_exporter on 10.10.17.34):
      3. pvenode1 - tomcatvm & Internal Systems
      4. pvenode2 - jumphostvm & monitoringvm
      5. pvenode3 - databasevm (MySQL / Pentana)
and pushes node-level CPU, Memory, Uptime, and 3-Disk breakdown to DBTRAC's
/api/health/ingest/ endpoint.
"""

import datetime
import json
import os
import urllib.parse
import urllib.request

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://grafana.nms.go.ug:9090").rstrip("/")
INGEST_API_URL = os.environ.get("INGEST_API_URL", "http://192.168.35.38:90/api/health/ingest/")
API_KEY = os.environ.get("REPLICATION_API_KEY", "uptimetracker_api_9f2c1d8e45x9")

OLKVM_NODES = [
    {
        "node_name": "OLKVM",
        "server": "nmsinfra.nms.go.ug",
        "instance": os.environ.get("PROMETHEUS_NMSINFRA_INSTANCE", "192.168.30.26:9100"),
        "hosted_systems": "Oracle DB (nmsinfra)",
        "database": "Oracle",
    },
    {
        "node_name": "OLKVM",
        "server": "nmsebs.nms.go.ug",
        "instance": os.environ.get("PROMETHEUS_NMSEBS_INSTANCE", "192.168.30.25:9100"),
        "hosted_systems": "Oracle EBS App (nmsebs)",
        "database": "Oracle",
    },
]

PROXMOX_NODES = [
    {
        "node_name": "Proxmox",
        "server": "pvenode1",
        "pve_node": "pvenode1",
        "hosted_systems": "tomcatvm & Internal Systems",
        "database": "MySQL",
    },
    {
        "node_name": "Proxmox",
        "server": "pvenode2",
        "pve_node": "pvenode2",
        "hosted_systems": "jumphostvm & monitoringvm",
        "database": "MySQL",
    },
    {
        "node_name": "Proxmox",
        "server": "pvenode3",
        "pve_node": "pvenode3",
        "hosted_systems": "databasevm (MySQL / Pentana)",
        "database": "MySQL",
    },
]


def query_prometheus(promql):
    url = f"{PROMETHEUS_URL}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") == "success":
                return payload.get("data", {}).get("result", [])
    except Exception as exc:
        print(f"[WARN] Prometheus query error ({promql}): {exc}")
    return []


def scalar(results, default=0.0):
    if not results:
        return default
    try:
        val = float(results[0]["value"][1])
        return default if val != val else val
    except Exception:
        return default


def classify_olkvm_3_disks(instance):
    size_q = (
        f'node_filesystem_size_bytes{{instance="{instance}",'
        f'fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|autofs",mountpoint!~"/boot.*|/run.*|/swap.*"}}'
    )
    avail_q = (
        f'node_filesystem_avail_bytes{{instance="{instance}",'
        f'fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|autofs",mountpoint!~"/boot.*|/run.*|/swap.*"}}'
    )
    size_res = query_prometheus(size_q)
    avail_res = query_prometheus(avail_q)

    avail_map = {}
    for item in avail_res:
        m = item.get("metric", {})
        key = (m.get("mountpoint", ""), m.get("device", ""))
        try:
            avail_map[key] = float(item["value"][1])
        except Exception:
            pass

    nfs_disks, lvm_disks, local_disks = [], [], []
    for item in size_res:
        m = item.get("metric", {})
        mnt = m.get("mountpoint", "/")
        dev = m.get("device", "disk")
        fstype = m.get("fstype", "")
        try:
            sz_b = float(item["value"][1])
        except Exception:
            continue
        if sz_b <= 1024 ** 3:
            continue
        av_b = avail_map.get((mnt, dev), 0.0)
        us_b = max(0.0, sz_b - av_b)
        sz_gb = round(sz_b / (1024 ** 3), 2)
        us_gb = round(us_b / (1024 ** 3), 2)
        pct = round((us_gb / sz_gb) * 100.0, 2) if sz_gb > 0 else 0.0
        entry = {"mountpoint": mnt, "device": dev, "fstype": fstype, "size_gb": sz_gb, "used_gb": us_gb, "percent": pct}
        if fstype.lower() == "nfs" or ":" in dev:
            nfs_disks.append(entry)
        elif "mapper" in dev.lower() or "lv_" in dev.lower() or "vg_" in dev.lower():
            lvm_disks.append(entry)
        else:
            local_disks.append(entry)

    tiers = []
    if nfs_disks:
        nfs_disks.sort(key=lambda d: d["size_gb"], reverse=True)
        d = nfs_disks[0]
        tiers.append({"type": f"NFS Backup ({d['mountpoint']})", "size_gb": d["size_gb"], "used_gb": d["used_gb"], "percent": d["percent"]})
    if lvm_disks:
        lvm_disks.sort(key=lambda d: d["size_gb"], reverse=True)
        d = lvm_disks[0]
        tiers.append({"type": f"LVM XFS ({d['mountpoint']})", "size_gb": d["size_gb"], "used_gb": d["used_gb"], "percent": d["percent"]})
    if local_disks:
        local_disks.sort(key=lambda d: d["size_gb"], reverse=True)
        prim = [d for d in local_disks if d["mountpoint"] != "/home"] or local_disks
        tot_sz = round(sum(d["size_gb"] for d in prim), 2)
        tot_us = round(sum(d["used_gb"] for d in prim), 2)
        tot_pct = round((tot_us / tot_sz) * 100.0, 2) if tot_sz > 0 else 0.0
        mnts = ", ".join(d["mountpoint"] for d in prim[:3])
        tiers.append({"type": f"Local XFS ({mnts})", "size_gb": tot_sz, "used_gb": tot_us, "percent": tot_pct})

    defaults = [
        {"type": "NFS Backup", "size_gb": 0.0, "used_gb": 0.0, "percent": 0.0},
        {"type": "LVM XFS Volume", "size_gb": 0.0, "used_gb": 0.0, "percent": 0.0},
        {"type": "Local XFS Disk", "size_gb": 0.0, "used_gb": 0.0, "percent": 0.0},
    ]
    while len(tiers) < 3:
        tiers.append(defaults[len(tiers)])
    return tiers[:3]


def build_olkvm_payload(cfg):
    inst = cfg["instance"]
    up_res = query_prometheus(f'up{{instance="{inst}"}}')
    if not up_res:
        return None
    is_up = scalar(up_res, 1.0) >= 1.0
    logical_cpus = int(scalar(query_prometheus(f'count(node_cpu_seconds_total{{instance="{inst}",mode="idle"}})'), 0.0))
    cpu_pct = round(min(100.0, max(0.0, scalar(query_prometheus(f'100 - (avg(irate(node_cpu_seconds_total{{instance="{inst}",mode="idle"}}[5m])) * 100)'), 0.0))), 2)
    mem_tot_b = scalar(query_prometheus(f'node_memory_MemTotal_bytes{{instance="{inst}"}}'), 0.0)
    mem_av_b = scalar(query_prometheus(f'node_memory_MemAvailable_bytes{{instance="{inst}"}}'), 0.0)
    if mem_tot_b > 0:
        mem_us_b = max(0.0, mem_tot_b - mem_av_b)
        mem_tot_gb = round(mem_tot_b / (1024 ** 3), 2)
        mem_us_gb = round(mem_us_b / (1024 ** 3), 2)
        mem_pct = round((mem_us_b / mem_tot_b) * 100.0, 2)
    else:
        mem_tot_gb = mem_us_gb = mem_pct = 0.0

    up_sec = scalar(query_prometheus(f'node_time_seconds{{instance="{inst}"}} - node_boot_time_seconds{{instance="{inst}"}}'), 0.0)
    up_days = round(max(0.0, up_sec / 86400.0), 2)
    up_pct = round(min(100.0, max(0.0, scalar(query_prometheus(f'avg_over_time(up{{instance="{inst}"}}[30d]) * 100'), 100.0 if is_up else 0.0))), 2)

    growth_q = f'100 * sum(-delta(node_filesystem_avail_bytes{{instance="{inst}",fstype="xfs"}}[24h])) / sum(node_filesystem_size_bytes{{instance="{inst}",fstype="xfs"}})'
    storage_growth_pct = round(scalar(query_prometheus(growth_q), 0.0), 3)

    d1, d2, d3 = classify_olkvm_3_disks(inst)
    max_disk_pct = round(max(d1["percent"], d2["percent"], d3["percent"]), 2)

    if not is_up or cpu_pct >= 90 or mem_pct >= 90 or max_disk_pct >= 90 or up_pct < 95:
        status = "Critical"
    elif cpu_pct >= 75 or mem_pct >= 80 or max_disk_pct >= 80 or up_pct < 99.0:
        status = "Warning"
    else:
        status = "Healthy"

    return {
        "date": datetime.date.today().isoformat(),
        "node_name": "OLKVM",
        "server": cfg["server"],
        "hosted_systems": cfg["hosted_systems"],
        "database": cfg["database"],
        "entered_by": "Prometheus",
        "uptime_percent": up_pct,
        "uptime_days": up_days,
        "logical_cpus": logical_cpus,
        "cpu_percent": cpu_pct,
        "memory_percent": mem_pct,
        "memory_total_gb": mem_tot_gb,
        "memory_used_gb": mem_us_gb,
        "disk_percent": max_disk_pct,
        "storage_growth_pct": storage_growth_pct,
        "disk_1_type": d1["type"],
        "disk_1_size_gb": d1["size_gb"],
        "disk_1_used_gb": d1["used_gb"],
        "disk_1_percent": d1["percent"],
        "disk_2_type": d2["type"],
        "disk_2_size_gb": d2["size_gb"],
        "disk_2_used_gb": d2["used_gb"],
        "disk_2_percent": d2["percent"],
        "disk_3_type": d3["type"],
        "disk_3_size_gb": d3["size_gb"],
        "disk_3_used_gb": d3["used_gb"],
        "disk_3_percent": d3["percent"],
        "tablespace_percent": d2["percent"],
        "connections": 0,
        "slow_queries": 0,
        "threads": logical_cpus,
        "status": status,
    }


def build_proxmox_payload(cfg):
    pnode = cfg["pve_node"]
    nid = f"node/{pnode}"
    cpu_res = query_prometheus(f'100 * pve_cpu_usage_ratio{{id="{nid}",job="proxmox"}}')
    if not cpu_res:
        return None
    cpu_pct = round(min(100.0, max(0.0, scalar(cpu_res, 0.0))), 2)
    logical_cpus = int(scalar(query_prometheus(f'pve_cpu_usage_limit{{id="{nid}",job="proxmox"}}'), 0.0))
    mem_tot_b = scalar(query_prometheus(f'pve_memory_size_bytes{{id="{nid}",job="proxmox"}}'), 0.0)
    mem_us_b = scalar(query_prometheus(f'pve_memory_usage_bytes{{id="{nid}",job="proxmox"}}'), 0.0)
    if mem_tot_b > 0:
        mem_tot_gb = round(mem_tot_b / (1024 ** 3), 2)
        mem_us_gb = round(mem_us_b / (1024 ** 3), 2)
        mem_pct = round((mem_us_b / mem_tot_b) * 100.0, 2)
    else:
        mem_tot_gb = mem_us_gb = mem_pct = 0.0

    up_sec = scalar(query_prometheus(f'pve_uptime_seconds{{id="{nid}",job="proxmox"}}'), 0.0)
    up_days = round(max(0.0, up_sec / 86400.0), 2)
    avail_res = query_prometheus(f'100 * avg_over_time(pve_up{{id="{nid}",job="proxmox"}}[30d])')
    up_pct = round(min(100.0, max(0.0, scalar(avail_res, 100.0))), 2) if avail_res else 100.0

    growth_q = f'100 * sum(delta(pve_disk_usage_bytes{{job="proxmox",id=~"storage/{pnode}/(local|local-lvm)"}}[24h])) / sum(pve_disk_size_bytes{{job="proxmox",id=~"storage/{pnode}/(local|local-lvm)"}})'
    storage_growth_pct = round(scalar(query_prometheus(growth_q), 0.0), 3)

    def pve_storage(sname, label):
        sid = f"storage/{pnode}/{sname}"
        sz_b = scalar(query_prometheus(f'pve_disk_size_bytes{{id="{sid}",job="proxmox"}}'), 0.0)
        us_b = scalar(query_prometheus(f'pve_disk_usage_bytes{{id="{sid}",job="proxmox"}}'), 0.0)
        sz_gb = round(sz_b / (1024 ** 3), 2)
        us_gb = round(us_b / (1024 ** 3), 2)
        pct = round((us_gb / sz_gb) * 100.0, 2) if sz_gb > 0 else 0.0
        return {"type": label, "size_gb": sz_gb, "used_gb": us_gb, "percent": pct}

    d1 = pve_storage("VMSAN", f"storage/{pnode}/VMSAN")
    d2 = pve_storage("local-lvm", f"storage/{pnode}/local-lvm")
    d3 = pve_storage("local", f"storage/{pnode}/local")
    max_disk_pct = round(max(d1["percent"], d2["percent"], d3["percent"]), 2)

    if cpu_pct >= 90 or mem_pct >= 90 or max_disk_pct >= 90 or up_pct < 95:
        status = "Critical"
    elif cpu_pct >= 75 or mem_pct >= 80 or max_disk_pct >= 80 or up_pct < 99.0:
        status = "Warning"
    else:
        status = "Healthy"

    return {
        "date": datetime.date.today().isoformat(),
        "node_name": "Proxmox",
        "server": cfg["server"],
        "hosted_systems": cfg["hosted_systems"],
        "database": cfg["database"],
        "entered_by": "Prometheus",
        "uptime_percent": up_pct,
        "uptime_days": up_days,
        "logical_cpus": logical_cpus,
        "cpu_percent": cpu_pct,
        "memory_percent": mem_pct,
        "memory_total_gb": mem_tot_gb,
        "memory_used_gb": mem_us_gb,
        "disk_percent": max_disk_pct,
        "storage_growth_pct": storage_growth_pct,
        "disk_1_type": d1["type"],
        "disk_1_size_gb": d1["size_gb"],
        "disk_1_used_gb": d1["used_gb"],
        "disk_1_percent": d1["percent"],
        "disk_2_type": d2["type"],
        "disk_2_size_gb": d2["size_gb"],
        "disk_2_used_gb": d2["used_gb"],
        "disk_2_percent": d2["percent"],
        "disk_3_type": d3["type"],
        "disk_3_size_gb": d3["size_gb"],
        "disk_3_used_gb": d3["used_gb"],
        "disk_3_percent": d3["percent"],
        "tablespace_percent": d1["percent"],
        "connections": 0,
        "slow_queries": 0,
        "threads": logical_cpus,
        "status": status,
    }


def push_to_dbtrac(payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INGEST_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    for cfg in OLKVM_NODES:
        payload = build_olkvm_payload(cfg)
        if payload:
            res = push_to_dbtrac(payload)
            print(f"[OK] Pushed OLKVM node {cfg['server']} to DBTRAC: {res}")
        else:
            print(f"[SKIP] No Prometheus metrics found for OLKVM node {cfg['server']}")

    for cfg in PROXMOX_NODES:
        payload = build_proxmox_payload(cfg)
        if payload:
            res = push_to_dbtrac(payload)
            print(f"[OK] Pushed Proxmox node {cfg['server']} to DBTRAC: {res}")
        else:
            print(f"[SKIP] No Prometheus metrics found for Proxmox node {cfg['server']}")
