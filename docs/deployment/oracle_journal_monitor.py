#!/usr/bin/env python3
"""
Oracle Journal & Crash Incident Monitor
Version: 2.0.0
Description: Scans systemd journal, boot records, and Oracle Alert Logs on Oracle Database
             (nmsinfra) and Oracle EBS Application (nmsebs) nodes for:
             1. System reboots, kernel crashes, and abrupt power cuts (via boot boundary & wtmp analysis).
             2. Oracle Redo/Instance Crash Recovery triggers ("Beginning crash recovery").
             3. Oracle fatal exceptions (ORA-00600, ORA-07445, PMON/SMON termination).
             Calculates exact downtime and POSTs incidents to DBTRAC /api/incident/ingest/.
"""

import os
import sys
import re
import glob
import json
import socket
import subprocess
import urllib.request
from datetime import datetime, timedelta


def run_cmd(cmd, check=False):
    """
    Enterprise Linux backwards-compatible wrapper around subprocess.run.
    Supports Python 3.5, 3.6, 3.7+ on Oracle Linux, RHEL, CentOS.
    """
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=check
    )


def parse_journal_timestamp(line):
    """
    Parses timestamps from systemd journal or syslog output line.
    Example line: 'Jul 28 06:45:12 nmsinfra systemd[1]: Starting Oracle Database...'
    or ISO: '2026-10-19T14:15:23+0300'
    """
    try:
        # Try ISO format first
        iso_m = re.match(r'(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})', line)
        if iso_m:
            dt_str = iso_m.group(1).replace('T', ' ')
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").astimezone()

        # Fallback to syslog format 'Mmm dd hh:mm:ss'
        ts_str = line[:15]
        current_year = datetime.now().year
        dt = datetime.strptime(f"{current_year} {ts_str}", "%Y %b %d %H:%M:%S")
        return dt.astimezone()
    except Exception:
        return None


def parse_lookback_hours(timeframe_str):
    m = re.search(r'(\d+)\s*(hour|hr|day|d)', str(timeframe_str).lower())
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if 'day' in unit or 'd' in unit:
            return num * 24
        return num
    return 48


def find_oracle_alert_logs():
    """
    Locates Oracle Database Alert Log files on the host.
    Checks ORACLE_BASE, standard OFA paths (/u01/app/oracle, /oracle, /opt/oracle),
    and ADR diagnostic directories.
    """
    candidates = []
    oracle_base = os.environ.get("ORACLE_BASE", "")
    search_dirs = [
        oracle_base,
        "/u01/app/oracle",
        "/oracle",
        "/opt/oracle",
        "/u02/app/oracle",
    ]
    for base in search_dirs:
        if base and os.path.isdir(base):
            pattern = os.path.join(base, "diag", "rdbms", "*", "*", "trace", "alert_*.log")
            matches = glob.glob(pattern)
            candidates.extend(matches)

    # Return unique sorted paths
    return sorted(list(set(candidates)))


def check_oracle_alert_log_for_recovery(alert_log_path, since_dt):
    """
    Reads the tail of the Oracle alert log to detect whether Oracle underwent
    Crash / Instance Recovery (indicative of abrupt power cut or crash) vs a clean shutdown.
    """
    if not alert_log_path or not os.path.isfile(alert_log_path):
        return None

    try:
        # Read the last 500 lines of the alert log
        res = run_cmd(["tail", "-n", "500", alert_log_path])
        if res.returncode != 0:
            return None

        lines = res.stdout.splitlines()
        has_crash_recovery = False
        has_clean_shutdown = False
        has_internal_error = False
        error_details = []

        for line in lines:
            if "Beginning crash recovery of 1 threads" in line or "Beginning instance recovery" in line:
                has_crash_recovery = True
            elif "Crash recovery completed successfully" in line or "Completed: ALTER DATABASE OPEN" in line:
                if has_crash_recovery:
                    error_details.append(line.strip())
            elif "Instance shut down complete" in line or "ALTER DATABASE CLOSE NORMAL" in line:
                has_clean_shutdown = True
            elif "ORA-00600" in line or "ORA-07445" in line or "PMON (ospid" in line:
                has_internal_error = True
                error_details.append(line.strip())

        return {
            "has_crash_recovery": has_crash_recovery,
            "has_clean_shutdown": has_clean_shutdown,
            "has_internal_error": has_internal_error,
            "details": error_details,
            "path": alert_log_path
        }
    except Exception:
        return None


