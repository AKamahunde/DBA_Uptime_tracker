<#
.SYNOPSIS
    MS SQL Server and Windows OS Monitoring Agent for Uptime Tracker.
.DESCRIPTION
    This script collects MS SQL Server backup logs, active user connections,
    and Windows Server hardware utilization (CPU, RAM, Disk), packaging them
    into JSON and posting them to the Uptime Tracker web API.
.NOTES
    Run this script via Windows Task Scheduler as an Administrator.
    Uses Integrated Security to avoid hardcoding database passwords.
#>

param(
    [switch]$Test,
    [switch]$Prod,
    [string]$Url = ""
)

# ============================================================
# CONFIGURATION
# ============================================================
$SqlServer      = "localhost"                           # SQL Server Instance Name (e.g. localhost or localhost\SQLEXPRESS)
$ApiKey         = if ($env:REPLICATION_API_KEY) { $env:REPLICATION_API_KEY } else { "uptimetracker_api_9f2c1d8e45x9" }
$ApiBaseUrl     = "http://192.168.35.38:90/api"         # Uptime Tracker base API URL
$Hostname       = "Pentana"                             # Server name registered in dashboard
$DbNameFilter   = "%Live%"                              # SQL LIKE filter to only track databases containing "Live"

$TargetMode = "prod"
if ($Url) {
    $TargetMode = "custom"
} elseif ($Test) {
    $TargetMode = "test"
} elseif ($Prod) {
    $TargetMode = "prod"
} elseif ($env:DBTRAC_ENV -and $env:DBTRAC_ENV.ToLower() -in @("test", "testing")) {
    $TargetMode = "test"
}

# ============================================================
# Helper: Send Data to API
# ============================================================
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Send-PayloadToApi {
    param (
        [string]$Endpoint,
        [hashtable]$Payload
    )
    
    $json = ConvertTo-Json $Payload -Depth 10
    $headers = @{
        "Content-Type" = "application/json"
        "X-API-KEY"    = $ApiKey
    }
    if ($TargetMode -eq "custom") {
        $targetBases = @($Url)
    } elseif ($TargetMode -eq "test") {
        $targetBases = @(
            "http://dbtractest.nms.go.ug:91/api",
            "http://192.168.35.38:91/api"
        )
    } else {
        $targetBases = @(
            $ApiBaseUrl,
            "http://dbtrac.nms.go.ug:90/api",
            "https://dbtrac.nms.go.ug/api",
            "http://dbtrac.nms.go.ug/api"
        ) | Select-Object -Unique
    }
    foreach ($base in $targetBases) {
        $url = "$($base.TrimEnd('/'))/$($Endpoint.TrimStart('/'))"
        Write-Host "Sending payload to $url..."
        try {
            $response = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $json -UseBasicParsing -TimeoutSec 10
            Write-Host "[$url] Success: $($response | ConvertTo-Json -Compress)"
            break
        }
        catch {
            $statusCode = $null
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            Write-Warning "[$url] Unreachable ($($_.Exception.Message))"
            if ($statusCode -in @(400, 401, 403)) {
                break
            }
        }
    }
}

# ============================================================
# PHASE 1: Collect Backup Records (State File Dedup)
# ============================================================
Write-Host "Checking backup tracking state file..."
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = "C:\Monitoring" }
if (-not (Test-Path $ScriptDir)) { New-Item -ItemType Directory -Path $ScriptDir -Force | Out-Null }
$StateFile = Join-Path $ScriptDir "last_backup_time.txt"

# Default to querying last 8 days to catch the latest weekly backup if no state file exists
$LastBackupTimeStr = (Get-Date).AddDays(-8).ToString("yyyy-MM-dd HH:mm:ss")
if (Test-Path $StateFile) {
    $LastBackupTimeStr = Get-Content $StateFile -First 1
}
Write-Host "Querying backups finished after: $LastBackupTimeStr"

$backups = @()
$connectionString = "Server=$SqlServer;Database=msdb;Integrated Security=True;TrustServerCertificate=True"
$connection = New-Object System.Data.SqlClient.SqlConnection($connectionString)

