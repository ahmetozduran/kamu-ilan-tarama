#!/usr/bin/env python3
"""
Kamu ilan tarama botu (GitHub Actions'ta her gün çalışır).

Ne yapar:
  1) ilan.memurlar.net'te son bakılan numaradan itibaren her ilanı sırayla açar.
     İlandaki görsel tabloları OCR ile, PDF ekleri pdftotext ile metne çevirir.
  2) Birkaç ilan sitesinin ana sayfasındaki personel alımı bağlantılarını toplar
     ve yeni olanların metnini kaydeder.
  3) Kamu ilanlarını takip eden herkese açık GitHub projelerinin son kayıtlarını alır.

Kurallar:
  - robots.txt'ye uyar; robots.txt okunamayan siteye girmez.
  - Erişime kapalı tutulan resmi portallara (ENGELLI_ALANLAR) hiç istek atmaz.
  - İstekler arasında bekler, siteleri yormaz.

Çıktılar (depoya yazılır):
  ilanlar/AAAA-AA/<kaynak>-<kimlik>.md   her ilanın tam metni (+ OCR/PDF metni)
  index.jsonl                            her ilan için tek satır özet
  ozet/AAAA-AA-GG.md                     o gün yeni bulunanların listesi
  durum.json                             kaldığı yer (son numara, görülen adresler)
"""

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- ayarlar
KOK = os.path.dirname(os.path.abspath(__file__))
DURUM_DOSYASI = os.path.join(KOK, "durum.json")
INDEX_DOSYASI = os.path.join(KOK, "index.jsonl")

UA = "kamu-ilan-tarama/1.0 (kisisel ilan takibi; github.com)"
BEKLEME = 1.5            # istekler arası saniye
ZAMAN_ASIMI = 30         # istek zaman aşımı (sn)

MEMURLAR_BASLANGIC = 86700   # durum.json yoksa buradan başlar (yaklaşık Ekim 2026 sonu)
MEMURLAR_TUR_LIMIT = 450     # bir çalışmada en fazla kaç numara denensin
MEMURLAR_ARDISIK_BOS = 40    # art arda bu kadar boş numara görülürse dur

# Hiç istek atılmayacak alanlar (bu alan adları ve alt alan adları)
ENGELLI_ALANLAR = [
    "kariyerkapisi.gov.tr",
    "cbiko.gov.tr",
    "ilan.gov.tr",
    "resmigazete.gov.tr",
    "kamuilan.sbb.gov.tr",
    "osym.gov.tr",
    "turkiye.gov.tr",
]

# Ana sayfasından personel alımı bağlantısı toplanacak siteler
TOPLAYICI_SITELER = [
    "https://www.memurjet.com/",
    "https://kariyerbankasi.net/",
    "https://kamukadro.com/",
    "https://memuralimilanlari.com/",
    "https://www.isbilgileri.com/",
]
ANAHTAR = re.compile(
    r"(al[ıi]m|alacak|personel|memur|s[öo]zle[şs]meli|uzman|kadro|ilan[ıi]|"
    r"[öo]ğretim|ara[şs]t[ıi]rma g[öo]revlisi|bili[şs]im)", re.I)

# Herkese açık ilan takip projeleri (GitHub API)
GITHUB_KAYNAKLARI = [
    ("issues", "kaan2635/memur-ilan-takip"),
    ("releases", "enesfeched-maker/kamu-ilan-takip"),
]

# ---------------------------------------------------------------- yardımcılar
oturum = requests.Session()
oturum.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
_robots = {}
gunluk_yeni = []  # bu çalışmada bulunanlar


def log(*a):
    print(*a, flush=True)


def engelli_mi(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ENGELLI_ALANLAR)


def robots_izin(url):
    p = urlparse(url)
    kok = f"{p.scheme}://{p.netloc}"
    if kok not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = oturum.get(kok + "/robots.txt", timeout=ZAMAN_ASIMI)
            if r.status_code >= 500:
                rp = None                      # okunamadı -> girme
            elif r.status_code >= 400:
                rp.parse([])                   # robots yok -> serbest
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots[kok] = rp
    rp = _robots[kok]
    return bool(rp and rp.can_fetch(UA, url))