def get_oracle_meaningful_summary(outage_type, crash_recovery_info=None):
    if crash_recovery_info and crash_recovery_info.get("has_crash_recovery"):
        return (
            "DBA Summary: [CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]\n"
            "The host server lost power or experienced a kernel crash without unmounting file systems.\n"
            "Oracle Database was not cleanly halted; upon reboot, Oracle performed automated Redo Log Crash Recovery "
            "to bring data blocks to consistent state before opening."
        )
    elif crash_recovery_info and crash_recovery_info.get("has_internal_error"):
        return (
            "DBA Summary: [CRITICAL OUTAGE - ORACLE INSTANCE TERMINATION]\n"
            "Oracle experienced a fatal background process crash or internal exception (ORA-00600 / ORA-07445).\n"
            "The instance halted and required service restart."
        )
    elif outage_type == "Power Outage / Abrupt Crash":
        return (
            "DBA Summary: [CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]\n"
            "The host server suffered an abrupt power cut or hardware/kernel crash without graceful shutdown.\n"
            "Power was subsequently restored and the system booted, restoring Oracle database services."
        )
    elif outage_type == "Graceful Restart":
        return (
            "DBA Summary: [MAINTENANCE - CLEAN SYSTEM REBOOT]\n"
            "A system administrator or planned maintenance procedure cleanly shut down Oracle and rebooted the host.\n"
            "All database buffers were flushed cleanly and services resumed."
        )
    else:
        return (
            "DBA Summary: [OUTAGE - SYSTEM REBOOT]\n"
            "The operating system rebooted and Oracle services were restarted.\n"
            "The database started up successfully."
        )