$backupQuery = @"
SELECT 
    bs.database_name AS database_instance,
    bs.backup_start_date AS start_time,
    bs.backup_finish_date AS end_time,
    DATEDIFF(minute, bs.backup_start_date, bs.backup_finish_date) AS duration_mins,
    CASE bs.type 
        WHEN 'D' THEN 'Full'
        WHEN 'I' THEN 'Incremental'
        WHEN 'L' THEN 'Incremental' -- Maps transactional log to Incremental for choice matching
        ELSE 'Incremental'
    END AS backup_type,
    bmf.physical_device_name AS location,
    'Success' AS outcome,
    CASE WHEN bs.has_backup_checksums = 1 THEN 1 ELSE 0 END AS verified,
    CASE WHEN bs.compressed_backup_size IS NOT NULL THEN 1 ELSE 0 END AS compression,
    bs.backup_size AS backup_size_bytes
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf ON bs.media_set_id = bmf.media_set_id
WHERE bs.backup_finish_date > @LastBackupTime
  AND bs.database_name LIKE @DbNameFilter
ORDER BY bs.backup_finish_date ASC;
"@

$perfStateFile = Join-Path $ScriptDir "mssql_perf_state.json"

$perfQuery = @"
DECLARE @cfgMax INT = 0;
SELECT @cfgMax = CAST(value_in_use AS INT) FROM sys.configurations WHERE name = 'user connections';
DECLARE @maxConn INT = CASE WHEN @cfgMax > 0 THEN @cfgMax ELSE @@MAX_CONNECTIONS END;

DECLARE @connCount INT = (SELECT COUNT(session_id) FROM sys.dm_exec_sessions WHERE is_user_process = 1);
DECLARE @activeReq INT = (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE session_id > 50 AND status <> 'background');
DECLARE @blockedReq INT = (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id <> 0);
DECLARE @longReq INT = (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE session_id > 50 AND status <> 'background' AND total_elapsed_time > 10000);

DECLARE @hitRatio DECIMAL(5,2) = 100.00;
SELECT @hitRatio = COALESCE(CAST(
    MAX(CASE WHEN counter_name = 'Buffer cache hit ratio' THEN CAST(cntr_value AS FLOAT) END) * 100.0 /
    NULLIF(MAX(CASE WHEN counter_name = 'Buffer cache hit ratio base' THEN CAST(cntr_value AS FLOAT) END), 0)
    AS DECIMAL(5,2)), 100.00)
FROM sys.dm_os_performance_counters
WHERE object_name LIKE '%Buffer Manager%'
  AND counter_name IN ('Buffer cache hit ratio', 'Buffer cache hit ratio base');

DECLARE @deadlocksTot BIGINT = 0;
DECLARE @lockTimeoutsTot BIGINT = 0;
SELECT @deadlocksTot = COALESCE(SUM(CAST(cntr_value AS BIGINT)), 0)
FROM sys.dm_os_performance_counters
WHERE counter_name = 'Number of Deadlocks/sec' AND instance_name = '_Total';
SELECT @lockTimeoutsTot = COALESCE(SUM(CAST(cntr_value AS BIGINT)), 0)
FROM sys.dm_os_performance_counters
WHERE counter_name = 'Lock Timeouts/sec' AND instance_name = '_Total';

DECLARE @logUsedPct DECIMAL(5,2) = 0.00;
CREATE TABLE #LogSpace (DbName SYSNAME, LogSizeMB FLOAT, LogSpaceUsedPct FLOAT, Status INT);
INSERT INTO #LogSpace EXEC('DBCC SQLPERF(LOGSPACE)');
SELECT @logUsedPct = COALESCE(CAST(MAX(LogSpaceUsedPct) AS DECIMAL(5,2)), 0.00)
FROM #LogSpace
WHERE DbName LIKE @DbNameFilter;
DROP TABLE #LogSpace;

SELECT
    @connCount AS conn_count,
    @maxConn AS max_conn,
    @activeReq AS active_req,
    @blockedReq AS blocked_req,
    @longReq AS long_req,
    @hitRatio AS hit_ratio,
    @deadlocksTot AS deadlocks_tot,
    @lockTimeoutsTot AS lock_timeouts_tot,
    @logUsedPct AS log_used_pct;
"@

$connectionsCount = 0
$maxConnections = 0
$threadsCount = 0
$blockedCount = 0
$longQueryCount = 0
$cacheHitPct = 100.00
$deadlocksDelta = 0
$lockTimeoutsDelta = 0
$logUsedPct = 0.00
$maxBackupTime = [DateTime]$LastBackupTimeStr

