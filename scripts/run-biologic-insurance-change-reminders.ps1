param(
    [switch]$Apply,
    [int]$DispenseLookbackDays = 45,
    [int]$ChangeLookbackDays = 3,
    [string]$RemoteHost = "ims-referrals",
    [string]$ReportsDir = "$PSScriptRoot\..\reports"
)

$ErrorActionPreference = "Stop"

if ($DispenseLookbackDays -lt 1 -or $DispenseLookbackDays -gt 365) {
    throw "DispenseLookbackDays must be between 1 and 365."
}
if ($ChangeLookbackDays -lt 1 -or $ChangeLookbackDays -gt 30) {
    throw "ChangeLookbackDays must be between 1 and 30."
}

$repoRoot = Resolve-Path -LiteralPath "$PSScriptRoot\.."
$resolvedReports = New-Item -ItemType Directory -Force -Path $ReportsDir
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

if ($Apply) {
    $templatePath = Join-Path $repoRoot "sql\create_biologic_insurance_change_reminders.sql"
    $localPrepared = Join-Path $env:TEMP "create_biologic_insurance_change_reminders_$stamp.sql"
    $remoteSql = "/tmp/create_biologic_insurance_change_reminders_$stamp.sql"
    $localOut = Join-Path $resolvedReports.FullName "created_biologic_insurance_change_reminders_$stamp.txt"
} else {
    $templatePath = Join-Path $repoRoot "sql\biologic_insurance_change_reminder_candidates.sql"
    $localPrepared = Join-Path $env:TEMP "biologic_insurance_change_reminder_candidates_$stamp.sql"
    $remoteSql = "/tmp/biologic_insurance_change_reminder_candidates_$stamp.sql"
    $remoteWrappedSql = "/tmp/biologic_insurance_change_reminder_candidates_${stamp}_wrapped.sql"
    $remoteCsv = "/tmp/biologic_insurance_change_reminder_candidates_$stamp.csv"
    $localOut = Join-Path $resolvedReports.FullName "biologic_insurance_change_reminder_candidates_$stamp.csv"
}

$sql = Get-Content -LiteralPath $templatePath -Raw
$sql = $sql.Replace("__DISPENSE_LOOKBACK_DAYS__", [string]$DispenseLookbackDays)
$sql = $sql.Replace("__CHANGE_LOOKBACK_DAYS__", [string]$ChangeLookbackDays)
$sql = $sql.Replace("__APPLY_EXTRA_WHERE__", "")
Set-Content -LiteralPath $localPrepared -Value $sql -NoNewline

try {
    scp $localPrepared "${RemoteHost}:$remoteSql" | Out-Null

    if ($Apply) {
        $remoteScript = @"
set -euo pipefail
set -a
. /opt/ims_router/.env
set +a
. /opt/sqlanywhere17/bin64/sa_config.sh
conn="UID=`${IMS_DB_USER};PWD=`${IMS_DB_PASSWORD};ENG=`${IMS_DB_ENGINE};DBN=`${IMS_DB_NAME};LINKS=tcpip(host=`${IMS_DB_HOST}:`${IMS_DB_PORT});"
/opt/sqlanywhere17/bin64/dbisql -nogui -onerror exit -c "`$conn" "$remoteSql"
rm -f "$remoteSql"
"@
        $remoteScript | ssh $RemoteHost "bash -s" | Set-Content -Path $localOut
        Write-Host "Created reminders. Saved dbisql output to $localOut"
        Get-Content -Path $localOut | Select-String -Pattern "OPEN_BIO_INS_CHANGE_PA_REMINDERS"
    } else {
        $remoteScript = @"
set -euo pipefail
set -a
. /opt/ims_router/.env
set +a
. /opt/sqlanywhere17/bin64/sa_config.sh
conn="UID=`${IMS_DB_USER};PWD=`${IMS_DB_PASSWORD};ENG=`${IMS_DB_ENGINE};DBN=`${IMS_DB_NAME};LINKS=tcpip(host=`${IMS_DB_HOST}:`${IMS_DB_PORT});"
cp "$remoteSql" "$remoteWrappedSql"
printf "\nOUTPUT TO '$remoteCsv' FORMAT ASCII QUOTE '\"' DELIMITED BY ',' WITH COLUMN NAMES;\n" >> "$remoteWrappedSql"
/opt/sqlanywhere17/bin64/dbisql -nogui -q -onerror exit -c "`$conn" "$remoteWrappedSql"
rm -f "$remoteSql" "$remoteWrappedSql"
"@
        $remoteScript | ssh $RemoteHost "bash -s"
        scp "${RemoteHost}:$remoteCsv" $localOut | Out-Null
        ssh $RemoteHost "rm -f '$remoteCsv'"
        $lineCount = (Get-Content -Path $localOut | Measure-Object -Line).Lines
        $rowCount = [Math]::Max(0, $lineCount - 1)
        Write-Host "Dry run only. Saved candidate CSV to $localOut"
        Write-Host "Rows: $rowCount"
    }
} finally {
    Remove-Item -LiteralPath $localPrepared -ErrorAction SilentlyContinue
}

