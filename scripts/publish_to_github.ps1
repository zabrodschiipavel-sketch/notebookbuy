# Publish NotebookBuy to GitHub (requires: gh auth login)
# Usage: .\scripts\publish_to_github.ps1

$ErrorActionPreference = "Stop"

$gh = "$env:ProgramFiles\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
    Write-Error "GitHub CLI not found. Install: winget install GitHub.cli"
}

& $gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Run: gh auth login"
    exit 1
}

$login = & $gh api user -q .login
Write-Host "GitHub user: $login"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$expectedOwner = "zabrodschiipavel-sketch"
if ($login -ne $expectedOwner) {
    Write-Warning -Message "Docs use github.com/$expectedOwner/notebookbuy but you are logged in as $login."
}

$remoteUrl = "https://github.com/$login/notebookbuy.git"
git remote remove origin 2>$null
git remote add origin $remoteUrl

$exists = & $gh repo view "$login/notebookbuy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating public repository $login/notebookbuy ..."
    & $gh repo create notebookbuy --public `
        --description "Find the best laptop deals on 999.md - scrape, benchmark, score, visualize." `
        --source . --remote origin --push
} else {
    Write-Host "Repository exists. Pushing main and tags ..."
    git push -u origin main
    git push origin v1.0.0
}

if ($LASTEXITCODE -eq 0) {
  $releaseExists = & $gh release view v1.0.0 2>$null
  if ($LASTEXITCODE -ne 0) {
      Write-Host "Creating GitHub Release v1.0.0 ..."
      & $gh release create v1.0.0 --title "NotebookBuy 1.0.0" --notes-file CHANGELOG.md
  } else {
      Write-Host "Release v1.0.0 already exists."
  }
}

Write-Host "Done: https://github.com/$login/notebookbuy"