try {
    $connection.Open()

    # 1. Fetch Real-Time SQL Server Performance & Saturation Stats
    $cmdPerf = New-Object System.Data.SqlClient.SqlCommand($perfQuery, $connection)
    $cmdPerf.Parameters.AddWithValue("@DbNameFilter", $DbNameFilter) | Out-Null
    $perfReader = $cmdPerf.ExecuteReader()
    if ($perfReader.Read()) {
        $connectionsCount = [int]$perfReader["conn_count"]
        $maxConnections = [int]$perfReader["max_conn"]
        $threadsCount = [int]$perfReader["active_req"]
        $blockedCount = [int]$perfReader["blocked_req"]
        $longQueryCount = [int]$perfReader["long_req"]
        $cacheHitPct = [Math]::Round([Math]::Min(100.0, [Math]::Max(0.0, [double]$perfReader["hit_ratio"])), 2)
        $logUsedPct = [Math]::Round([Math]::Min(100.0, [Math]::Max(0.0, [double]$perfReader["log_used_pct"])), 2)

        $currDeadlocks = [int64]$perfReader["deadlocks_tot"]
        $currLockTimeouts = [int64]$perfReader["lock_timeouts_tot"]
        $prevPerf = $null
        if (Test-Path $perfStateFile) {
            try { $prevPerf = Get-Content $perfStateFile -Raw | ConvertFrom-Json } catch { $prevPerf = $null }
        }
        if ($prevPerf) {
            $pDl = [int64]$prevPerf.deadlocks_tot
            $pLt = [int64]$prevPerf.lock_timeouts_tot
            $deadlocksDelta = if ($currDeadlocks -ge $pDl) { [int]($currDeadlocks - $pDl) } else { [int]$currDeadlocks }
            $lockTimeoutsDelta = if ($currLockTimeouts -ge $pLt) { [int]($currLockTimeouts - $pLt) } else { [int]$currLockTimeouts }
        }
        @{
            deadlocks_tot = $currDeadlocks
            lock_timeouts_tot = $currLockTimeouts
            updated_at = (Get-Date).ToString("o")
        } | ConvertTo-Json | Out-File $perfStateFile -Encoding utf8 -Force
    }
    $perfReader.Close()

    Write-Host "[MSSQL PERF] Conn: $connectionsCount/$maxConnections | ActiveReq: $threadsCount | Blocked: $blockedCount | Long(>10s): $longQueryCount | Deadlocks(d): $deadlocksDelta | LockTimeouts(d): $lockTimeoutsDelta | CacheHit: $cacheHitPct% | TxLog: $logUsedPct%"

    # 3. Fetch Backup Sets
    $cmdBackup = New-Object System.Data.SqlClient.SqlCommand($backupQuery, $connection)
    $cmdBackup.Parameters.AddWithValue("@LastBackupTime", [DateTime]$LastBackupTimeStr) | Out-Null
    $cmdBackup.Parameters.AddWithValue("@DbNameFilter", $DbNameFilter) | Out-Null
    $reader = $cmdBackup.ExecuteReader()
    
    while ($reader.Read()) {
        $sizeGb = [Math]::Round($reader["backup_size_bytes"] / 1GB, 3)
        $sizeStr = "$sizeGb GB"
        
        $rawLoc = $reader["location"].ToString()
        $fileName = $rawLoc
        if ($rawLoc -like "*\*") {
            $fileName = Split-Path -Leaf $rawLoc
        }
        $backupUsed = "$fileName ($sizeStr)"
        if ($backupUsed.Length -gt 255) {
            $backupUsed = $backupUsed.Substring(0, 255)
        }
        
        $startTime = [DateTime]$reader["start_time"]
        $endTime = [DateTime]$reader["end_time"]
        
        if ($endTime -gt $maxBackupTime) {
            $maxBackupTime = $endTime
        }
        
        $backupPayload = @{
            database          = "MS SQL Server"
            database_instance = $reader["database_instance"].ToString()
            backup_type       = $reader["backup_type"].ToString()
            backup_used       = $backupUsed
            start             = $startTime.ToString("yyyy-MM-ddTHH:mm:ssK")
            end               = $endTime.ToString("yyyy-MM-ddTHH:mm:ssK")
            duration_mins     = [int]$reader["duration_mins"]
            outcome           = $reader["outcome"].ToString()
            failure_reason    = ""
            location          = $reader["location"].ToString()
            verified          = [bool]($reader["verified"] -eq 1)
            compression       = [bool]($reader["compression"] -eq 1)
        }
        $backups += $backupPayload
    }
    $reader.Close()
}
catch {
    Write-Error "Database connection or query failed: $_"
}
finally {
    $connection.Close()
}

