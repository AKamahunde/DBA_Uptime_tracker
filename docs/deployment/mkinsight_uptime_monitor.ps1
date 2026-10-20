<#
.SYNOPSIS
    MKInsight Audit Framework & MS SQL Server Uptime Monitor for DBTRAC.
.DESCRIPTION
    Scans Windows Event Logs, SQL Server engine states, and live application
    connectivity on the Windows host (Pentana) hosting MKInsight and MS SQL Server:
    1. System reboots, kernel crashes, dirty power cuts (Event IDs 6008, 41, 1074, 6005, 6006).
    2. SQL Server service state transitions, unexpected terminations, and crash recovery (Event IDs 7036, 7034, 7031, 17147, 17148).
    3. Live database and TCP connectivity probes with active outage state tracking.
    Calculates exact downtime and POSTs incidents to DBTRAC /api/incident/ingest/.
.NOTES
    Runs on Windows Server via Windows Task Scheduler (e.g. every 5 minutes)
    or continuously in daemon mode (-Daemon).
#>

[CmdletBinding()]
param (
    [string]$ApiBaseUrl        = "http://192.168.35.38:90/api",
    [string]$ApiKey            = "uptimetracker_api_9f2c1d8e45x9",
    [string]$SqlServer         = "localhost",
    [int]$SqlPort              = 1433,
    [string]$ServiceName       = "MSSQLSERVER",
    [string]$Hostname          = "Pentana",
    [string]$DbNameFilter      = "%Live%",
    [int]$LookbackHours        = 48,
    [switch]$Daemon            = $false,
    [int]$IntervalSec          = 60,
    [switch]$DryRun            = $false,
    [string]$StateDirectory    = "C:\Monitoring"
)

# Ensure TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Create State Directory if missing
if (-not (Test-Path $StateDirectory)) {
    try {
        New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
    } catch {
        $StateDirectory = [System.IO.Path]::GetTempPath()
    }
}

$EventStateFile = Join-Path $StateDirectory "mkinsight_last_event_time.txt"
$ActiveProbeFile = Join-Path $StateDirectory "mkinsight_active_probe.json"

Write-Host "=========================================================="
Write-Host " MKInsight & MS SQL Server Uptime Monitor - Version 2.0.0"
Write-Host " Target Host: $Hostname | SQL Server: $SqlServer"
Write-Host " Ingestion API: $ApiBaseUrl/incident/ingest/"
Write-Host "=========================================================="

# ============================================================
# Helper: Send Incident Payload to DBTRAC API
# ============================================================
function Send-IncidentToApi {
    param (
        [hashtable]$Payload
    )

    $endpoint = "$ApiBaseUrl/incident/ingest/"
    $json = ConvertTo-Json $Payload -Depth 5

    if ($DryRun) {
        Write-Host "[DRY RUN] Would send payload to $endpoint:" -ForegroundColor Yellow
        Write-Host $json
        return $true
    }

    $headers = @{
        "Content-Type" = "application/json"
        "X-API-KEY"    = $ApiKey
    }

    try {
        $response = Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -Body $json -UseBasicParsing -TimeoutSec 15
        if ($response.status -eq 'success') {
            Write-Host "✓ Logged to DBTRAC Staging: ID $($response.id) ($($Payload.start) to $($Payload.end))" -ForegroundColor Green
            return $true
        } elseif ($response.status -eq 'ignored') {
            Write-Host "✓ Already Staged / Deduplicated: $($Payload.start)" -ForegroundColor Cyan
            return $true
        } else {
            Write-Host "API Response: $($response | ConvertTo-Json -Compress)" -ForegroundColor Yellow
            return $true
        }
    }
    catch {
        Write-Error "Failed to POST incident starting at $($Payload.start): $_"
        return $false
    }
}