def detect_oracle_boot_and_power_outages(lookback_hours=48, system_name=None):
    """
    Detects power outages and system crashes on Oracle hosts by analyzing:
    1. journalctl --list-boots
    2. /var/log/wtmp (via last -x -F reboot shutdown)
    3. Oracle Alert Log for crash recovery confirmations
    """
    if not system_name:
        hostname = socket.gethostname().lower()
        if "infra" in hostname:
            system_name = "nmsinfra (DB)"
        elif "ebs" in hostname:
            system_name = "nmsebs (App)"
        else:
            system_name = f"Oracle Host ({hostname})"

    detected = []
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=lookback_hours)

    # Check for Oracle alert log details
    alert_logs = find_oracle_alert_logs()
    alert_log_path = alert_logs[0] if alert_logs else None

    # 1. Primary Method: Query journalctl --list-boots
    try:
        res = run_cmd(["journalctl", "--list-boots", "--no-pager"])
        if res.returncode == 0 and res.stdout.strip():
            boot_lines = res.stdout.strip().splitlines()
            boots = []
            for line in boot_lines:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                boot_id = parts[1]
                ts_matches = re.findall(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                if len(ts_matches) >= 2:
                    try:
                        b_start = datetime.strptime(ts_matches[0], "%Y-%m-%d %H:%M:%S").astimezone()
                        b_end = datetime.strptime(ts_matches[1], "%Y-%m-%d %H:%M:%S").astimezone()
                        boots.append({
                            'id': boot_id,
                            'start': b_start,
                            'end': b_end,
                            'raw': line
                        })
                    except Exception:
                        pass

            # Check transitions between boots
            for i in range(len(boots) - 1):
                prev_boot = boots[i]
                curr_boot = boots[i + 1]

                if curr_boot['start'] >= cutoff or prev_boot['end'] >= cutoff:
                    # Check if prev_boot had a graceful shutdown sequence
                    clean_shutdown = False
                    try:
                        chk = run_cmd(["journalctl", "-b", prev_boot['id'], "-n", "50", "--no-pager"])
                        if chk.returncode == 0:
                            clean_markers = [
                                "Shutting down.",
                                "systemd-shutdown",
                                "Reached target Power-Off",
                                "Reached target Shutdown",
                                "Reached target Reboot",
                                "systemd[1]: Stopped target",
                                "Instance shut down complete"
                            ]
                            clean_shutdown = any(m in chk.stdout for m in clean_markers)
                    except Exception:
                        pass

                    outage_start = prev_boot['end']
                    outage_end = curr_boot['start']
                    duration_sec = (outage_end - outage_start).total_seconds()

                    if duration_sec > 30:  # Downtime greater than 30s
                        outage_type = "Graceful Restart" if clean_shutdown else "Power Outage / Abrupt Crash"
                        crash_info = check_oracle_alert_log_for_recovery(alert_log_path, outage_start)
                        summary = get_oracle_meaningful_summary(outage_type, crash_info)

                        evidence_lines = [
                            f"Previous Boot ({prev_boot['id'][:8]}): Last system log recorded at {outage_start.strftime('%Y-%m-%d %H:%M:%S')}",
                            f"Subsequent Boot ({curr_boot['id'][:8]}): System booted and Oracle restored at {outage_end.strftime('%Y-%m-%d %H:%M:%S')}",
                            f"Calculated Total Downtime: {round(duration_sec / 60, 1)} minutes"
                        ]

                        if crash_info and crash_info.get("has_crash_recovery"):
                            evidence_lines.append(f"Oracle Alert Log ({crash_info['path']}): Redo log crash recovery verified upon startup.")

                        detected.append({
                            "start": outage_start.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": outage_end.strftime("%Y-%m-%d %H:%M:%S"),
                            "database": "Oracle",
                            "system": system_name,
                            "root_cause": (
                                f"AUTO-LOGGED: {outage_type} detected.\n\n{summary}\n\n"
                                f"Evidence:\n" + "\n".join(evidence_lines)
                            )
                        })
    except Exception as e:
        print(f"[WARN] Boot list check warning: {e}", file=sys.stderr)

    # 2. Fallback via `last -x -F reboot shutdown` if journal boots returned nothing
    if not detected:
        try:
            l_res = run_cmd(["last", "-x", "-F", "-n", "30", "reboot", "shutdown"])
            if l_res.returncode == 0 and l_res.stdout.strip():
                wtmp_lines = l_res.stdout.strip().splitlines()
                date_pattern = r'[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}'
                for line in wtmp_lines:
                    if 'crash' in line.lower() or ('reboot' in line and '-' in line):
                        dts = re.findall(date_pattern, line)
                        if len(dts) >= 2:
                            try:
                                crash_dt = datetime.strptime(dts[1], "%a %b %d %H:%M:%S %Y").astimezone()
                                if crash_dt >= cutoff:
                                    btime_res = run_cmd(["uptime", "-s"])
                                    if btime_res.returncode == 0 and btime_res.stdout.strip():
                                        boot_dt = datetime.strptime(btime_res.stdout.strip(), "%Y-%m-%d %H:%M:%S").astimezone()
                                        if boot_dt > crash_dt:
                                            dur_m = round((boot_dt - crash_dt).total_seconds() / 60, 1)
                                            crash_info = check_oracle_alert_log_for_recovery(alert_log_path, crash_dt)
                                            summary = get_oracle_meaningful_summary("Power Outage / Abrupt Crash", crash_info)

                                            detected.append({
                                                "start": crash_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                                "end": boot_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                                "database": "Oracle",
                                                "system": system_name,
                                                "root_cause": (
                                                    f"AUTO-LOGGED: Power Outage / Abrupt Crash detected.\n\n{summary}\n\n"
                                                    f"Evidence:\n"
                                                    f"Power loss detected via /var/log/wtmp: {crash_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                                    f"Server booted and recovered: {boot_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                                    f"Total Downtime: {dur_m} minutes"
                                                )
                                            })
                            except Exception:
                                pass
        except Exception as e:
            print(f"[WARN] WTMP last check warning: {e}", file=sys.stderr)

    return detected


def main():
    api_url = "http://192.168.35.38:90/api/incident/ingest/"
    api_key = "uptimetracker_api_9f2c1d8e45x9"
    timeframe = "48 hours ago"
    hostname = socket.gethostname().lower()
    system_name = "nmsinfra (DB)" if "infra" in hostname else ("nmsebs (App)" if "ebs" in hostname else f"Oracle ({hostname})")

    # CLI Overrides
    if len(sys.argv) > 1:
        api_url = sys.argv[1]
    if len(sys.argv) > 2:
        api_key = sys.argv[2]
    if len(sys.argv) > 3:
        timeframe = sys.argv[3]

    if not api_key:
        api_key = os.environ.get('REPLICATION_API_KEY', '')

    if not api_key:
        print("Error: Ingestion API Key must be provided as an argument or in REPLICATION_API_KEY env var.", file=sys.stderr)
        sys.exit(1)

    lookback_hours = parse_lookback_hours(timeframe)
    print(f"Running Oracle host journal, alert log & power outage check (lookback: {lookback_hours} hours)...")

    incidents_detected = detect_oracle_boot_and_power_outages(lookback_hours=lookback_hours, system_name=system_name)

    if not incidents_detected:
        print(f"No power outages or crash events detected for Oracle in the last {lookback_hours} hours.")
        return

    print(f"Detected {len(incidents_detected)} Oracle outage incident(s). Ingesting to DBTRAC API...")

    success_count = 0
    for payload in incidents_detected:
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                api_url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'X-API-KEY': api_key
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = json.loads(response.read().decode('utf-8'))
                if res_body.get('status') == 'success':
                    print(f"✓ Successfully Logged Incident to Staging: ID {res_body.get('id')} ({payload['start']} to {payload['end']})")
                    success_count += 1
                elif res_body.get('status') == 'ignored':
                    print(f"✓ Already Registered / Staged ({payload['start']})")
                    success_count += 1
                else:
                    print(f"API Response: {res_body}")
        except Exception as e:
            print(f"Failed to ingest incident starting at {payload['start']}: {e}", file=sys.stderr)

    print(f"Completed. Successfully processed {success_count} Oracle outage incident(s).")


if __name__ == '__main__':
    main()
