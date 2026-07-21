# Atlas (formerly DBA Operations Tracker)

A robust Django web application for tracking database administration operations. Atlas acts as the central heartbeat monitor for your infrastructure, tracking incidents, replication health, backup recovery, server capacity, and scheduled maintenance.

## Features

- **Dashboard**: Overview and analytics of all data records.
- **Incident Register**: Track and manage database incidents.
- **Replication Monitor**: Monitor database replication status.
- **Backup & Recovery**: Track backup and recovery operations.
- **Server Health**: Monitor server performance metrics.
- **Maintenance Register**: Track maintenance activities.
- **Capacity Planning**: Plan database capacity requirements.
- **Availability Calculator**: Calculate system availability metrics.

## User Roles & Security

Atlas implements a strict security and permissioning model:
- **Admin Users**: Full access to view, create, edit, and safely delete all records.
- **Regular Users (Data Entrants)**: Can create new records, but can only edit them within a strict **5-minute window** of creation. They cannot delete records.
- **Permission Walls**: Users are strictly blocked from accessing views they lack permissions for via soft-error redirects and hidden UI elements.

## Architecture & Integration (ETL)

Atlas is designed to act not just as a manual data entry tool, but as a centralized operations hub. 
- **ETL Scaffolding**: The `etl/` Django app provides a structured foundation for integrating with external REST APIs and Kafka message streams to automate data ingestion from other databases and monitoring tools.
- **Documentation**: See `docs/architecture.md` for a deeper dive into the system's design.

## Installation

1. **Create virtual environment**:
   ```bash
   python -m venv venv
   ```

2. **Activate virtual environment**:
   - Windows: `venv\Scripts\activate`
   - Linux/Mac: `source venv/bin/activate`

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure database connection**:
   Edit `dba_tracker/settings.py` and update the DATABASES section with your MySQL credentials.

5. **Run migrations**:
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

6. **Create superuser**:
   ```bash
   python manage.py createsuperuser
   ```

7. **Run development server**:
   ```bash
   python manage.py runserver
   ```

## Development & Version Control

- The project uses Git for version control. A comprehensive `.gitignore` is provided.
- **Note on ETL**: To utilize Kafka integrations, ensure you have Kafka running locally or configure connection strings in the ETL settings block.