# ============================================================
# Helper: Format Meaningful DBA Summary
# ============================================================
function Get-DowntimeSummary {
    param (
        [string]$OutageType,
        [string]$Details
    )

    switch ($OutageType) {
        "Power Outage / Abrupt Crash" {
            return @"
DBA Summary: [CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]
The Windows Server host '$Hostname' suffered an abrupt power cut or hardware/kernel crash without graceful shutdown (Windows Event ID 6008).
Power was subsequently restored and the system booted, restoring SQL Server and the MKInsight Audit Framework.
"@
        }
        "Clean Maintenance Reboot" {
            return @"
DBA Summary: [MAINTENANCE - CLEAN RESTART]
A system administrator or automated patch script initiated a graceful shutdown and reboot of the Windows Server host '$Hostname' (Windows Event ID 1074).
The system completed startup and restored all database services.
"@
        }
        "SQL Server Crash" {
            return @"
DBA Summary: [CRITICAL OUTAGE - SQL SERVER CRASH / TERMINATION]
The MS SQL Server engine process ($ServiceName) terminated unexpectedly or was killed (Windows Event ID 7034).
The database service automatically restarted or was brought back online by the DBA.
"@
        }
        "SQL Server Service Stop" {
            return @"
DBA Summary: [SERVICE OUTAGE - SQL SERVER STOPPED]
The MS SQL Server database service entered the STOPPED state (Windows Event ID 7036).
MKInsight audit operations were paused until the database engine was restarted.
"@
        }
        "Database Probe Failure" {
            return @"
DBA Summary: [CRITICAL OUTAGE - DATABASE CONNECTIVITY FAILED]
Active TCP port 1433 or SQL instance probe failed to establish a connection to SQL Server on $SqlServer.
The database engine was unresponsive to client requests.
"@
        }
        default {
            return @"
DBA Summary: [OUTAGE - SERVICE INTERRUPTION]
An interruption occurred on $Hostname affecting MS SQL Server / MKInsight Audit Framework.
"@
        }
    }
}

