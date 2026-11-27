# kk_tara.ps1 — LogiX'in bilgisayarında (Türkiye) çalışan ilan okuyucu (sürüm 2).
# Görevleri:
#  1) Kariyer Kapısı kamu işe alım sayfasının herkese açık ilan listesini okur (pc/kariyerkapisi.json).
#  2) GitHub taramasının açamadığı sayfaları (pc/pc_gorevleri.json; ilan.gov.tr personel listesi her zaman
#     dahil) bilgisayardaki Edge tarayıcısıyla, ekran açmadan, normal bir ziyaretçi gibi açar; ilan
#     bağlantılarını ve metinlerini toplar (pc/pc_sonuclar.json).
# Kurallar: robots.txt'nin yasakladığı sayfalara girmez; giriş yapmaz; site başına istekler arasında bekler;
# çalışma başına en fazla 60 sayfa açar. Her çalışmada GitHub'daki en yeni sürüme kendini günceller.
# Kurulum: pc/kur.ps1. Kaldırma: pc/kaldir.ps1.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Klasor  = Join-Path $env:LOCALAPPDATA "kamu-ilan"
$Gunluk  = Join-Path $Klasor "gunluk.txt"
$TokenDosyasi = Join-Path $Klasor "token.dat"
$SonCalisma   = Join-Path $Klasor "son_calisma.txt"
$GorulenDosya = Join-Path $Klasor "gorulen.txt"
$Depo    = "ahmetozduran/kamu-ilan-tarama"
$UA      = "kamu-ilan-tarama/2.0 (kisisel ilan takibi; github.com/ahmetozduran/kamu-ilan-tarama)"
$TR      = [Globalization.CultureInfo]::GetCultureInfo("tr-TR")
$Basla   = Get-Date
$SayfaSiniri = 60
$SureSiniriDk = 11
New-Item -ItemType Directory -Force -Path $Klasor | Out-Null

function Yaz($m) {
    $s = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"
    Add-Content -Path $Gunluk -Value $s -Encoding UTF8
    Write-Host $s
}

trap {
    Yaz "BEKLENMEYEN HATA (satır $($_.InvocationInfo.ScriptLineNumber)): $($_.Exception.Message)"
    break
}

# Günlük dosyası çok büyümesin
if ((Test-Path $Gunluk) -and (Get-Item $Gunluk).Length -gt 500KB) {
    Get-Content $Gunluk -Tail 2000 | Set-Content $Gunluk -Encoding UTF8
}

# 3 saatten sık çalışma
$zorla = $args -contains "-simdi"
if ((Test-Path $SonCalisma) -and -not $zorla) {
    $son = [datetime]::Parse((Get-Content $SonCalisma -Raw).Trim())
    if (((Get-Date) - $son).TotalHours -lt 3) { Yaz "Son çalışma $son, 3 saat dolmadı; atlandı."; exit 0 }
}

# İnternet bağlantısı gelene kadar bekle: en fazla ~5 dk
$bagli = $false
for ($i = 0; $i -lt 10; $i++) {
    try { Invoke-WebRequest -Uri "https://api.github.com" -UseBasicParsing -TimeoutSec 15 -UserAgent $UA | Out-Null; $bagli = $true; break }
    catch { Start-Sleep -Seconds 30 }
}
if (-not $bagli) { Yaz "İnternet yok; çıkıldı."; exit 0 }

# Zamanlanmış görevin saatlerini güncel tut: oturum açılışı + 09:00, 13:00, 17:00, 21:00
try {
    $gorev = Get-ScheduledTask -TaskName "Kamu ilan okuyucu" -ErrorAction Stop
    $saatler = @($gorev.Triggers | Where-Object { $_.StartBoundary } | ForEach-Object { ([datetime]$_.StartBoundary).ToString("HH:mm") })
    $istenen = @("09:00", "13:00", "17:00", "21:00")
    if (@($istenen | Where-Object { $saatler -notcontains $_ }).Count -gt 0) {
        $t1 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"; $t1.Delay = "PT2M"
        $tetik = @($t1) + @($istenen | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
        Set-ScheduledTask -TaskName "Kamu ilan okuyucu" -Trigger $tetik | Out-Null
        Yaz "Zamanlanmış görev saatleri güncellendi: açılış + $($istenen -join ', ')"
    }
} catch { Yaz "Zamanlanmış görev saatleri denetlenemedi: $($_.Exception.Message)" }

# ---------------------------------------------------------------- GitHub
if (-not (Test-Path $TokenDosyasi)) { Yaz "Token yok; kur.ps1'i çalıştır."; exit 1 }
$guvenli = Get-Content $TokenDosyasi | ConvertTo-SecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($guvenli))
$GH = @{ "Authorization" = "Bearer $token"; "User-Agent" = $UA }

