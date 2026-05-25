# Quick smoke test of the gitpp CLI on the parallel-methods scenario.
$ErrorActionPreference = 'Stop'
$root = 'Q:\projects\git-plusplus'
$gitpp = "$root\.venv\Scripts\gitpp.exe"
$scen = "$root\tests\scenarios\parallel-methods"
$demo = "$root\.demo"

if (Test-Path $demo) { Remove-Item -Recurse -Force $demo }
New-Item -ItemType Directory -Path $demo | Out-Null
Set-Location $demo

Copy-Item "$scen\base.py" .\inventory.py
& $gitpp init .
& $gitpp add inventory.py
& $gitpp commit -m "base"
$baseSha = (Get-Content .gitpp\refs\heads\main).Trim()
Write-Host "BASE commit: $baseSha"

Copy-Item "$scen\ours.py" .\inventory.py -Force
& $gitpp add inventory.py
& $gitpp commit -m "add remove()"
$oursSha = (Get-Content .gitpp\refs\heads\main).Trim()
Write-Host "OURS commit: $oursSha"

# Branch 'feature' from base, commit theirs there
Set-Content .gitpp\refs\heads\feature "$baseSha`n"
Set-Content .gitpp\HEAD "ref: refs/heads/feature`n"
# Reset working tree + index to base before committing theirs
Copy-Item "$scen\base.py" .\inventory.py -Force
& $gitpp add inventory.py
Copy-Item "$scen\theirs.py" .\inventory.py -Force
& $gitpp add inventory.py
& $gitpp commit -m "add count()"

# Back to main, working tree to ours, then merge feature
Set-Content .gitpp\HEAD "ref: refs/heads/main`n"
Copy-Item "$scen\ours.py" .\inventory.py -Force
& $gitpp add inventory.py

Write-Host ""
Write-Host "=== gitpp merge feature ==="
& $gitpp merge feature
Write-Host ""
Write-Host "=== merged inventory.py ==="
Get-Content .\inventory.py
Write-Host ""
Write-Host "=== diff vs expected ==="
$diff = Compare-Object (Get-Content .\inventory.py) (Get-Content "$scen\expected.py")
if ($null -eq $diff) { Write-Host "IDENTICAL to expected.py" }
else { $diff | Format-Table }

Write-Host ""
Write-Host "=== gitpp log ==="
& $gitpp log
