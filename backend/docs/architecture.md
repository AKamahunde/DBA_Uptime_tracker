# DBTRAC - System Architecture & Technical Specifications

> **Notice:** The definitive, approved technical architecture document is the **[DBTRAC System Design Document v2.0 (October 2026)](file:///c:/Users/Ashley.atim/Downloads/DBA_Uptime_tracker-main/DBA_Uptime_tracker-main/docs/DBTRAC_SYSTEM_DESIGN_DOCUMENT_v2.0.md)**.

## Executive Architectural Summary

DBTRAC (**Database Tracking for Reliability, Administration and Compliance**) is a unified operational reliability platform engineered for National Medical Stores (NMS). It provides centralized SLA tracking, automated telemetry ingestion, storage runway forecasting, and compliance auditing across:
1. **Enterprise Database Platforms:**
   - **Oracle Database 19c Enterprise/Standard** (`nmsinfra.nms.go.ug` & `nmsebs.nms.go.ug`)
   - **MySQL 8.x** (`nickledbnew` replicated to `nickledbdr`)
   - **Microsoft SQL Server 2016+** (`Pentana` audit database)
2. **Virtualization Hypervisor Fleet:**
   - **OLKVM Cluster** (Oracle Linux KVM running core ERP DB & EBS tier)
   - **Proxmox VE Cluster** (`pvenode1`, `pvenode2`, `pvenode3`)

---

## Core Technical Layers

* **Frontend / Presentation Layer:** Server-side rendered Django Templates with clean typographic tables, responsive cardless KPI panels, inline SVG trend charts, and CSS `@media print` executive views.
* **Application & Business Logic Layer:** Python 3.11+ running Django 5.x with custom calculation engines:
  - `api.sla_utils.calculate_operational_downtime`: Evaluates downtime within defined operating windows (Oracle: 18h/d $\times$ 7d/w; MySQL/MSSQL: 9h/d $\times$ 5d/w; Infrastructure: 24/7/365 continuous), automatically excluding Uganda statutory public holidays.
  - `_calculate_runway`: Multi-tier storage runway and headroom forecasting engine.
  - Staging Triage Engine: Automated ping alert buffer detecting intermittent blips ($< 15\text{m}$) and decoupling raw monitoring probes from production SLA registers.
* **ETL & Telemetry Scraper Layer:**
  - Automated Prometheus scraper querying Node Exporters on port 9100.
  - Authenticated REST Ingestion endpoints (`X-API-KEY` secured).
* **Persistence Layer:** MySQL 8.x Enterprise InnoDB store with connection pooling and relational audit trails (`IncidentChangeLog`).
* **Edge Proxy:** Nginx terminating HTTP/HTTPS and proxying to Gunicorn pre-fork WSGI workers on port 8080/90.

---

For the full 25-section architectural specification, data schemas, mathematical equations, and operational runbook, refer to:
* **[docs/DBTRAC_SYSTEM_DESIGN_DOCUMENT_v2.0.md](file:///c:/Users/Ashley.atim/Downloads/DBA_Uptime_tracker-main/DBA_Uptime_tracker-main/docs/DBTRAC_SYSTEM_DESIGN_DOCUMENT_v2.0.md)**
* **[C:\Users\Ashley.atim\Downloads\DBTRAC_System_Design_Document_v2.0.md](file:///C:/Users/Ashley.atim/Downloads/DBTRAC_System_Design_Document_v2.0.md)**