# ============================================================
# 1. Detect System Boot, Crash & Power Outages via Event Log
# ============================================================
function Detect-BootAndPowerOutages {
    param (
        [DateTime]$CutoffTime
    )

    $detected = @()

    # Query Event ID 6008 (Unexpected Shutdown) in System log
    try {
        $filter6008 = @{
            LogName   = 'System'
            Id        = 6008
            StartTime = $CutoffTime
        }
        $unexpectedEvents = Get-WinEvent -FilterHashtable $filter6008 -ErrorAction SilentlyContinue

        if ($unexpectedEvents) {
            foreach ($evt in $unexpectedEvents) {
                # Event 6008 TimeCreated is when the machine logged the event (at boot)
                $bootTime = $evt.TimeCreated
                
                # The message contains the exact crash time: "The previous system shutdown at <time> on <date> was unexpected."
                $crashTime = $null
                if ($evt.Properties.Count -ge 2) {
                    # Properties[1] is date, Properties[0] is time
                    $timeStr = "$($evt.Properties[1].Value) $($evt.Properties[0].Value)"
                    if ([DateTime]::TryParse($timeStr, [ref]$crashTime)) {
                        # Parsed correctly
                    }
                }
                
                if (-not $crashTime) {
                    # Fallback: estimate 5 minutes prior to boot if time parsing fails
                    $crashTime = $bootTime.AddMinutes(-5)
                }

                $durationMins = [Math]::Round(($bootTime - $crashTime).TotalMinutes, 1)
                $summary = Get-DowntimeSummary -OutageType "Power Outage / Abrupt Crash"

                $detected += @{
                    start      = $crashTime.ToString("yyyy-MM-dd HH:mm:ss")
                    end        = $bootTime.ToString("yyyy-MM-dd HH:mm:ss")
                    database   = "MS SQL Server"
                    system     = "MKInsight (Pentana)"
                    root_cause = @"
AUTO-LOGGED: Power Outage / Abrupt System Crash detected.

$summary

Evidence:
Windows Event ID: 6008 (EventLog - Unexpected Shutdown)
Host: $Hostname
Unexpected Shutdown Detected: $($crashTime.ToString("yyyy-MM-dd HH:mm:ss"))
System Boot Completed: $($bootTime.ToString("yyyy-MM-dd HH:mm:ss"))
Calculated Downtime: $durationMins minutes
"@
                }
            }
        }
    }
    catch {
        Write-Warning "Failed to query Event 6008: $_"
    }

    # Query Event ID 1074 (Clean Shutdown / Reboot)
    try {
        $filter1074 = @{
            LogName   = 'System'
            Id        = 1074
            StartTime = $CutoffTime
        }
        $cleanEvents = Get-WinEvent -FilterHashtable $filter1074 -ErrorAction SilentlyContinue

        if ($cleanEvents) {
            # Find matching subsequent boot events (Event 6005)
            $filter6005 = @{
                LogName   = 'System'
                Id        = 6005
                StartTime = $CutoffTime
            }
            $bootEvents = Get-WinEvent -FilterHashtable $filter6005 -ErrorAction SilentlyContinue | Sort-Object TimeCreated

            foreach ($evt in $cleanEvents) {
                $shutTime = $evt.TimeCreated
                $reason = $evt.Message
                if ($reason.Length -gt 300) { $reason = $reason.Substring(0, 300) + "..." }

                # Find the earliest boot that occurred AFTER this shutdown
                $nextBoot = $bootEvents | Where-Object { $_.TimeCreated -gt $shutTime } | Select-Object -First 1

                if ($nextBoot) {
                    $upTime = $nextBoot.TimeCreated
                    $durationMins = [Math]::Round(($upTime - $shutTime).TotalMinutes, 1)

                    if ($durationMins -gt 0.5) {
                        $summary = Get-DowntimeSummary -OutageType "Clean Maintenance Reboot"

                        $detected += @{
                            start      = $shutTime.ToString("yyyy-MM-dd HH:mm:ss")
                            end        = $upTime.ToString("yyyy-MM-dd HH:mm:ss")
                            database   = "MS SQL Server"
                            system     = "MKInsight (Pentana)"
                            root_cause = @"
AUTO-LOGGED: Clean System Reboot detected.

$summary

Evidence:
Windows Event ID: 1074 (USER32 Shutdown) -> 6005 (EventLog Startup)
Host: $Hostname
Shutdown Initiated: $($shutTime.ToString("yyyy-MM-dd HH:mm:ss"))
System Reboot Restored: $($upTime.ToString("yyyy-MM-dd HH:mm:ss"))
Shutdown Details: $reason
Calculated Downtime: $durationMins minutes
"@
                        }
                    }
                }
            }
        }
    }
    catch {
        Write-Warning "Failed to query Event 1074: $_"
    }

    return $detected
}

