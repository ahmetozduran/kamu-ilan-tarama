# kur.ps1 — LogiX'in bilgisayarina Kariyer Kapisi okuyucusunu kurar (bir kez calistirilir).
# PowerShell'de:  irm https://raw.githubusercontent.com/ahmetozduran/kamu-ilan-tarama/main/pc/kur.ps1 | iex
# Yaptiklari: 1) kk_tara.ps1'i %LOCALAPPDATA%\kamu-ilan klasorune indirir,
# 2) GitHub token'ini sorar ve yalnizca bu Windows kullanicisinin acabilecegi sekilde sifreli saklar,
# 3) "Kamu ilan okuyucu" adli zamanlanmis gorevi kurar: her oturum acilisinda (2 dk sonra) ve
#    her gun 09:00 / 13:00 / 17:00 / 21:00'de (bilgisayar o saatte kapaliysa acilinca) calisir,
# 4) hemen bir kez calistirip sonucu gosterir.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Klasor = Join-Path $env:LOCALAPPDATA "kamu-ilan"
New-Item -ItemType Directory -Force -Path $Klasor | Out-Null
$Betik = Join-Path $Klasor "kk_tara.ps1"

Write-Host "`n[1/4] Betik indiriliyor..." -ForegroundColor Cyan
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/ahmetozduran/kamu-ilan-tarama/main/pc/kk_tara.ps1" -OutFile $Betik
Write-Host "      Tamam: $Betik"

Write-Host "`n[2/4] GitHub token'i" -ForegroundColor Cyan
Write-Host "      Kopyaladigin token'i yapistirip Enter'a bas (ekranda gorunmez, bu normal):"
$guvenli = Read-Host -AsSecureString
$guvenli | ConvertFrom-SecureString | Set-Content -Path (Join-Path $Klasor "token.dat")
Write-Host "      Tamam: sifreli kaydedildi."

Write-Host "`n[3/4] Zamanlanmis gorev kuruluyor..." -ForegroundColor Cyan
$eylem = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Betik`""
$t1 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$t1.Delay = "PT2M"
$t2 = New-ScheduledTaskTrigger -Daily -At "09:00"
$t3 = New-ScheduledTaskTrigger -Daily -At "13:00"
$t4 = New-ScheduledTaskTrigger -Daily -At "17:00"
$t5 = New-ScheduledTaskTrigger -Daily -At "21:00"
$ayar = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$kim = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Kamu ilan okuyucu" -Action $eylem -Trigger @($t1, $t2, $t3, $t4, $t5) `
    -Settings $ayar -Principal $kim -Force | Out-Null
Write-Host "      Tamam: 'Kamu ilan okuyucu' gorevi kuruldu."

Write-Host "`n[4/4] Ilk deneme calistiriliyor (1-2 dk surebilir)..." -ForegroundColor Cyan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Betik -simdi
Write-Host "`nBitti. Bu pencereyi kapatabilirsin. Laren'e 'kurdum' yazman yeterli." -ForegroundColor Green
