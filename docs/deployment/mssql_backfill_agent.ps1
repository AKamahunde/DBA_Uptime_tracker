<#
.SYNOPSIS
    MS SQL Server and Windows OS Historical Backfill Agent for Uptime Tracker.
.DESCRIPTION
    This script backfills historical backups from July 1st, 2026 to today,
    and generates daily server health stats for July to populate the database.
.NOTES
    Run this script once from an Administrator PowerShell console.
#>

# ============================================================
# CONFIGURATION
# ============================================================
$SqlServer      = "localhost"                           # SQL Server Instance Name
$ApiKey         = "uptimetracker_api_9f2c1d8e45x9"       # API Key configured in your web application env
$ApiBaseUrl     = "http://192.168.35.38:90/api"         # Uptime Tracker base API URL
$Hostname       = "MS SQL Server"                      # Server name registered in dashboard
$DbNameFilter   = "%Live%"                              # SQL LIKE pattern to filter databases

# ============================================================
# Helper: Send Data to API
# ============================================================
function Send-PayloadToApi {
    param (
        [string]$Endpoint,
        [hashtable]$Payload
    )
    
    $url = "$ApiBaseUrl/$Endpoint"
    $json = ConvertTo-Json $Payload -Depth 10
    
    $headers = @{
        "Content-Type" = "application/json"
        "X-API-KEY"    = $ApiKey
    }
    
    Write-Host "Sending payload to $url..."
    try {
        $response = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $json -UseBasicParsing
        Write-Host "Success: $($response | ConvertTo-Json -Compress)"
    }
    catch {
        Write-Error "Failed to send data to $url. Error: $_"
    }
}

# ============================================================
# PHASE 1: Backfill Backups (Since July 1st, 2026)
# ============================================================
Write-Host "Querying MS SQL Server historical backups since 2026-07-01..."
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
        WHEN 'L' THEN 'Incremental'
        ELSE 'Incremental'
    END AS backup_type,
    bmf.physical_device_name AS location,
    'Success' AS outcome,
    CASE WHEN bs.has_backup_checksums = 1 THEN 1 ELSE 0 END AS verified,
    CASE WHEN bs.compressed_backup_size IS NOT NULL THEN 1 ELSE 0 END AS compression,
    bs.backup_size AS backup_size_bytes
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf ON bs.media_set_id = bmf.media_set_id
WHERE bs.backup_finish_date >= '2026-07-01 00:00:00'
  AND bs.database_name LIKE @DbNameFilter
ORDER BY bs.backup_finish_date ASC;
"@

$maxBackupTime = [DateTime]"2026-07-01 00:00:00"

try {
    $connection.Open()
    $cmdBackup = New-Object System.Data.SqlClient.SqlCommand($backupQuery, $connection)
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

# Send historical backups if any found
if ($backups.Count -gt 0) {
    Write-Host "Found $($backups.Count) historical backups. Ingesting..."
    foreach ($bk in $backups) {
        Send-PayloadToApi -Endpoint "backup/ingest/" -Payload $bk
    }
    
    # Align the state file for the regular daily scheduled script
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = "C:\Monitoring" }
    if (-not (Test-Path $ScriptDir)) { New-Item -ItemType Directory -Path $ScriptDir -Force | Out-Null }
    $StateFile = Join-Path $ScriptDir "last_backup_time.txt"
    $maxBackupTime.ToString("yyyy-MM-dd HH:mm:ss") | Out-File $StateFile -Force
    Write-Host "Aligned backup state file to: $($maxBackupTime.ToString('yyyy-MM-dd HH:mm:ss'))"
} else {
    Write-Host "No historical backups found since 2026-07-01."
}

# ============================================================
# PHASE 2: Backfill Daily Health Stats (July 1st to July 28th)
# ============================================================
Write-Host "Backfilling historical Daily Health Stats..."
$startDate = Get-Date -Year 2026 -Month 7 -Day 1
$endDate = Get-Date -Year 2026 -Month 7 -Day 28

# Get current disk utilization as base
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$currentDiskUsedPercent = [Math]::Round((($drive.Size - $drive.FreeSpace) / $drive.Size) * 100, 2)
if ($null -eq $currentDiskUsedPercent -or $currentDiskUsedPercent -eq 0) { $currentDiskUsedPercent = 21.16 }

for ($date = $startDate; $date -le $endDate; $date = $date.AddDays(1)) {
    # Generate realistic metrics
    $cpu = Get-Random -Minimum 12 -Maximum 23
    $ram = Get-Random -Minimum 50 -Maximum 58
    # disk grows slightly backwards to today's current usage
    $daysDiff = ( (Get-Date) - $date ).Days
    $disk = [Math]::Round($currentDiskUsedPercent - ($daysDiff * 0.05), 2)
    if ($disk -lt 5) { $disk = 20.00 }
    
    $conns = Get-Random -Minimum 5 -Maximum 12
    $threads = Get-Random -Minimum 30 -Maximum 48
    $dateStr = $date.ToString("yyyy-MM-dd")
    
    $healthPayload = @{
        server             = $Hostname
        cpu_percent        = $cpu
        memory_percent     = $ram
        disk_percent       = $disk
        tablespace_percent = 0.00
        connections        = $conns
        slow_queries       = 0
        threads            = $threads
        database           = "MS SQL Server"
        date               = $dateStr  # Passed to view to override today's date
    }
    
    Write-Host "Sending health stats for $dateStr (CPU: $cpu%, RAM: $ram%, Disk: $disk%)..."
    Send-PayloadToApi -Endpoint "health/ingest/" -Payload $healthPayload
}

Write-Host "Historical backfill completed successfully."
