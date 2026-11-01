# 一键推送到你的 GitHub 空仓库（由你本人执行，凭证只输入到你本机）
# 用法（在项目根目录 PowerShell 中执行）：
#   powershell -ExecutionPolicy Bypass -File .\push_github.ps1
# 或直接带地址：
#   powershell -ExecutionPolicy Bypass -File .\push_github.ps1 -RepoUrl https://github.com/你的账号/仓库名.git
param(
    [string]$RepoUrl = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $RepoUrl) {
    $RepoUrl = Read-Host "请粘贴 GitHub 空仓库地址（形如 https://github.com/用户名/仓库名.git）"
}
$RepoUrl = $RepoUrl.Trim()

if (-not (Test-Path ".git")) {
    Write-Host "[错误] 当前目录不是 git 仓库，请确认在项目根目录执行。" -ForegroundColor Red
    exit 1
}

Write-Host "==> 检查本地提交" -ForegroundColor Cyan
git log --oneline -1

Write-Host "==> 设置远程仓库 origin" -ForegroundColor Cyan
git remote remove origin 2>$null
git remote add origin $RepoUrl

Write-Host "==> 推送 main 分支（首次会弹出 GitHub 登录，请按提示授权）" -ForegroundColor Cyan
git push -u origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[完成] 已推送到：$RepoUrl" -ForegroundColor Green
    Write-Host "提示：可用 `git remote -v` 查看远程地址；后续提交用 `git push`。" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "[失败] 推送未成功。常见原因：仓库地址错误、未登录 GitHub、分支保护。" -ForegroundColor Yellow
    Write-Host "可重新执行本脚本，或手动执行：git push -u origin main" -ForegroundColor Yellow
}