# ============================================================
# 2. Detect SQL Server Service Terminations & Crashes
# ============================================================
function Detect-SqlServerServiceOutages {
    param (
        [DateTime]$CutoffTime
    )

    $detected = @()

    # 1. Event ID 7034 (Service Terminated Unexpectedly)
    try {
        $filter7034 = @{
            LogName      = 'System'
            ProviderName = 'Service Control Manager'
            Id           = 7034
            StartTime    = $CutoffTime
        }
        $crashEvents = Get-WinEvent -FilterHashtable $filter7034 -ErrorAction SilentlyContinue

        if ($crashEvents) {
            # Find running events (7036) for SQL Server
            $filter7036 = @{
                LogName      = 'System'
                ProviderName = 'Service Control Manager'
                Id           = 7036
                StartTime    = $CutoffTime
            }
            $scmEvents = Get-WinEvent -FilterHashtable $filter7036 -ErrorAction SilentlyContinue | Sort-Object TimeCreated

            foreach ($evt in $crashEvents) {
                # Check if this crash was for MSSQLSERVER or MSSQL$*
                $svcParam = $evt.Properties[0].Value.ToString()
                if ($svcParam -like "*SQL Server*" -or $svcParam -like "*MSSQL*") {
                    $crashTime = $evt.TimeCreated
                    
                    # Find when it restarted
                    $restartEvt = $scmEvents | Where-Object { 
                        $_.TimeCreated -gt $crashTime -and 
                        $_.Properties[0].Value.ToString() -eq $svcParam -and 
                        $_.Properties[1].Value.ToString() -like "*running*" 
                    } | Select-Object -First 1

                    $endTime = if ($restartEvt) { $restartEvt.TimeCreated } else { (Get-Date) }
                    $durationMins = [Math]::Round(($endTime - $crashTime).TotalMinutes, 1)

                    $summary = Get-DowntimeSummary -OutageType "SQL Server Crash"

                    $detected += @{
                        start      = $crashTime.ToString("yyyy-MM-dd HH:mm:ss")
                        end        = $endTime.ToString("yyyy-MM-dd HH:mm:ss")
                        database   = "MS SQL Server"
                        system     = "MKInsight (Pentana)"
                        root_cause = @"
AUTO-LOGGED: SQL Server Service Crash Outage detected.

$summary

Evidence:
Windows Event ID: 7034 (Service Control Manager)
Service Name: $svcParam
Process Crashed / Terminated: $($crashTime.ToString("yyyy-MM-dd HH:mm:ss"))
Service Restored: $($endTime.ToString("yyyy-MM-dd HH:mm:ss"))
Calculated Downtime: $durationMins minutes
"@
                    }
                }
            }
        }
    }
    catch {
        Write-Warning "Failed to query Event 7034: $_"
    }

    # 2. Event ID 7036 (SQL Service Stopped -> Running)
    try {
        $filter7036 = @{
            LogName      = 'System'
            ProviderName = 'Service Control Manager'
            Id           = 7036
            StartTime    = $CutoffTime
        }
        $stateEvents = Get-WinEvent -FilterHashtable $filter7036 -ErrorAction SilentlyContinue | 
                       Where-Object { 
                           $svc = $_.Properties[0].Value.ToString()
                           $svc -like "*SQL Server ($ServiceName)*" -or $svc -like "*MSSQLSERVER*" -or $svc -like "*MSSQL$*"
                       } | Sort-Object TimeCreated

        if ($stateEvents) {
            $stopTime = $null
            $svcName = ""

            foreach ($evt in $stateEvents) {
                $name = $evt.Properties[0].Value.ToString()
                $state = $evt.Properties[1].Value.ToString().ToLower()

                if ($state -eq "stopped") {
                    $stopTime = $evt.TimeCreated
                    $svcName = $name
                } elseif ($state -eq "running" -and $stopTime) {
                    $startTime = $evt.TimeCreated
                    $durationMins = [Math]::Round(($startTime - $stopTime).TotalMinutes, 1)

                    if ($durationMins -gt 0.3) {
                        $summary = Get-DowntimeSummary -OutageType "SQL Server Service Stop"

                        $detected += @{
                            start      = $stopTime.ToString("yyyy-MM-dd HH:mm:ss")
                            end        = $startTime.ToString("yyyy-MM-dd HH:mm:ss")
                            database   = "MS SQL Server"
                            system     = "MKInsight (Pentana)"
                            root_cause = @"
AUTO-LOGGED: SQL Server Service Interruption detected.

$summary

Evidence:
Windows Event ID: 7036 (Service Control Manager)
Service: $svcName
Service Stopped: $($stopTime.ToString("yyyy-MM-dd HH:mm:ss"))
Service Running: $($startTime.ToString("yyyy-MM-dd HH:mm:ss"))
Calculated Downtime: $durationMins minutes
"@
                        }
                    }
                    $stopTime = $null
                }
            }
        }
    }
    catch {
        Write-Warning "Failed to query Event 7036: $_"
    }

    return $detected
}