# Kendini güncelle (GitHub'daki pc/kk_tara.ps1 farklıysa onu alıp yeniden başlar)
if (-not ($args -contains "-guncellendi")) {
    try {
        $tmp = Join-Path $Klasor "kk_tara.yeni.ps1"
        # raw.githubusercontent önbelleği 5 dk gecikebildiği için GitHub API'den (önbelleksiz) alınır
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 -OutFile $tmp -Headers ($GH + @{ "Accept" = "application/vnd.github.raw" }) `
            -Uri "https://api.github.com/repos/$Depo/contents/pc/kk_tara.ps1?ref=main"
        if ((Get-Item $tmp).Length -gt 1000 -and (Get-FileHash $tmp).Hash -ne (Get-FileHash $PSCommandPath).Hash) {
            Copy-Item $tmp $PSCommandPath -Force
            Yaz "Betik GitHub'dan güncellendi; yeni sürüm başlatılıyor."
            # Not: '& powershell.exe' kullanılınca alt sürecin hata akışı üst süreçte durdurucu hataya
            # dönüşüp çalışmayı sessizce kesebiliyordu (27.11.2026); Start-Process ile bekleniyor.
            $ekArg = (@($args) + "-guncellendi") -join " "
            $alt = Start-Process -FilePath "powershell.exe" -NoNewWindow -Wait -PassThru `
                -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $ekArg"
            $kod = $alt.ExitCode
            Yaz "Yeni sürüm bitti (çıkış kodu $kod)."
            exit $kod
        }
    } catch { Yaz "Güncelleme denetimi yapılamadı: $($_.Exception.Message)" }
}


function GhOku($yol) {   # dosyanın metni ve sha'sı; yoksa $null
    $api = "https://api.github.com/repos/$Depo/contents/$yol`?ref=main"
    try {
        $meta = Invoke-RestMethod -Uri $api -Headers ($GH + @{ "Accept" = "application/vnd.github+json" }) -TimeoutSec 30
        $r = Invoke-WebRequest -Uri $api -Headers ($GH + @{ "Accept" = "application/vnd.github.raw" }) -UseBasicParsing -TimeoutSec 60
        $ms = New-Object IO.MemoryStream; $r.RawContentStream.CopyTo($ms)
        $metin = [Text.Encoding]::UTF8.GetString($ms.ToArray()).TrimStart([char]0xFEFF)
        return @{ sha = $meta.sha; metin = $metin }
    } catch { return $null }
}

function GhYaz($yol, $metin, $mesaj) {
    $api = "https://api.github.com/repos/$Depo/contents/$yol"
    for ($deneme = 0; $deneme -lt 3; $deneme++) {
        $sha = $null
        try { $sha = (Invoke-RestMethod -Uri "$api`?ref=main" -Headers ($GH + @{ "Accept" = "application/vnd.github+json" }) -TimeoutSec 30).sha } catch { }
        $govde = [ordered]@{ message = $mesaj; branch = "main"
                             content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($metin)) }
        if ($sha) { $govde.sha = $sha }
        try {
            Invoke-RestMethod -Uri $api -Method Put -Headers ($GH + @{ "Accept" = "application/vnd.github+json" }) -TimeoutSec 90 `
                -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes(($govde | ConvertTo-Json -Compress))) | Out-Null
            return $true
        } catch { Yaz "GitHub yazma denemesi $($deneme + 1) başarısız ($yol): $($_.Exception.Message)"; Start-Sleep -Seconds 5 }
    }
    return $false
}

# ---------------------------------------------------------------- yardımcılar
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

function Kucuk($s) { if ($null -eq $s) { return "" }; return $s.ToLower($TR) }

$RobotsOnbellek = @{}
function RobotsIzin($url) {
    # RFC 9309: robots.txt 4xx ise serbest; 5xx/ulaşılamıyorsa izin yok. Yalnızca 'User-agent: *' grubu.
    $u = [Uri]$url
    $kok = "$($u.Scheme)://$($u.Host)"
    if (-not $RobotsOnbellek.ContainsKey($kok)) {
        $r = Getir "GET" "$kok/robots.txt" $null
        $kurallar = @()
        if ($r.durum -eq 200) {
            $grup = $false
            foreach ($satir in ($r.metin -split "`n")) {
                $s = ($satir -replace "#.*$", "").Trim()
                if ($s -match "^(?i)user-agent\s*:\s*(.+)$") { $grup = ($Matches[1].Trim() -eq "*"); continue }
                if ($grup -and $s -match "^(?i)disallow\s*:\s*(.*)$") { $y = $Matches[1].Trim(); if ($y) { $kurallar += $y } }
            }
            $RobotsOnbellek[$kok] = $kurallar
        } elseif ($r.durum -ge 400 -and $r.durum -lt 500) {
            $RobotsOnbellek[$kok] = @()
        } else {
            $RobotsOnbellek[$kok] = @("/")
        }
    }
    $yol = $u.PathAndQuery
    foreach ($k in $RobotsOnbellek[$kok]) {
        $desen = "^" + (([regex]::Escape($k) -replace '\\\*', '.*') -replace '\\\$$', '$')
        if ($yol -match $desen) { return $false }
    }
    return $true
}

$Edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$SonIstek = @{}
$AcilanSayfa = 0
function EdgeDom($url) {
    # Edge'i görünmez (headless) açar, sayfa JavaScript'iyle yüklendikten sonraki HTML'i döndürür
    if (-not $Edge) { return "" }
    $h = ([Uri]$url).Host
    if ($SonIstek.ContainsKey($h)) {
        $gecen = ((Get-Date) - $SonIstek[$h]).TotalSeconds
        if ($gecen -lt 3) { Start-Sleep -Milliseconds ([int]((3 - $gecen) * 1000)) }
    }
    $SonIstek[$h] = Get-Date
    $script:AcilanSayfa++
    # Not: Edge alt süreçleri çıktıyı açık tuttuğu için dosyaya yönlendirme kilitleniyordu (27.11.2026);
    # çıktı doğrudan akıştan, tüm alt süreçler kapanana kadar okunur (en fazla 90 sn).
    $arg = @("--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", "--mute-audio",
             "--user-data-dir=`"$Klasor\edge`"", "--virtual-time-budget=15000", "--dump-dom", "`"$url`"")
    try {
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = $Edge
        $psi.Arguments = ($arg -join " ")
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
        $p = [Diagnostics.Process]::Start($psi)
        $cikis = $p.StandardOutput.ReadToEndAsync()
        $null = $p.StandardError.ReadToEndAsync()
        if ($cikis.Wait(90000)) { return $cikis.Result }
        try { & taskkill.exe /PID $p.Id /T /F | Out-Null } catch { }
        Yaz "Edge zaman aşımı ($url)"
    } catch { Yaz "Edge hatası ($url): $($_.Exception.Message)" }
    return ""
}

function DuzMetin($html) {
    $h = [regex]::Replace($html, "(?is)<(script|style|noscript|svg|head|nav|footer)\b.*?</\1>", " ")
    $h = [regex]::Replace($h, "(?i)<br\s*/?>|</(p|div|li|tr|h\d|section|article)>", "`n")
    $h = [regex]::Replace($h, "(?s)<[^>]+>", " ")
    $h = [Net.WebUtility]::HtmlDecode($h)
    $h = [regex]::Replace($h, "[ \t ]+", " ")
    $h = [regex]::Replace($h, "(\s*\n\s*)+", "`n")
    return $h.Trim()
}

function Baslik($html) {
    foreach ($d in @("(?is)<h1\b[^>]*>(.*?)</h1>", "(?is)<title\b[^>]*>(.*?)</title>")) {
        $m = [regex]::Match($html, $d)
        if ($m.Success) { $t = DuzMetin $m.Groups[1].Value; if ($t.Length -gt 5) { return $t } }
    }
    return ""
}

function Baglantilar($html, $taban) {
    $sonuc = @()
    foreach ($m in [regex]::Matches($html, "(?is)<a\b[^>]*?href\s*=\s*[""']([^""'#]+)[""'][^>]*>(.*?)</a>")) {
        try { $u = [Uri]::new([Uri]$taban, [Net.WebUtility]::HtmlDecode($m.Groups[1].Value)).AbsoluteUri } catch { continue }
        $yazi = (DuzMetin $m.Groups[2].Value) -replace "\s+", " "
        $sonuc += [pscustomobject]@{ url = $u; yazi = $yazi }
    }
    return $sonuc
}

$Gorulen = New-Object 'System.Collections.Generic.HashSet[string]'
if (Test-Path $GorulenDosya) { Get-Content $GorulenDosya -Encoding UTF8 | ForEach-Object { [void]$Gorulen.Add($_) } }

$kayit = [ordered]@{ zaman = (Get-Date).ToString("s"); bilgisayar = $env:COMPUTERNAME; surum = 2; kaynaklar = [ordered]@{} }

# ---------------------------------------------------------------- 1) Kariyer Kapısı
$kk = Getir "POST" "https://api.kariyerkapisi.gov.tr/api/ilan/GetIseAlimPage" @{ krM_ID = 0; searchText = ""; il = "0"; ilanTuru = "0" }
$kkVeri = $null
if ($kk.durum -eq 200) { try { $kkVeri = $kk.metin | ConvertFrom-Json } catch { $kk.hata = "JSON okunamadı" } }
$ilanSayisi = 0
if ($kkVeri -and $kkVeri.searchIlan) { $ilanSayisi = @($kkVeri.searchIlan).Count }
$kayit.kaynaklar["kariyerkapisi"] = [ordered]@{ durum = $kk.durum; hata = $kk.hata; ilan_sayisi = $ilanSayisi }
Yaz "Kariyer Kapısı: HTTP $($kk.durum) $($kk.hata) · $ilanSayisi ilan"
$kayit["kariyerkapisi_ilanlar"] = if ($kkVeri) { $kkVeri.searchIlan } else { @() }
if (GhYaz "pc/kariyerkapisi.json" ($kayit | ConvertTo-Json -Depth 12) "pc: Kariyer Kapısı okuması $(Get-Date -Format 'yyyy-MM-dd HH:mm')") {
    Yaz "Kariyer Kapısı listesi GitHub'a yüklendi."
}

# ---------------------------------------------------------------- 2) GitHub'ın açamadığı sayfalar (Edge ile)
$gorevler = @()
$g = GhOku "pc/pc_gorevleri.json"
if ($g) { try { $gorevler = @(($g.metin | ConvertFrom-Json).gorevler) } catch { Yaz "pc_gorevleri.json okunamadı" } }
# ilan.gov.tr personel listesi her zaman (dosya yoksa da)
if (-not ($gorevler | Where-Object { $_.anahtar -eq "ilangovtr" })) {
    $gorevler = @([pscustomobject]@{ anahtar = "ilangovtr"; url = @("https://www.ilan.gov.tr/ilan/tum-ilanlar/personel-alimi?ats=5")
        href_deseni = "/ilan/\d{6,}/"; baglanti_deseni = ""; haric = "tebligat|ihale|kiralama|sat[ıi][şs]|icra|acente"; limit = 40
        liste_sayfa_deseni = "personel-alimi.*[?&](p|page|sayfa|currentpage|pagenumber|pageindex)=\d+"; liste_sayfa_sayisi = 3 }) + $gorevler
}
if (-not $Edge) { Yaz "Edge bulunamadı; sayfa görevleri atlandı." ; $gorevler = @() }
Yaz "Sayfa görevleri: $(@($gorevler).Count) görev, Edge: $Edge"

$yeniKayitlar = @()
$gorevDurumu = [ordered]@{}
foreach ($gv in $gorevler) {
    if ($AcilanSayfa -ge $SayfaSiniri -or ((Get-Date) - $Basla).TotalMinutes -gt $SureSiniriDk) {
        $gorevDurumu[$gv.anahtar] = "süre/sayfa sınırı doldu, atlandı"; continue
    }
    $listeHtml = ""; $listeUrl = ""
    foreach ($u in @($gv.url)) {
        if (-not (RobotsIzin $u)) { $gorevDurumu[$gv.anahtar] = "robots.txt izin vermiyor"; continue }
        $html = EdgeDom $u
        Yaz "[$($gv.anahtar)] liste sayfası: $($html.Length) karakter ($u)"
        if ($html.Length -gt 500) { $listeHtml = $html; $listeUrl = $u; break }
    }
    if (-not $listeHtml) { if (-not $gorevDurumu.Contains($gv.anahtar)) { $gorevDurumu[$gv.anahtar] = "sayfa açılmadı" }; continue }
    $alan = ([Uri]$listeUrl).Host -replace "^www\.", ""
    # Listenin sonraki sayfaları (görevde 'liste_sayfa_deseni' varsa; ör. ilan.gov.tr ilk sayfada ~13 ilan gösteriyor)
    if ($gv.liste_sayfa_deseni) {
        $ekSayfa = @()
        foreach ($b in (Baglantilar $listeHtml $listeUrl)) {
            if ($b.url -ne $listeUrl -and [regex]::IsMatch($b.url, $gv.liste_sayfa_deseni, "IgnoreCase") -and
                ([Uri]$b.url).Host -eq ([Uri]$listeUrl).Host -and -not ($ekSayfa -contains $b.url)) { $ekSayfa += $b.url }
        }
        $azami = if ($gv.liste_sayfa_sayisi) { [int]$gv.liste_sayfa_sayisi } else { 2 }
        if (-not $ekSayfa) { Yaz "[$($gv.anahtar)] sonraki liste sayfası bağlantısı bulunamadı (yalnızca ilk sayfa okundu)" }
        foreach ($su in ($ekSayfa | Select-Object -First $azami)) {
            if (-not (RobotsIzin $su)) { continue }
            $h2 = EdgeDom $su
            Yaz "[$($gv.anahtar)] ek liste sayfası: $($h2.Length) karakter ($su)"
            if ($h2.Length -gt 500) { $listeHtml += "`n" + $h2 }
        }
    }
    $adaylar = @()
    foreach ($b in (Baglantilar $listeHtml $listeUrl)) {
        $bh = ([Uri]$b.url).Host
        if (-not ($bh -eq $alan -or $bh.EndsWith("." + $alan))) { continue }
        if ($Gorulen.Contains($b.url) -or ($adaylar | Where-Object { $_.url -eq $b.url })) { continue }
        $yk = Kucuk $b.yazi
        if ($gv.href_deseni -and -not [regex]::IsMatch($b.url, $gv.href_deseni, "IgnoreCase")) { continue }
        if ($gv.baglanti_deseni -and ($b.yazi.Length -lt 12 -or -not [regex]::IsMatch($yk, $gv.baglanti_deseni, "IgnoreCase, Singleline"))) { continue }
        if ($gv.haric -and ([regex]::IsMatch($yk, $gv.haric, "IgnoreCase, Singleline") -or [regex]::IsMatch((Kucuk $b.url), $gv.haric, "IgnoreCase"))) { continue }
        $adaylar += $b
    }
    $limit = if ($gv.limit) { [int]$gv.limit } else { 8 }
    $alinan = 0
    foreach ($b in ($adaylar | Select-Object -First $limit)) {
        if ($AcilanSayfa -ge $SayfaSiniri -or ((Get-Date) - $Basla).TotalMinutes -gt $SureSiniriDk) { break }
        if (-not (RobotsIzin $b.url)) { continue }
        $html = EdgeDom $b.url
        if ($html.Length -lt 500) { continue }
        $metin = DuzMetin $html
        if ($metin.Length -gt 20000) { $metin = $metin.Substring(0, 20000) }
        $bas = Baslik $html; if (-not $bas) { $bas = $b.yazi }
        $yeniKayitlar += [pscustomobject]@{ anahtar = $gv.anahtar; url = $b.url; liste = $listeUrl; baslik = $bas
            baglanti_yazisi = $b.yazi; metin = $metin; zaman = (Get-Date).ToString("s"); ek = $gv.ek }
        $alinan++
    }
    $gorevDurumu[$gv.anahtar] = "açıldı: $(@($adaylar).Count) yeni bağlantı, $alinan sayfa alındı"
    Yaz "[$($gv.anahtar)] $($gorevDurumu[$gv.anahtar])"
}

# Sonuçları GitHub'daki pc/pc_sonuclar.json ile birleştir (son 14 gün, en fazla 300 kayıt)
$eski = @()
$s = GhOku "pc/pc_sonuclar.json"
if ($s) { try { $eski = @(($s.metin | ConvertFrom-Json).kayitlar) } catch { } }
$sinir = (Get-Date).AddDays(-14).ToString("s")
$hepsi = @($eski | Where-Object { $_.zaman -ge $sinir }) + $yeniKayitlar
if ($hepsi.Count -gt 300) { $hepsi = $hepsi | Select-Object -Last 300 }
$sonucJson = [ordered]@{ zaman = (Get-Date).ToString("s"); surum = 2; edge = [bool]$Edge; acilan_sayfa = $AcilanSayfa
                         gorevler = $gorevDurumu; kayitlar = @($hepsi) } | ConvertTo-Json -Depth 8
if (GhYaz "pc/pc_sonuclar.json" $sonucJson "pc: sayfa okuması $(Get-Date -Format 'yyyy-MM-dd HH:mm') ($(@($yeniKayitlar).Count) yeni)") {
    foreach ($k in $yeniKayitlar) { Add-Content -Path $GorulenDosya -Value $k.url -Encoding UTF8 }
    Yaz "Sayfa okuması GitHub'a yüklendi: $(@($yeniKayitlar).Count) yeni sayfa, $AcilanSayfa sayfa açıldı."
    (Get-Date).ToString("o") | Set-Content -Path $SonCalisma
} else { Yaz "Sayfa okuması yüklenemedi; bir sonraki çalışmada tekrar denenecek."; exit 1 }
