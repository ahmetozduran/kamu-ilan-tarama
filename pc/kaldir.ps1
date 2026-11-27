# kaldir.ps1 — Kariyer Kapisi okuyucusunu bilgisayardan tamamen kaldirir.
# PowerShell'de:  irm https://raw.githubusercontent.com/ahmetozduran/kamu-ilan-tarama/main/pc/kaldir.ps1 | iex
Unregister-ScheduledTask -TaskName "Kamu ilan okuyucu" -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA "kamu-ilan") -ErrorAction SilentlyContinue
Write-Host "Kaldirildi: zamanlanmis gorev ve kamu-ilan klasoru silindi. GitHub token'ini github.com/settings/personal-access-tokens adresinden de silebilirsin." -ForegroundColor Green