# ============================================================
# 3. Active Real-Time Probe Check (Daemon / Scheduled Mode)
# ============================================================
function Check-ActiveProbeHealth {
    Write-Host "Running live connectivity check for MKInsight & MS SQL Server..."
    $isHealthy = $false
    $probeError = ""

    # Test SQL TCP Port Reachability
    $tcpCheck = $false
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $iar = $tcpClient.BeginConnect($SqlServer, $SqlPort, $null, $null)
        $wait = $iar.AsyncWaitHandle.WaitOne(3000, $false)
        if ($wait) {
            $tcpClient.EndConnect($iar)
            $tcpCheck = $true
            $tcpClient.Close()
        } else {
            $tcpClient.Close()
            $probeError = "TCP Port $SqlPort unreachable on $SqlServer."
        }
    } catch {
        $probeError = "TCP connect error: $_"
    }

    # Test SQL Query Execution
    if ($tcpCheck) {
        $connStr = "Server=$SqlServer;Database=master;Integrated Security=True;TrustServerCertificate=True;Connection Timeout=5"
        $conn = New-Object System.Data.SqlClient.SqlConnection($connStr)
        try {
            $conn.Open()
            $cmd = $conn.CreateCommand()
            $cmd.CommandText = "SELECT TOP 1 name, state_desc FROM sys.databases WHERE name LIKE '$DbNameFilter' OR name LIKE '%Pentana%' OR name LIKE '%MKInsight%';"
            $reader = $cmd.ExecuteReader()
            if ($reader.Read()) {
                $dbName = $reader["name"].ToString()
                $state = $reader["state_desc"].ToString()
                if ($state -eq "ONLINE") {
                    $isHealthy = $true
                    Write-Host "✓ SQL Server is ONLINE. Audit DB '$dbName' is ONLINE." -ForegroundColor Green
                } else {
                    $probeError = "Audit Database '$dbName' state is $state (Not ONLINE)."
                }
            } else {
                # Fallback check server version
                $cmd2 = $conn.CreateCommand()
                $cmd2.CommandText = "SELECT @@SERVERNAME, SERVERPROPERTY('ProductVersion');"
                $reader2 = $cmd2.ExecuteReader()
                if ($reader2.Read()) {
                    $isHealthy = $true
                    Write-Host "✓ SQL Server instance is responsive (master DB reachable)." -ForegroundColor Green
                }
                $reader2.Close()
            }
            $reader.Close()
        }
        catch {
            $probeError = "SQL Query failed: $_"
        }
        finally {
            $conn.Close()
        }
    }

    # Active Outage State Handling
    $now = Get-Date
    if (-not $isHealthy) {
        Write-Host "✗ PROBE FAILED: $probeError" -ForegroundColor Red
        if (-not (Test-Path $ActiveProbeFile)) {
            # Start of a new outage
            $activeState = @{
                start_time = $now.ToString("yyyy-MM-dd HH:mm:ss")
                error      = $probeError
            }
            $activeState | ConvertTo-Json | Out-File $ActiveProbeFile -Force
            Write-Host "Recorded active outage starting at: $($now.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor Yellow
        }
    } else {
        # Check if we are recovering from an active outage
        if (Test-Path $ActiveProbeFile) {
            try {
                $rawJson = Get-Content $ActiveProbeFile -Raw | ConvertFrom-Json
                $startTimeStr = $rawJson.start_time
                $origError = $rawJson.error
                $startDt = [DateTime]$startTimeStr

                $durationMins = [Math]::Round(($now - $startDt).TotalMinutes, 1)
                $summary = Get-DowntimeSummary -OutageType "Database Probe Failure"

                Write-Host "✓ Service RECOVERED! Total Downtime: $durationMins minutes. Ingesting incident..." -ForegroundColor Green

                $payload = @{
                    start      = $startDt.ToString("yyyy-MM-dd HH:mm:ss")
                    end        = $now.ToString("yyyy-MM-dd HH:mm:ss")
                    database   = "MS SQL Server"
                    system     = "MKInsight (Pentana)"
                    root_cause = @"
AUTO-LOGGED: MKInsight Database Unresponsive Outage detected.

$summary

Evidence:
Probe Target: $SqlServer:$SqlPort
Probe Failure Detected: $($startDt.ToString("yyyy-MM-dd HH:mm:ss"))
Recovery Confirmed: $($now.ToString("yyyy-MM-dd HH:mm:ss"))
Initial Failure Root Cause: $origError
Calculated Downtime: $durationMins minutes
"@
                }

                Send-IncidentToApi -Payload $payload
                Remove-Item $ActiveProbeFile -Force
            } catch {
                Write-Warning "Error resolving active outage state file: $_"
            }
        }
    }
}