def getir(url, ikili=False):
    """Sayfayı getirir. Engelli/robots yasaklıysa ya da hata olursa None döner."""
    if engelli_mi(url):
        log("  atlandı (engelli alan):", url)
        return None
    if not robots_izin(url):
        log("  atlandı (robots):", url)
        return None
    time.sleep(BEKLEME)
    try:
        r = oturum.get(url, timeout=ZAMAN_ASIMI, allow_redirects=True)
    except requests.RequestException as e:
        log("  hata:", url, e)
        return None
    if r.status_code != 200:
        log("  durum", r.status_code, url)
        return r if r.status_code == 404 else None
    return r


def temiz_metin(el):
    for t in el(["script", "style", "noscript", "nav", "header", "footer", "form", "iframe"]):
        t.decompose()
    satirlar = [s.strip() for s in el.get_text("\n").splitlines()]
    cikti, onceki = [], None
    for s in satirlar:
        if s and s != onceki:
            cikti.append(s)
        onceki = s
    return "\n".join(cikti)


def ana_icerik(soup):
    """Başlığın (h1) bulunduğu, yeterince metin içeren en küçük kapsayıcıyı alır."""
    h1 = soup.find("h1")
    el = h1
    while el is not None and el.parent is not None:
        el = el.parent
        if len(el.get_text(" ", strip=True)) > 1500:
            return el
    return soup.body or soup


def ocr_gorsel(icerik):
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "g")
        with open(yol, "wb") as f:
            f.write(icerik)
        try:
            out = subprocess.run(["tesseract", yol, "stdout", "-l", "tur+eng", "--psm", "6"],
                                 capture_output=True, text=True, timeout=180)
            return out.stdout.strip()
        except Exception as e:
            return f"[OCR hatası: {e}]"


def pdf_metin(icerik):
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "a.pdf")
        with open(yol, "wb") as f:
            f.write(icerik)
        try:
            out = subprocess.run(["pdftotext", "-layout", yol, "-"],
                                 capture_output=True, text=True, timeout=180)
            metin = out.stdout.strip()
        except Exception as e:
            return f"[PDF hatası: {e}]"
        if len(metin) < 200:  # taranmış PDF olabilir -> sayfaları OCR'la
            try:
                subprocess.run(["pdftoppm", "-r", "200", "-png", yol, os.path.join(d, "s")],
                               capture_output=True, timeout=300)
                parcalar = []
                for ad in sorted(os.listdir(d)):
                    if ad.startswith("s") and ad.endswith(".png"):
                        with open(os.path.join(d, ad), "rb") as f:
                            parcalar.append(ocr_gorsel(f.read()))
                metin = (metin + "\n" + "\n".join(parcalar)).strip()
            except Exception as e:
                metin += f"\n[PDF OCR hatası: {e}]"
        return metin


def ekleri_coz(soup, sayfa_url, sadece=None):
    """Sayfadaki ilan görsellerini ve PDF eklerini metne çevirir."""
    parcalar = []
    gorulen = set()
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        u = urljoin(sayfa_url, src)
        if not re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", u, re.I):
            continue
        if sadece and sadece not in u:
            continue
        if u in gorulen:
            continue
        gorulen.add(u)
        r = getir(u, ikili=True)
        if r is not None and r.status_code == 200 and len(r.content) > 5000:
            parcalar.append(f"\n--- GÖRSEL (OCR): {u}\n{ocr_gorsel(r.content)}")
    for a in soup.find_all("a", href=True):
        u = urljoin(sayfa_url, a["href"])
        if not re.search(r"\.pdf(\?|$)", u, re.I) or u in gorulen:
            continue
        gorulen.add(u)
        r = getir(u, ikili=True)
        if r is not None and r.status_code == 200:
            parcalar.append(f"\n--- PDF: {u}\n{pdf_metin(r.content)}")
    return "\n".join(parcalar)


def bul(desen, metin):
    m = re.search(desen, metin, re.I)
    return m.group(1).strip() if m else ""


