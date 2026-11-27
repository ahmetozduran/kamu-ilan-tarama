# kk_tara.ps1 — LogiX'in bilgisayarında (Türkiye) çalışan küçük ilan okuyucu.
# Kariyer Kapısı kamu işe alım sayfasının herkese açık ilan listesini, sayfanın kendisinin
# kullandığı adresten, normal bir ziyaretçi gibi bir kez okur ve sonucu GitHub deposuna
# (pc/kariyerkapisi.json) yükler. Giriş yapmaz, e-Devlet bilgisi kullanmaz.
# ilan.gov.tr için de tek bir deneme yapar ve sonucunu not eder.
# Kurulum: pc/kur.ps1. Kaldırma: pc/kaldir.ps1.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Klasor  = Join-Path $env:LOCALAPPDATA "kamu-ilan"
$Gunluk  = Join-Path $Klasor "gunluk.txt"
$TokenDosyasi = Join-Path $Klasor "token.dat"
$SonCalisma   = Join-Path $Klasor "son_calisma.txt"
$Depo    = "ahmetozduran/kamu-ilan-tarama"
$UA      = "kamu-ilan-tarama/1.0 (kisisel ilan takibi; github.com/ahmetozduran/kamu-ilan-tarama)"
New-Item -ItemType Directory -Force -Path $Klasor | Out-Null

function Yaz($m) {
    $s = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"
    Add-Content -Path $Gunluk -Value $s -Encoding UTF8
    Write-Host $s
}

# Aynı gün içinde 4 saatten sık çalışma (bilgisayar sık açılıp kapanırsa gereksiz istek olmasın)
$zorla = $args -contains "-simdi"
if ((Test-Path $SonCalisma) -and -not $zorla) {
    $son = [datetime]::Parse((Get-Content $SonCalisma -Raw).Trim())
    if (((Get-Date) - $son).TotalHours -lt 4) { Yaz "Son çalışma $son, 4 saat dolmadı; atlandı."; exit 0 }
}

# İnternet bağlantısı gelene kadar bekle (açılışta Wi-Fi geç bağlanabilir): en fazla ~5 dk
$bagli = $false
for ($i = 0; $i -lt 10; $i++) {
    try { Invoke-WebRequest -Uri "https://api.github.com" -UseBasicParsing -TimeoutSec 15 -UserAgent $UA | Out-Null; $bagli = $true; break }
    catch { Start-Sleep -Seconds 30 }
}
if (-not $bagli) { Yaz "İnternet yok; çıkıldı."; exit 0 }

function Getir($yontem, $adres, $govde) {
    $sonuc = [ordered]@{ adres = $adres; durum = 0; hata = ""; metin = "" }
    try {
        $p = @{ Uri = $adres; Method = $yontem; UseBasicParsing = $true; TimeoutSec = 40; UserAgent = $UA
                Headers = @{ "Accept" = "application/json, text/plain, */*" } }
        if ($govde) { $p.Body = [Text.Encoding]::UTF8.GetBytes(($govde | ConvertTo-Json -Compress)); $p.ContentType = "application/json" }
        $r = Invoke-WebRequest @p
        $sonuc.durum = [int]$r.StatusCode
        $ms = New-Object IO.MemoryStream; $r.RawContentStream.CopyTo($ms)
        $sonuc.metin = [Text.Encoding]::UTF8.GetString($ms.ToArray())
    } catch {
        if ($_.Exception.Response) { $sonuc.durum = [int]$_.Exception.Response.StatusCode }
        $sonuc.hata = $_.Exception.Message
    }
    return $sonuc
}

$kayit = [ordered]@{ zaman = (Get-Date).ToString("s"); bilgisayar = $env:COMPUTERNAME; kaynaklar = [ordered]@{} }

# 1) Kariyer Kapısı: kamu işe alım sayfasının açılışta kendisinin istediği liste (tüm kurumlar, tüm iller)
$kk = Getir "POST" "https://api.kariyerkapisi.gov.tr/api/ilan/GetIseAlimPage" @{ krM_ID = 0; searchText = ""; il = "0"; ilanTuru = "0" }
$kkVeri = $null
if ($kk.durum -eq 200) { try { $kkVeri = $kk.metin | ConvertFrom-Json } catch { $kk.hata = "JSON okunamadı" } }
$ilanSayisi = 0
if ($kkVeri -and $kkVeri.searchIlan) { $ilanSayisi = @($kkVeri.searchIlan).Count }
$kayit.kaynaklar["kariyerkapisi"] = [ordered]@{ durum = $kk.durum; hata = $kk.hata; ilan_sayisi = $ilanSayisi }
Yaz "Kariyer Kapısı: HTTP $($kk.durum) $($kk.hata) · $ilanSayisi ilan"

# 2) ilan.gov.tr: sayfanın kullandığı ilan detay adresine tek deneme (bilinen bir personel ilanı)
$ig = Getir "GET" "https://www.ilan.gov.tr/api/api/services/app/AdDetail/GetAdDetail?id=2298310&isKiwiAd=false" $null
$kayit.kaynaklar["ilangovtr_deneme"] = [ordered]@{ durum = $ig.durum; hata = $ig.hata; ornek = $ig.metin.Substring(0, [Math]::Min(300, $ig.metin.Length)) }
Yaz "ilan.gov.tr deneme: HTTP $($ig.durum) $($ig.hata)"

$kayit["kariyerkapisi_ilanlar"] = if ($kkVeri) { $kkVeri.searchIlan } else { @() }

# 3) GitHub'a yükle (pc/kariyerkapisi.json)
if (-not (Test-Path $TokenDosyasi)) { Yaz "Token yok; kur.ps1'i çalıştır."; exit 1 }
$guvenli = Get-Content $TokenDosyasi | ConvertTo-SecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($guvenli))
$gh = @{ "Authorization" = "Bearer $token"; "Accept" = "application/vnd.github+json"; "User-Agent" = $UA }
$yol = "https://api.github.com/repos/$Depo/contents/pc/kariyerkapisi.json"
$sha = $null
try { $sha = (Invoke-RestMethod -Uri "$yol`?ref=main" -Headers $gh -TimeoutSec 30).sha } catch { }
$json = $kayit | ConvertTo-Json -Depth 12
$govde = [ordered]@{ message = "pc: Kariyer Kapısı okuması $(Get-Date -Format 'yyyy-MM-dd HH:mm')"; branch = "main"
                     content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)) }
if ($sha) { $govde.sha = $sha }
try {
    Invoke-RestMethod -Uri $yol -Method Put -Headers $gh -TimeoutSec 60 -ContentType "application/json" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($govde | ConvertTo-Json -Compress))) | Out-Null
    Yaz "GitHub'a yüklendi."
    (Get-Date).ToString("o") | Set-Content -Path $SonCalisma
} catch { Yaz "GitHub yükleme hatası: $($_.Exception.Message)"; exit 1 }
