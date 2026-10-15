# The temporary report URL arrives on stdin, never in process arguments/output.
# This probe downloads only the aggregate active-user-count report.
#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $target = [uri]$inputData.url
    if (-not $target.IsAbsoluteUri -or $target.Scheme -ne 'https' -or $target.UserInfo) {
        throw 'Expected an HTTPS report URL without embedded user credentials.'
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $response = Invoke-WebRequest -Uri $target -Method Get -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 5
    $body = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }
    $header = ($body -split '\r?\n', 2)[0]
    $valid = $header -match 'Report Refresh Date' -and $header -match 'Report Date' -and $header -match 'Report Period'
    $result = @{success=($response.StatusCode -eq 200 -and $valid);status_code=[int]$response.StatusCode}
} catch {
    $statusCode = 0
    if ($_.Exception.Response) {
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { }
    }
    # Exception messages can contain the signed URL. Emit only type/status.
    $baseException = $_.Exception.GetBaseException()
    $result = @{success=$false;status_code=$statusCode;error_type=$baseException.GetType().Name}
    if ($baseException -is [System.ComponentModel.Win32Exception]) { $result['native_error_code'] = $baseException.NativeErrorCode }
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
if (-not $result.success) { exit 1 }