# Send backups if any found
if ($backups.Count -gt 0) {
    Write-Host "Found $($backups.Count) new backup logs. Ingesting..."
    foreach ($bk in $backups) {
        Send-PayloadToApi -Endpoint "backup/ingest/" -Payload $bk
    }
    # Persist the latest backup end time so we don't ingest these again
    $maxBackupTime.ToString("yyyy-MM-dd HH:mm:ss") | Out-File $StateFile -Force
    Write-Host "Updated state file to: $($maxBackupTime.ToString('yyyy-MM-dd HH:mm:ss'))"
} else {
    Write-Host "No new backups found since last run."
}

# ============================================================
# PHASE 2: Collect Windows Server Resource Health
# ============================================================
Write-Host "Collecting Server Resource Health..."

# 1. CPU Utilization (Average load across processors)
$cpuLoad = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average | Select-Object -ExpandProperty Average
if ($null -eq $cpuLoad) { $cpuLoad = 0 }
$cpuPercent = [Math]::Round($cpuLoad, 2)

# 2. RAM Utilization
$os = Get-CimInstance Win32_OperatingSystem
$totalRam = $os.TotalVisibleMemorySize
$freeRam = $os.FreePhysicalMemory
$ramTotalGb = [Math]::Round(($totalRam * 1024.0) / 1GB, 2)
$ramUsedGb = [Math]::Round((($totalRam - $freeRam) * 1024.0) / 1GB, 2)
$ramPercent = [Math]::Round((($totalRam - $freeRam) / $totalRam) * 100, 2)
$logicalCpus = 0
try { $logicalCpus = [int](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors } catch { $logicalCpus = 0 }

# 3. Disk Utilization (C:\ drive)
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$diskTotalGb = [Math]::Round($drive.Size / 1GB, 2)
$diskUsedGb = [Math]::Round(($drive.Size - $drive.FreeSpace) / 1GB, 2)
$diskPercent = [Math]::Round((($drive.Size - $drive.FreeSpace) / $drive.Size) * 100, 2)

# 4. OS Uptime (Days & Availability)
$bootTime = $os.LastBootUpTime
$uptimeDays = 0.00
if ($bootTime) {
    $uptimeSpan = (Get-Date) - $bootTime
    $uptimeDays = [Math]::Round($uptimeSpan.TotalDays, 2)
}

# Create Server Health Payload
$healthPayload = @{
    server               = $Hostname
    entered_by           = "Script"
    logical_cpus         = $logicalCpus
    cpu_percent          = $cpuPercent
    memory_percent       = $ramPercent
    memory_total_gb      = $ramTotalGb
    memory_used_gb       = $ramUsedGb
    disk_percent         = $diskPercent
    disk_3_type          = "OS Volume (C:)"
    disk_3_size_gb       = $diskTotalGb
    disk_3_used_gb       = $diskUsedGb
    disk_3_percent       = $diskPercent
    tablespace_percent   = $logUsedPct
    connections          = $connectionsCount
    max_connections      = $maxConnections
    slow_queries         = $deadlocksDelta
    threads              = $threadsCount
    blocked_queries      = $blockedCount
    long_running_queries = $longQueryCount
    cache_hit_percent    = $cacheHitPct
    aborted_connections  = $lockTimeoutsDelta
    database             = "MS SQL Server"
    uptime_days          = $uptimeDays
    uptime_percent       = 100.00
}

Write-Host "Sending Server Health Stats..."
Send-PayloadToApi -Endpoint "health/ingest/" -Payload $healthPayload

# ============================================================
# PHASE 3: Collect Monthly Capacity Planning Stats
# ============================================================
$CapacityStateFile = Join-Path $ScriptDir "last_capacity_run.txt"
$CurrentMonth = (Get-Date).ToString("yyyy-MM")
$LastCapacityRun = ""
if (Test-Path $CapacityStateFile) {
    $LastCapacityRun = Get-Content $CapacityStateFile -First 1
}

if ($CurrentMonth -ne $LastCapacityRun) {
    Write-Host "Running monthly capacity tracking for $CurrentMonth..."
    $capacityQuery = @"
DECLARE @DbName NVARCHAR(256);
SELECT TOP 1 @DbName = name FROM sys.databases WHERE name LIKE @DbNameFilter AND database_id > 4 ORDER BY name;

IF @DbName IS NOT NULL
BEGIN
    DECLARE @SizeGb DECIMAL(18,2);
    SELECT @SizeGb = SUM(size * 8.0 / 1024 / 1024) FROM sys.master_files WHERE database_id = DB_ID(@DbName);

    DECLARE @LargestTable NVARCHAR(256) = 'Unknown';
    DECLARE @LargestTablePages BIGINT = 0;
    DECLARE @SQL NVARCHAR(MAX);
    SET @SQL = N'
        SELECT TOP 1 @TableOut = t.name, @PagesOut = SUM(a.total_pages)
        FROM [' + @DbName + '].sys.tables t
        INNER JOIN [' + @DbName + '].sys.indexes i ON t.object_id = i.object_id
        INNER JOIN [' + @DbName + '].sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
        INNER JOIN [' + @DbName + '].sys.allocation_units a ON p.partition_id = a.container_id
        GROUP BY t.name
        ORDER BY SUM(a.total_pages) DESC;';
        
    EXEC sp_executesql @SQL, N'@TableOut NVARCHAR(256) OUTPUT, @PagesOut BIGINT OUTPUT', @TableOut = @LargestTable OUTPUT, @PagesOut = @LargestTablePages OUTPUT;
    
    IF @LargestTable <> 'Unknown'
    BEGIN
        DECLARE @TableSizeMb DECIMAL(18,2) = (@LargestTablePages * 8.0) / 1024.0;
        IF @TableSizeMb >= 102.4
        BEGIN
            DECLARE @TableSizeGb DECIMAL(18,2) = @TableSizeMb / 1024.0;
            SET @LargestTable = @LargestTable + ' (' + CAST(@TableSizeGb AS VARCHAR(50)) + ' GB)';
        END
        ELSE
        BEGIN
            SET @LargestTable = @LargestTable + ' (' + CAST(@TableSizeMb AS VARCHAR(50)) + ' MB)';
        END
    END
    
    SELECT @DbName AS db_name, @SizeGb AS size_gb, @LargestTable AS largest_table;
END
"@
    
    $connection = New-Object System.Data.SqlClient.SqlConnection($connectionString)
    try {
        $connection.Open()
        $cmdCapacity = New-Object System.Data.SqlClient.SqlCommand($capacityQuery, $connection)
        $cmdCapacity.Parameters.AddWithValue("@DbNameFilter", $DbNameFilter) | Out-Null
        $reader = $cmdCapacity.ExecuteReader()
        
        if ($reader.Read()) {
            $dbNameActual = $reader["db_name"].ToString()
            $dbSizeGb = [double]$reader["size_gb"]
            $largestTable = $reader["largest_table"].ToString()
            
            $availableCapacityGb = [Math]::Round($drive.FreeSpace / 1GB, 2)
            
            $capacityPayload = @{
                database              = "MS SQL Server"
                database_size_gb      = $dbSizeGb
                disk_percent          = $diskPercent
                tablespace_percent    = $logUsedPct
                available_capacity_gb = $availableCapacityGb
                largest_table         = $largestTable
            }
            
            Write-Host "Ingesting Capacity metrics for $dbNameActual ($dbSizeGb GB, Largest Table: $largestTable)..."
            Send-PayloadToApi -Endpoint "capacity/ingest/" -Payload $capacityPayload
            
            # Save run state
            $CurrentMonth | Out-File $CapacityStateFile -Force
            Write-Host "Updated capacity state file to: $CurrentMonth"
        }
        $reader.Close()
    }
    catch {
        Write-Error "Capacity planning query failed: $_"
    }
    finally {
        $connection.Close()
    }
} else {
    Write-Host "Capacity tracking already completed for this month ($CurrentMonth)."
}

Write-Host "Collector run completed successfully."
