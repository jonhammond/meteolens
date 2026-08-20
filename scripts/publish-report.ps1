$ErrorActionPreference = 'Stop'
$az = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
$reportRoot = 'Z:\Code\meteolens\powerbi\e89d7c20-65af-95c3-4838-2b659bafdddf.Report'
$ws = '7a102e5b-56e2-4dbc-b04c-5cedff7c3b0e'
$reportId = '81f29b1c-7758-498c-a367-bb00ac6e9c8a'

$token = & $az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
if (-not $token) { throw 'no fabric token' }
$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

$parts = @()
Get-ChildItem -Path $reportRoot -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($reportRoot.Length + 1) -replace '\\', '/'
    if ($rel -eq '.platform' -or $rel -like '.pbi/*') { return }
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    $parts += @{ path = $rel; payload = [Convert]::ToBase64String($bytes); payloadType = 'InlineBase64' }
}
Write-Output ("parts: " + $parts.Count)
$parts | ForEach-Object { Write-Output ("  " + $_.path) }

$body = @{ definition = @{ parts = $parts } } | ConvertTo-Json -Depth 6
$url = "https://api.fabric.microsoft.com/v1/workspaces/$ws/items/$reportId/updateDefinition"
$resp = Invoke-WebRequest -Uri $url -Method POST -Headers $headers -Body $body -UseBasicParsing
Write-Output ("updateDefinition HTTP " + $resp.StatusCode)

if ($resp.StatusCode -eq 202) {
    $loc = $resp.Headers['Location']
    Write-Output ("polling " + $loc)
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        $poll = Invoke-WebRequest -Uri $loc -Headers @{ Authorization = "Bearer $token" } -UseBasicParsing
        $st = ($poll.Content | ConvertFrom-Json).status
        Write-Output ("  status: " + $st)
        if ($st -eq 'Succeeded') { break }
        if ($st -eq 'Failed') { Write-Output $poll.Content; throw 'updateDefinition failed' }
    }
}
Write-Output 'PUBLISH DONE'