# ============================================================
# Main Processing Run
# ============================================================
function Invoke-MonitorRun {
    $now = Get-Date
    $cutoff = $now.AddHours(-$LookbackHours)

    # Check last state file
    if (Test-Path $EventStateFile) {
        $lastSavedStr = Get-Content $EventStateFile -First 1
        $parsedLast = $null
        if ([DateTime]::TryParse($lastSavedStr, [ref]$parsedLast)) {
            if ($parsedLast -gt $cutoff) {
                $cutoff = $parsedLast
            }
        }
    }

    Write-Host "Scanning event logs for outages since: $($cutoff.ToString('yyyy-MM-dd HH:mm:ss'))..."

    $incidents = @()

    # 1. System Boots, Crashes & Power Cut Events
    $bootEvents = Detect-BootAndPowerOutages -CutoffTime $cutoff
    if ($bootEvents) {
        Write-Host "Found $($bootEvents.Count) system crash / reboot outage event(s)." -ForegroundColor Yellow
        $incidents += $bootEvents
    }

    # 2. SQL Server Service State Events
    $svcEvents = Detect-SqlServerServiceOutages -CutoffTime $cutoff
    if ($svcEvents) {
        Write-Host "Found $($svcEvents.Count) SQL Server service interruption event(s)." -ForegroundColor Yellow
        $incidents += $svcEvents
    }

    # 3. Active Real-Time Database Probe
    Check-ActiveProbeHealth

    # Deduplicate incidents by start timestamp
    $uniqueIncidents = @{}
    foreach ($inc in $incidents) {
        if (-not $uniqueIncidents.ContainsKey($inc.start)) {
            $uniqueIncidents[$inc.start] = $inc
        }
    }

    if ($uniqueIncidents.Count -gt 0) {
        Write-Host "Ingesting $($uniqueIncidents.Count) detected incident(s) to DBTRAC API..." -ForegroundColor Cyan
        $sentCount = 0
        foreach ($inc in $uniqueIncidents.Values) {
            $ok = Send-IncidentToApi -Payload $inc
            if ($ok) { $sentCount++ }
        }
        Write-Host "Ingestion complete: $sentCount incident(s) processed." -ForegroundColor Green
    } else {
        Write-Host "No new unlogged event log outages detected." -ForegroundColor Gray
    }

    # Update state file to current timestamp
    $now.ToString("yyyy-MM-dd HH:mm:ss") | Out-File $EventStateFile -Force
}

# ============================================================
# Execution Entry Point (One-Shot vs Daemon)
# ============================================================
if ($Daemon) {
    Write-Host "Starting MKInsight Uptime Monitor in DAEMON mode (Interval: $IntervalSec s)..." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to terminate."
    while ($true) {
        try {
            Invoke-MonitorRun
        } catch {
            Write-Error "Monitor run encountered an error: $_"
        }
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Invoke-MonitorRun
}

Write-Host "MKInsight Uptime Monitor run completed."
