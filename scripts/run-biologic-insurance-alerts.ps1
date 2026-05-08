param(
    [string]$RemoteHost = "ims-referrals",
    [string]$SqlPath = "$PSScriptRoot\..\sql\biologic_insurance_change_alerts.sql",
    [string]$ReportsDir = "$PSScriptRoot\..\reports"
)

$ErrorActionPreference = "Stop"

$resolvedSql = Resolve-Path -LiteralPath $SqlPath
$resolvedReports = New-Item -ItemType Directory -Force -Path $ReportsDir
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$remoteSql = "/tmp/biologic_insurance_change_alerts_$stamp.sql"
$remoteWrappedSql = "/tmp/biologic_insurance_change_alerts_${stamp}_wrapped.sql"
$remoteCsv = "/tmp/biologic_insurance_change_alerts_$stamp.csv"
$localOut = Join-Path $resolvedReports.FullName "biologic_insurance_change_alerts_$stamp.csv"

scp $resolvedSql.Path "${RemoteHost}:$remoteSql" | Out-Null

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

Write-Host ""
Write-Host "Saved report to $localOut"
$lineCount = (Get-Content -Path $localOut | Measure-Object -Line).Lines
$rowCount = [Math]::Max(0, $lineCount - 1)
Write-Host "Rows: $rowCount"