def kaydet(kaynak, kimlik, url, baslik, metin, ek_metin, meta):
    ay = dt.date.today().strftime("%Y-%m")
    klasor = os.path.join(KOK, "ilanlar", ay)
    os.makedirs(klasor, exist_ok=True)
    dosya = os.path.join(klasor, f"{kaynak}-{kimlik}.md")
    with open(dosya, "w", encoding="utf-8") as f:
        f.write(f"# {baslik}\n\n")
        f.write(f"- kaynak: {kaynak}\n- adres: {url}\n- alındı: {dt.datetime.utcnow().isoformat()}Z\n")
        for k, v in meta.items():
            if v:
                f.write(f"- {k}: {v}\n")
        f.write("\n## Metin\n\n" + metin + "\n")
        if ek_metin:
            f.write("\n## Ekler (görsel/PDF metni)\n" + ek_metin + "\n")
    kayit = {"tarih": dt.date.today().isoformat(), "kaynak": kaynak, "kimlik": str(kimlik),
             "adres": url, "baslik": baslik, "dosya": os.path.relpath(dosya, KOK), **meta}
    with open(INDEX_DOSYASI, "a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    gunluk_yeni.append(kayit)


# ---------------------------------------------------------------- 1) memurlar.net
def memurlar(durum):
    son = durum.get("memurlar_son", MEMURLAR_BASLANGIC)
    tekrar = [x for x in durum.get("memurlar_tekrar", []) if x <= son]  # önceki hatalılar
    log(f"[memurlar] {son + 1} numarasından başlanıyor; {len(tekrar)} numara yeniden denenecek")
    hatali = []
    for n in tekrar:
        if not memurlar_tek(n):
            hatali.append(n)
    bos, n, bulunan = 0, son + 1, 0
    while n <= son + MEMURLAR_TUR_LIMIT and bos < MEMURLAR_ARDISIK_BOS:
        sonuc = memurlar_tek(n)
        if sonuc is None:          # ağ/sunucu hatası: sonra tekrar dene
            hatali.append(n)
            bos += 1
            n += 1
            continue
        if sonuc is False:         # böyle bir ilan yok
            bos += 1
            n += 1
            continue
        bos = 0
        bulunan += 1
        durum["memurlar_son"] = n
        n += 1
    son_bulunan = durum.get("memurlar_son", son)
    durum["memurlar_tekrar"] = [x for x in hatali if x <= son_bulunan][-200:]
    log(f"[memurlar] {bulunan} ilan kaydedildi, son numara {son_bulunan}, "
        f"{len(durum['memurlar_tekrar'])} numara sonraki tura kaldı")


def memurlar_tek(n):
    """True: kaydedildi, False: ilan yok, None: hata (sonra tekrar denenmeli)."""
    if True:
        url = f"https://ilan.memurlar.net/ilan/{n}/"
        r = getir(url)
        if r is None:
            return None
        if r.status_code != 200:
            return False
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.find("h1")
        baslik = h1.get_text(" ", strip=True) if h1 else (soup.title.string.strip() if soup.title and soup.title.string else "")
        # Var olmayan numara ana sayfaya ya da başka ilana yönlenebilir
        if f"/ilan/{n}/" not in r.url or not baslik:
            return False
        tam = temiz_metin(BeautifulSoup(r.text, "html.parser"))
        metin = temiz_metin(ana_icerik(soup))
        ek = ekleri_coz(BeautifulSoup(r.text, "html.parser"), r.url, sadece=f"/documents/{n}/")
        meta = {
            "ilan_giris": bul(r"[İI]lan Giri[şs]\s*:?\s*([0-9]{1,2}\s+\w+\s+[0-9]{4}[^\n]{0,8})", tam),
            "son_basvuru": bul(r"Son Ba[şs]vuru(?: Tarihi)?\s*:?\s*([0-9]{1,2}[\s./]+\w+[\s./]+[0-9]{4})", tam),
            "kategori": bul(r"Kategori\s*:?\s*([^\n]{2,60})", tam),
        }
        kaydet("memurlar", n, r.url, baslik, metin, ek, meta)
        return True


# ---------------------------------------------------------------- 2) toplayıcı siteler
def toplayicilar(durum):
    gorulen = set(durum.get("gorulen", []))
    for ana in TOPLAYICI_SITELER:
        r = getir(ana)
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        host = urlparse(ana).hostname
        adaylar = []
        for a in soup.find_all("a", href=True):
            u = urljoin(r.url, a["href"]).split("#")[0]
            yazi = a.get_text(" ", strip=True)
            if urlparse(u).hostname != host or len(yazi) < 15 or not ANAHTAR.search(yazi):
                continue
            h = hashlib.sha1(u.encode()).hexdigest()[:16]
            if h in gorulen:
                continue
            adaylar.append((u, yazi, h))
        log(f"[{host}] {len(adaylar)} yeni bağlantı")
        for u, yazi, h in adaylar[:40]:
            r2 = getir(u)
            gorulen.add(h)
            if r2 is None or r2.status_code != 200:
                continue
            s2 = BeautifulSoup(r2.text, "html.parser")
            h1 = s2.find("h1")
            baslik = h1.get_text(" ", strip=True) if h1 else yazi
            metin = temiz_metin(ana_icerik(BeautifulSoup(r2.text, "html.parser")))
            ek = ekleri_coz(s2, r2.url)
            kaydet(host.replace("www.", ""), h, r2.url, baslik, metin[:60000], ek, {})
    durum["gorulen"] = list(gorulen)[-8000:]


# ---------------------------------------------------------------- 3) GitHub takip projeleri
def github_kaynaklari(durum):
    token = os.environ.get("GITHUB_TOKEN")
    bas = {"Accept": "application/vnd.github+json"}
    if token:
        bas["Authorization"] = f"Bearer {token}"
    gorulen = set(durum.get("gh_gorulen", []))
    for tur, depo in GITHUB_KAYNAKLARI:
        url = f"https://api.github.com/repos/{depo}/{tur}?per_page=30" + ("&state=all" if tur == "issues" else "")
        try:
            r = requests.get(url, headers=bas, timeout=ZAMAN_ASIMI)
            r.raise_for_status()
        except requests.RequestException as e:
            log("[github] hata", depo, e)
            continue
        yeni = 0
        for o in r.json():
            k = f"{depo}#{o.get('id')}"
            if k in gorulen:
                continue
            gorulen.add(k)
            baslik = o.get("title") or o.get("name") or o.get("tag_name") or ""
            govde = o.get("body") or ""
            kaydet("github-" + depo.split("/")[0], o.get("id"), o.get("html_url", ""), baslik, govde, "",
                   {"yayin": o.get("created_at") or o.get("published_at") or ""})
            yeni += 1
        log(f"[github] {depo}: {yeni} yeni kayıt")
    durum["gh_gorulen"] = list(gorulen)[-3000:]


# ---------------------------------------------------------------- ana akış
def main():
    durum = {}
    if os.path.exists(DURUM_DOSYASI):
        with open(DURUM_DOSYASI, encoding="utf-8") as f:
            durum = json.load(f)
    adimlar = [memurlar, toplayicilar, github_kaynaklari]
    for adim in adimlar:
        try:
            adim(durum)
        except Exception as e:  # bir kaynak çökerse diğerleri devam etsin
            log(f"[{adim.__name__}] beklenmeyen hata: {e!r}")
        with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(durum, f, ensure_ascii=False, indent=1)

    gun = dt.date.today().isoformat()
    os.makedirs(os.path.join(KOK, "ozet"), exist_ok=True)
    with open(os.path.join(KOK, "ozet", f"{gun}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {gun} — {len(gunluk_yeni)} yeni kayıt\n\n")
        for k in gunluk_yeni:
            sb = f" — son başvuru: {k['son_basvuru']}" if k.get("son_basvuru") else ""
            f.write(f"- [{k['kaynak']}] {k['baslik']}{sb} — {k['adres']} (`{k['dosya']}`)\n")
    log(f"Bitti: {len(gunluk_yeni)} yeni kayıt")


if __name__ == "__main__":
    sys.exit(main())
