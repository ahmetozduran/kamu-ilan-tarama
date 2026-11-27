#!/usr/bin/env python3
"""
Kamu ilan tarama botu (GitHub Actions'ta her gün çalışır).  Sürüm 2

Ne yapar:
  1) ilan.memurlar.net'te son bakılan numaradan itibaren her ilanı sırayla açar.
     İlandaki görsel tabloları OCR ile, PDF ekleri pdftotext ile metne çevirir.
     OCR kalitesini ölçer; düşükse kayda "ocr_kalite: dusuk" yazar (doğrulama gerekir).
  2) İlan listesi veren sitelerin (haber/ilan siteleri, akademiktr) liste sayfalarındaki
     ilan bağlantılarını toplar, yeni olanların metnini kaydeder.
  3) Üniversitelerin ana sayfa / personel daire başkanlığı sayfalarındaki
     ilan-duyuru bağlantılarını toplar (öncelik Karadeniz ve çevresi).
  4) Her kaynağın o gün açılıp açılmadığını kaynak_durumu.json'a yazar.

Kurallar:
  - robots.txt'ye uyar; robots.txt 5xx/ağ hatası veren siteye girmez.
  - Erişime kapalı tutulan resmi portallara (ENGELLI_ALANLAR) hiç istek atmaz.
  - İstekler arasında bekler; toplam süre bütçesini aşmaz (SURE_LIMIT_DK).

Çıktılar (depoya yazılır):
  ilanlar/AAAA-AA/<kaynak>-<kimlik>.md   her ilanın tam metni (+ OCR/PDF metni)
  index.jsonl                            her ilan için tek satır özet (tarihler ISO)
  acik_ilanlar.md                        son başvurusu geçmemiş / tarihi okunamamış ilanlar
  ozet/AAAA-AA-GG.md                     o gün yeni bulunanlar + kaynak durumu
  kaynak_durumu.json                     son çalışmada her kaynağın durumu
  durum.json                             kaldığı yer (son numara, görülen adresler)
"""

import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.robotparser
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow yoksa OCR ön işleme atlanır
    Image = None

# ---------------------------------------------------------------- ayarlar
KOK = os.path.dirname(os.path.abspath(__file__))
DURUM_DOSYASI = os.path.join(KOK, "durum.json")
INDEX_DOSYASI = os.path.join(KOK, "index.jsonl")
KAYNAK_DOSYASI = os.path.join(KOK, "kaynak_durumu.json")
ACIK_DOSYASI = os.path.join(KOK, "acik_ilanlar.md")

UA = "kamu-ilan-tarama/2.0 (kisisel ilan takibi; github.com/ahmetozduran/kamu-ilan-tarama)"
BEKLEME = 1.5            # istekler arası saniye
ZAMAN_ASIMI = 20         # istek zaman aşımı (sn)
SURE_LIMIT_DK = float(os.environ.get("SURE_LIMIT_DK", "105"))  # toplam süre bütçesi
BASLANGIC_ZAMANI = time.time()

# durum.json yoksa buradan başlar. 86607 = 2 Ekim 2026, 86694 = 13 Ekim (OKA, son başvuru 22 Kasım).
# Başvuru süresi uzun ilanları da yakalamak için ~Eylül ortasından başlanır; ilk günlerde süre
# bütçesi dolarsa kalan numaralar ertesi günlere bölünür.
MEMURLAR_BASLANGIC = 86300
MEMURLAR_TUR_LIMIT = 600     # bir çalışmada en fazla kaç numara denensin
MEMURLAR_ARDISIK_BOS = 40    # art arda bu kadar YOK numara görülürse dur
MEMURLAR_ARDISIK_HATA = 15   # art arda bu kadar ağ/erişim hatası olursa site kapalı say, dur
MEMURLAR_PAY = 0.45          # süre bütçesinin en fazla bu kadarı memurlar.net'e

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

# Liste sayfaları: (kaynak adı, adres, bağlantı süzgeci)
# Süzgeç: bağlantı metninde ya da adresinde geçmesi gereken desen.
ILAN_DESENI = (r"(al[ıi]m|alacak|al[ıi]nacak|personel|memur|s[öo]zle[şs]meli|uzman|kadro|"
               r"ilan|[öo]ğretim|ara[şs]t[ıi]rma g[öo]revlisi|bili[şs]im|m[üu]fetti[şs]|denetç)")
LISTE_SAYFALARI = [
    # haber/ilan siteleri (21 Kasım 2026'da elle denendi: güncel kurum+kadro ilanı veriyor)
    ("posta", "https://www.posta.com.tr/haberleri/kamu-personel-alimi", ILAN_DESENI),
    ("milliyet", "https://www.milliyet.com.tr/haberleri/sozlesmeli-personel-alimi", ILAN_DESENI),
    ("bigpara", "https://bigpara.hurriyet.com.tr/haberleri/kamu-personeli", ILAN_DESENI),
    # ilan toplayıcı siteler (ana sayfa)
    ("memurjet", "https://www.memurjet.com/", ILAN_DESENI),
    ("kariyerbankasi", "https://kariyerbankasi.net/", ILAN_DESENI),
    ("kamukadro", "https://kamukadro.com/", ILAN_DESENI),
    ("memuralimilanlari", "https://memuralimilanlari.com/", ILAN_DESENI),
    ("isbilgileri", "https://www.isbilgileri.com/", ILAN_DESENI),
    # akademik (kadro tablosu metin olarak veriliyor)
    ("akademiktr", "https://akademiktr.com/ilan/ogretim-gorevlisi", r"/ilan/"),
    ("akademiktr", "https://akademiktr.com/bolum/matematik/ogretim-gorevlisi", r"/ilan/"),
    ("omu-mezun", "https://mezun.omu.edu.tr/tr/is-hayati/is-staj/akademik-ilanlar", ILAN_DESENI),
]

# Üniversite sayfaları: ana alan adı; ana sayfa + personel/pdb alt alanları denenir.
# Sıra = öncelik (Ordu'dan uzaklaşarak). Açılmayanlar kaynak_durumu.json'da görünür.
UNIVERSITELER = [
    ("Ordu", "odu.edu.tr"),
    ("Giresun", "giresun.edu.tr"),
    ("Samsun", "omu.edu.tr"), ("Samsun", "samsun.edu.tr"),
    ("Trabzon", "ktu.edu.tr"), ("Trabzon", "trabzon.edu.tr"),
    ("Amasya", "amasya.edu.tr"),
    ("Tokat", "gop.edu.tr"),
    ("Rize", "erdogan.edu.tr"),
    ("Gümüşhane", "gumushane.edu.tr"),
    ("Sinop", "sinop.edu.tr"),
    ("Sivas", "cumhuriyet.edu.tr"), ("Sivas", "sivas.edu.tr"),
    ("Bayburt", "bayburt.edu.tr"),
    ("Çorum", "hitit.edu.tr"),
    ("Artvin", "artvin.edu.tr"),
    ("Erzincan", "ebyu.edu.tr"),
    ("Erzurum", "atauni.edu.tr"),
    ("Kastamonu", "kastamonu.edu.tr"),
    ("Bartın", "bartin.edu.tr"),
    ("Karabük", "karabuk.edu.tr"),
    ("Zonguldak", "beun.edu.tr"),
    ("Bolu", "ibu.edu.tr"),
    ("Düzce", "duzce.edu.tr"),
    ("Yozgat", "bozok.edu.tr"),
]
# Tüm Türkiye (LogiX'in 27 Kasım 2026 talimatı). Karadeniz listesi her çalışmada taranır;
# diğerleri her çalışmada UNI_TUR_ADEDI kadar sırayla (durum.json'daki uni_sira kaldığı yeri tutar).
# Kaynak: Hipo/university-domains-list (TR) + elle eklenenler; eski/öğrenci alan adları çıkarıldı.
TUM_UNIVERSITELER = [
    '29mayis.edu.tr', 'acibadem.edu.tr', 'adiyaman.edu.tr', 'adu.edu.tr', 'afsu.edu.tr', 'agri.edu.tr',
    'agu.edu.tr', 'ahap.edu.tr', 'ahievran.edu.tr', 'akdeniz.edu.tr', 'aksaray.edu.tr', 'aku.edu.tr',
    'alanyaaku.edu.tr', 'alparslan.edu.tr', 'amasya.edu.tr', 'anadolu.edu.tr', 'ankara.edu.tr', 'ankarabilim.edu.tr',
    'ankaramedipol.edu.tr', 'antalya.edu.tr', 'ardahan.edu.tr', 'arel.edu.tr', 'artuklu.edu.tr', 'artvin.edu.tr',
    'asbu.edu.tr', 'atauni.edu.tr', 'atilim.edu.tr', 'atu.edu.tr', 'avrasya.edu.tr', 'aybu.edu.tr',
    'aydin.edu.tr', 'bahcesehir.edu.tr', 'bakircay.edu.tr', 'balikesir.edu.tr', 'bandirma.edu.tr', 'bartin.edu.tr',
    'baskent.edu.tr', 'batman.edu.tr', 'bayburt.edu.tr', 'beu.edu.tr', 'beun.edu.tr', 'beykent.edu.tr',
    'bezmialem.edu.tr', 'bilecik.edu.tr', 'bilgi.edu.tr', 'bilkent.edu.tr', 'bingol.edu.tr', 'biruni.edu.tr',
    'bitlis.edu.tr', 'bogazici.edu.tr', 'bozok.edu.tr', 'btu.edu.tr', 'cag.edu.tr', 'cankaya.edu.tr',
    'cbu.edu.tr', 'comu.edu.tr', 'cu.edu.tr', 'cumhuriyet.edu.tr', 'deu.edu.tr', 'dicle.edu.tr',
    'dogus.edu.tr', 'dpu.edu.tr', 'duzce.edu.tr', 'ebyu.edu.tr', 'ege.edu.tr', 'erbakan.edu.tr',
    'erciyes.edu.tr', 'erdogan.edu.tr', 'erzurum.edu.tr', 'esenyurt.edu.tr', 'eskisehir.edu.tr', 'etu.edu.tr',
    'fatihsultan.edu.tr', 'fbu.edu.tr', 'firat.edu.tr', 'gantep.edu.tr', 'gazi.edu.tr', 'gedik.edu.tr',
    'gelisim.edu.tr', 'gidatarim.edu.tr', 'giresun.edu.tr', 'gop.edu.tr', 'gsu.edu.tr', 'gtu.edu.tr',
    'gumushane.edu.tr', 'hacettepe.edu.tr', 'hakkari.edu.tr', 'halic.edu.tr', 'harran.edu.tr', 'hbv.edu.tr',
    'hitit.edu.tr', 'hku.edu.tr', 'hun.edu.tr', 'ibnhaldun.edu.tr', 'ibu.edu.tr', 'idu.edu.tr',
    'ieu.edu.tr', 'igdir.edu.tr', 'ikcu.edu.tr', 'iku.edu.tr', 'inonu.edu.tr', 'isikun.edu.tr',
    'isparta.edu.tr', 'istanbul.edu.tr', 'istanbulticaret.edu.tr', 'iste.edu.tr', 'istiklal.edu.tr', 'istinye.edu.tr',
    'iszu.edu.tr', 'itu.edu.tr', 'iuc.edu.tr', 'iyte.edu.tr', 'kafkas.edu.tr', 'kapadokya.edu.tr',
    'karabuk.edu.tr', 'karatay.edu.tr', 'karatekin.edu.tr', 'kastamonu.edu.tr', 'kayseri.edu.tr', 'kemerburgaz.edu.tr',
    'khas.edu.tr', 'kilis.edu.tr', 'kirklareli.edu.tr', 'kku.edu.tr', 'kmu.edu.tr', 'konya.edu.tr',
    'kou.edu.tr', 'ksbu.edu.tr', 'ksu.edu.tr', 'ktu.edu.tr', 'ktun.edu.tr', 'ku.edu.tr',
    'lokmanhekim.edu.tr', 'maltepe.edu.tr', 'marmara.edu.tr', 'medeniyet.edu.tr', 'medipol.edu.tr', 'mef.edu.tr',
    'mehmetakif.edu.tr', 'mersin.edu.tr', 'metu.edu.tr', 'mku.edu.tr', 'msgsu.edu.tr', 'msu.edu.tr',
    'mu.edu.tr', 'munzur.edu.tr', 'mus.edu.tr', 'nevsehir.edu.tr', 'nigde.edu.tr', 'nisantasi.edu.tr',
    'nku.edu.tr', 'nny.edu.tr', 'odu.edu.tr', 'ogu.edu.tr', 'ohu.edu.tr', 'okan.edu.tr',
    'omu.edu.tr', 'osmaniye.edu.tr', 'ostimteknik.edu.tr', 'ozal.edu.tr', 'ozu.edu.tr', 'ozyegin.edu.tr',
    'pau.edu.tr', 'pirireis.edu.tr', 'rumeli.edu.tr', 'sabanciuniv.edu', 'sakarya.edu.tr', 'samsun.edu.tr',
    'sanko.edu.tr', 'sau.edu.tr', 'sbu.edu.tr', 'sdu.edu.tr', 'selcuk.edu.tr', 'siirt.edu.tr',
    'sinop.edu.tr', 'sirnak.edu.tr', 'subu.edu.tr', 'tarsus.edu.tr', 'tau.edu.tr', 'tedu.edu.tr',
    'thk.edu.tr', 'ticaret.edu.tr', 'tinaztepe.edu.tr', 'topkapi.edu.tr', 'toros.edu.tr', 'trakya.edu.tr',
    'ufuk.edu.tr', 'uludag.edu.tr', 'usak.edu.tr', 'uskudar.edu.tr', 'yalova.edu.tr', 'yasar.edu.tr',
    'ybu.edu.tr', 'yeditepe.edu.tr', 'yeniyuzyil.edu.tr', 'yildiz.edu.tr', 'yuksekihtisasuniversitesi.edu.tr', 'yyu.edu.tr',
    'zonguldak.edu.tr',
]
UNI_TUR_ADEDI = 45
# Ana sayfa bazen 404/yönlendirme verir (ör. /tr); bu yüzden birkaç adres denenir.
UNI_ALT_ALANLAR = ["www.{d}/", "www.{d}/tr", "{d}/", "personel.{d}/", "pdb.{d}/"]
UNI_DESENI = (r"(ilan|al[ıi]m|[öo]ğretim (g[öo]revlisi|eleman|[üu]yesi)|akademik personel|"
              r"s[öo]zle[şs]meli|personel al|kadro|duyuru)")
UNI_HARIC = r"(yeme[ğk]|men[üu]|sat[ıi]n alma|ihale|kiralama|i[şs]g[üu]c[üu] uyum|sonu[çc]|kazanan|mezuniyet t[öo]ren|kutla|tebrik)"
UNI_LINK_LIMIT = 12          # üniversite başına en fazla kaç yeni bağlantı açılsın

# ---- Yerel kurumlar (LogiX'in 27 Kasım 2026 talimatı: 12 Karadeniz ili)
# Belediye, il özel idaresi, valilik/kaymakamlık (SYDV duyuruları dâhil), il/ilçe MEM.
# Alan adları kalıptan üretilir; açılmayanlar kaynak_durumu.json'da "açılmadı" görünür
# ve haftalık görev o kurumu WebSearch ile tamamlar.
ORDU_ILCELER = ["altinordu", "fatsa", "unye", "persembe", "golkoy", "korgan", "kumru",
                "aybasti", "ulubey", "mesudiye", "akkus", "caybasi", "ikizce", "kabaduz",
                "kabatas", "camas", "catalpinar", "gulyali", "gurgentepe"]
KARADENIZ_ILLER = [("Giresun", "giresun"), ("Samsun", "samsun"), ("Trabzon", "trabzon"),
                   ("Amasya", "amasya"), ("Tokat", "tokat"), ("Rize", "rize"),
                   ("Gümüşhane", "gumushane"), ("Sinop", "sinop"), ("Sivas", "sivas"),
                   ("Bayburt", "bayburt"), ("Çorum", "corum")]
OZEL_IDARELI = ["giresun", "amasya", "tokat", "rize", "gumushane", "sinop", "sivas", "bayburt", "corum"]
BUYUK_ILCELER = {
    "Samsun": ["atakum", "ilkadim", "canik", "tekkekoy", "bafra", "carsamba", "terme", "vezirkopru", "havza"],
    "Trabzon": ["ortahisar", "akcaabat", "of", "arakli", "yomra", "vakfikebir"],
    "Giresun": ["bulancak", "espiye", "gorele", "tirebolu", "sebinkarahisar"],
    "Tokat": ["erbaa", "niksar", "turhal", "zile"],
    "Amasya": ["merzifon", "suluova"],
    "Rize": ["cayeli", "ardesen"],
    "Sivas": ["susehri", "sarkisla"],
    "Çorum": ["sungurlu", "osmancik", "iskilip"],
    "Sinop": ["boyabat", "gerze"],
    "Gümüşhane": ["kelkit"],
}
# Konu: bağlantı metninde kadro/personel türü geçmeli
YEREL_KONU = (r"(memur|personel|s[öo]zle[şs]meli|zab[ıi]ta|itfaiye|istihdam|kadro|g[öo]revli|"
              r"sosyal yard[ıi]m ve inceleme|sydv|vakf|[öo]ğretmen|e[ğg]itmen|usta [öo]ğretici|"
              r"kurs [öo]ğret|et[üu]t merkez|eleman|uzman|b[üu]ro)")
# Eylem: ayrıca bir alım/ilan/başvuru sözcüğü geçmeli (yalnızca "Zabıta Müdürlüğü" menüsü,
# "Öğretmenler Günü mesajı" gibi bağlantılar 27.11.2026 taramasında boş yere kotayı dolduruyordu)
YEREL_EYLEM = (r"(al[ıi]m|al[ıi]nacak|alacak|al[ıi]yor|ilan|ba[şs]vuru|s[ıi]nav|istihdam|aran[ıi]yor|"
               r"ihtiya[çc]|g[öo]revlendir|i[şs]e al|m[üu]lakat|duyuru)")
PERSONEL_DESENI = rf"(sosyal yard[ıi]m ve inceleme g[öo]revlisi|^(?=.*{YEREL_KONU})(?=.*{YEREL_EYLEM}))"
MEM_DESENI = (r"([üu]cretli [öo]ğretmen|s[öo]zle[şs]meli [öo]ğretmen|usta [öo]ğretici|"
              rf"^(?=.*(matematik|[öo]ğretmen|personel|e[ğg]itmen))(?=.*{YEREL_EYLEM}))")
YEREL_HARIC = (r"(ihale|kiraya|kiralama|sat[ıi][şs] ilan|imar plan|meclis karar|asfalt|konser|festival|"
               r"etkinli|yar[ıi][şs]ma|anma|tebrik|ziyaret|[öo]ğretmenler g[üu]n|mesaj|kutla|"
               r"e[ğg]itimi ver|toplant|denetim|y[öo]netmeli|g[öo]revde y[üu]kselme|unvan de[ğg]i[şs]|"
               r"sonu[çc]|kazanan|ba[şs][ıi]m[ıi]z sa[ğg]|ta[şs][ıi]n[ıi]r mal|as[ıi]l.{0,3}yedek|zirve|projeler)")
YEREL_LINK_LIMIT = 8


def _yerel_listesi():
    k = []  # (il, tür, alan adı, desen)
    k.append(("Ordu", "belediye", "ordu.bel.tr", PERSONEL_DESENI))
    k.append(("Ordu", "valilik", "ordu.gov.tr", PERSONEL_DESENI))
    k.append(("Ordu", "mem", "ordu.meb.gov.tr", MEM_DESENI))
    for d in ORDU_ILCELER:
        k.append(("Ordu", "belediye", f"{d}.bel.tr", PERSONEL_DESENI))
        k.append(("Ordu", "kaymakamlik", f"{d}.gov.tr", PERSONEL_DESENI))
        k.append(("Ordu", "mem", f"{d}.meb.gov.tr", MEM_DESENI))
    for il, d in KARADENIZ_ILLER:
        k.append((il, "belediye", f"{d}.bel.tr", PERSONEL_DESENI))
        k.append((il, "valilik", f"{d}.gov.tr", PERSONEL_DESENI))
        k.append((il, "mem", f"{d}.meb.gov.tr", MEM_DESENI))
        if d in OZEL_IDARELI:
            k.append((il, "ozelidare", f"{d}ozelidare.gov.tr", PERSONEL_DESENI))
        for ilce in BUYUK_ILCELER.get(il, []):
            k.append((il, "belediye", f"{ilce}.bel.tr", PERSONEL_DESENI))
    return k


YEREL_KURUMLAR = _yerel_listesi()

# ---- Türkiye geneli yerel kurumlar (LogiX'in 27 Kasım 2026 talimatı: "her zaman tüm Türkiye")
# Karadeniz dışındaki 69 ilde il belediyesi, valilik, il MEM ve (büyükşehir olmayan illerde) il özel idaresi.
# Her çalışmada YEREL_TUR_ADEDI kadarı sırayla taranır (durum.json'da yerel_sira).
BUYUKSEHIRLER = {"Adana", "Ankara", "Antalya", "Aydın", "Balıkesir", "Bursa", "Denizli", "Diyarbakır",
                 "Erzurum", "Eskişehir", "Gaziantep", "Hatay", "İstanbul", "İzmir", "Kahramanmaraş",
                 "Kayseri", "Kocaeli", "Konya", "Malatya", "Manisa", "Mardin", "Mersin", "Muğla", "Ordu",
                 "Sakarya", "Samsun", "Tekirdağ", "Trabzon", "Şanlıurfa", "Van"}
IL_SLUG_OZEL = {"Afyonkarahisar": "afyon"}
YEREL_TUR_ADEDI = 70


def _slug(ad):
    t = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return IL_SLUG_OZEL.get(ad, ad.translate(t).lower().replace(" ", ""))


def _turkiye_yerel_listesi():
    karadeniz = {"Ordu"} | {il for il, _ in KARADENIZ_ILLER}
    k = []
    for il in ILLER:
        if il in karadeniz:
            continue
        d = _slug(il)
        tam = il.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")).lower()
        k.append((il, "belediye", "ibb.istanbul" if il == "İstanbul" else f"{d}.bel.tr", PERSONEL_DESENI))
        k.append((il, "valilik", f"{tam}.gov.tr", PERSONEL_DESENI))
        k.append((il, "mem", f"{d}.meb.gov.tr", MEM_DESENI))
        if il not in BUYUKSEHIRLER:
            k.append((il, "ozelidare", f"{d}ozelidare.gov.tr", PERSONEL_DESENI))
    return k


YEREL_TURKIYE = None  # main() içinde doldurulur (ILLER aşağıda tanımlı)
YEREL_ALT_YOLLAR = ["", "duyurular", "ilanlar"]

# ---- Bilgisayar okuyucusu görevleri (LogiX'in 27 Kasım 2026 talimatı: GitHub'dan açılmayanlar otomatik
# bilgisayara kaysın). Bu çalışmada GitHub'dan açılamayan (robots engeli OLMAYAN) sayfalar pc/pc_gorevleri.json'a
# yazılır; LogiX'in bilgisayarındaki pc/kk_tara.ps1 bunları Edge ile açıp pc/pc_sonuclar.json olarak geri yükler.
PC_GOREVLERI = [{
    "anahtar": "ilangovtr", "url": ["https://www.ilan.gov.tr/ilan/tum-ilanlar/personel-alimi?ats=5"],
    "href_deseni": r"/ilan/\d{6,}/", "baglanti_deseni": "",
    "haric": r"tebligat|ihale|kiralama|sat[ıi][şs]|icra|acente", "limit": 40, "ek": {},
    "liste_sayfa_deseni": r"personel-alimi.*[?&](p|page|sayfa|currentpage|pagenumber|pageindex)=\d+",
    "liste_sayfa_sayisi": 3}]


def pc_gorev_ekle(anahtar, urller, desen, haric, limit, ek=None):
    if kaynak[anahtar]["robots"]:
        return   # robots.txt yasaklıyorsa bilgisayardan da açılmaz
    if any(g["anahtar"] == anahtar for g in PC_GOREVLERI):
        return
    PC_GOREVLERI.append({"anahtar": anahtar, "url": urller, "href_deseni": "", "baglanti_deseni": desen,
                         "haric": haric or "", "limit": limit, "ek": ek or {}})

ILLER = ["Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya", "Ankara", "Antalya",
         "Ardahan", "Artvin", "Aydın", "Balıkesir", "Bartın", "Batman", "Bayburt", "Bilecik",
         "Bingöl", "Bitlis", "Bolu", "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli",
         "Diyarbakır", "Düzce", "Edirne", "Elazığ", "Erzincan", "Erzurum", "Eskişehir", "Gaziantep",
         "Giresun", "Gümüşhane", "Hakkari", "Hatay", "Iğdır", "Isparta", "İstanbul", "İzmir",
         "Kahramanmaraş", "Karabük", "Karaman", "Kars", "Kastamonu", "Kayseri", "Kilis",
         "Kırıkkale", "Kırklareli", "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa",
         "Mardin", "Mersin", "Muğla", "Muş", "Nevşehir", "Niğde", "Ordu", "Osmaniye", "Rize",
         "Sakarya", "Samsun", "Şanlıurfa", "Siirt", "Sinop", "Şırnak", "Sivas", "Tekirdağ", "Tokat",
         "Trabzon", "Tunceli", "Uşak", "Van", "Yalova", "Yozgat", "Zonguldak"]
# İl adı geçmeyen kurum adları -> il
KURUM_IL = {
    "ondokuz mayıs": "Samsun", "karadeniz teknik": "Trabzon", "recep tayyip erdoğan": "Rize",
    "gaziosmanpaşa": "Tokat", "hitit": "Çorum", "sivas cumhuriyet": "Sivas", "cumhuriyet üniversitesi": "Sivas",
    "atatürk üniversitesi": "Erzurum", "bülent ecevit": "Zonguldak", "abant izzet baysal": "Bolu",
    "bozok": "Yozgat", "binali yıldırım": "Erzincan", "çoruh": "Artvin", "doğu karadeniz kalkınma": "Trabzon",
    "orta karadeniz kalkınma": "Samsun", "fatsa": "Ordu", "ünye": "Ordu", "altınordu": "Ordu",
    "bafra": "Samsun", "çarşamba": "Samsun", "atakum": "Samsun", "ilkadım": "Samsun",
}
AYLAR = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
         "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
         "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12}

# ---------------------------------------------------------------- yardımcılar
oturum = requests.Session()
oturum.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
_robots = {}
_son_istek = {}  # site -> son istek zamanı (siteye göre nazik bekleme)
gunluk_yeni = []  # bu çalışmada bulunanlar
kaynak = defaultdict(lambda: {"istek": 0, "ok": 0, "kayit": 0, "kodlar": {}, "robots": 0,
                              "hata": 0, "not": ""})


def log(*a):
    print(*a, flush=True)


def gecen_dk():
    return (time.time() - BASLANGIC_ZAMANI) / 60


def sure_bitti(pay=1.0):
    return gecen_dk() >= SURE_LIMIT_DK * pay


def bugun():
    return dt.date.today()


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
                rp.parse([])                   # robots yok -> serbest (RFC 9309)
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots[kok] = rp
    rp = _robots[kok]
    return bool(rp and rp.can_fetch(UA, url))


def getir(url, kaynak_adi=None):
    """Sayfayı getirir. Engelli/robots yasaklıysa ya da hata olursa None döner.
    404/410 yanıtı nesne olarak döner (memurlar.net'te 'böyle ilan yok' demektir)."""
    k = kaynak[kaynak_adi] if kaynak_adi else None
    if engelli_mi(url):
        log("  atlandı (engelli alan):", url)
        return None
    if not robots_izin(url):
        log("  atlandı (robots):", url)
        if k is not None:
            k["robots"] += 1
        return None
    host = urlparse(url).hostname or ""
    gecen = time.time() - _son_istek.get(host, 0)
    if gecen < BEKLEME:
        time.sleep(BEKLEME - gecen)
    _son_istek[host] = time.time()
    if k is not None:
        k["istek"] += 1
    try:
        r = oturum.get(url, timeout=ZAMAN_ASIMI, allow_redirects=True)
    except requests.RequestException as e:
        log("  hata:", url, type(e).__name__)
        if k is not None:
            k["hata"] += 1
        return None
    if k is not None:
        kod = str(r.status_code)
        k["kodlar"][kod] = k["kodlar"].get(kod, 0) + 1
        if r.status_code == 200:
            k["ok"] += 1
    if r.status_code != 200:
        log("  durum", r.status_code, url)
        return r if r.status_code in (404, 410) else None
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


# ---------------------------------------------------------------- tarih / il / tür
def tarih_iso(metin):
    """'4 Aralık 2026', '04.12.2026', '2026-12-04T..' -> '2026-12-04'."""
    if not metin:
        return ""
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", metin)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b", metin)
    if m:
        return _gun_yap(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(20\d\d)\b", metin)
    if m:
        ay = AYLAR.get(m.group(2).lower().replace("i̇", "i"))
        if ay:
            return _gun_yap(int(m.group(3)), ay, int(m.group(1)))
    return ""


def _gun_yap(yil, ay, gun):
    """Geçersiz günü (ilan metnindeki '31 Kasım' gibi yazım hataları) ay sonuna çeker."""
    if not (1 <= ay <= 12 and 1 <= gun <= 31):
        return ""
    import calendar
    gun = min(gun, calendar.monthrange(yil, ay)[1])
    return dt.date(yil, ay, gun).isoformat()


_TARIH = r"(\d{1,2}[./]\d{1,2}[./]20\d\d|\d{1,2}\s+[a-zçğıöşü]+\s+20\d\d)"


def son_tarih_ara(metin):
    """Başlıkta son başvuru yoksa metinden bulmaya çalışır (ör. 'son başvuru tarihi (11.12.2026)',
    '23/11/2026 tarihinden başlayarak 06/12/2026', '2 Kasım 2026 ile 16 Kasım 2026 tarihleri arasında')."""
    k = kucuk(metin or "")
    m = re.search(r"son\s+ba[şs]vuru[^0-9\n]{0,40}?" + _TARIH, k)
    if m and tarih_iso(m.group(1)):
        return m.group(1)
    m = re.search(_TARIH + r"[^0-9\n]{0,60}?(?:-|–|ile|ila|ba[şs]layarak|itibaren)[^0-9\n]{0,40}?" + _TARIH, k)
    if m and tarih_iso(m.group(2)):
        return m.group(2)
    m = re.search(r"(\d{1,2})\s*(?:-|–)\s*(\d{1,2})\s+([a-zçğıöşü]+)\s+(20\d\d)", k)  # '7-10 aralık 2026'
    if m and tarih_iso(f"{m.group(2)} {m.group(3)} {m.group(4)}"):
        return f"{m.group(2)} {m.group(3)} {m.group(4)}"
    return ""


def kucuk(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def il_bul(baslik, metin):
    for parca in (baslik, metin[:800]):
        kp = kucuk(parca)
        for anahtar, il in KURUM_IL.items():
            if anahtar in kp:
                return il
        for il in ILLER:
            if re.search(r"(?<![a-zçğıöşü])" + re.escape(kucuk(il)), kp):
                return il
    return ""


def tur_bul(baslik):
    b = kucuk(baslik)
    if re.search(r"i̇şçi|işçi|isci", b):
        return "isci"
    if re.search(r"öğretim (üyesi|elemanı|görevlisi)|araştırma görevlisi|akademik", b):
        return "akademik"
    return "kamu"


# ---------------------------------------------------------------- OCR / PDF
def ocr_kalite(metin):
    """Kaba kalite ölçüsü: anlamlı görünen sözcük oranı. 0..1"""
    sozcukler = re.findall(r"\S+", metin)
    if len(sozcukler) < 15:
        return 0.0
    iyi = sum(1 for s in sozcukler
              if re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}[.,:;)]?|\(?\d+[.,)]?|[\d.,/-]+", s))
    return round(iyi / len(sozcukler), 2)


def _tesseract(yol, psm):
    out = subprocess.run(["tesseract", yol, "stdout", "-l", "tur+eng", "--psm", str(psm)],
                         capture_output=True, text=True, timeout=180)
    return out.stdout.strip()


def ocr_gorsel(icerik):
    """Görseli büyütüp gri tona çevirerek OCR'lar; iki sayfa düzeninden iyisini alır.
    Döner: (metin, kalite)"""
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "g.png")
        try:
            if Image is not None:
                im = Image.open(io.BytesIO(icerik))
                im = ImageOps.grayscale(im)
                if im.width < 2000:
                    oran = 2000 / im.width
                    im = im.resize((2000, int(im.height * oran)), Image.LANCZOS)
                im = ImageOps.autocontrast(im)
                im.save(yol)
            else:
                with open(yol, "wb") as f:
                    f.write(icerik)
            en_iyi, en_iyi_k = "", -1.0
            for psm in (6, 4):
                m = _tesseract(yol, psm)
                k = ocr_kalite(m)
                if k > en_iyi_k:
                    en_iyi, en_iyi_k = m, k
                if en_iyi_k >= 0.6:   # yeterliyse ikinci düzeni deneme (hız)
                    break
            return en_iyi, en_iyi_k
        except Exception as e:
            return f"[OCR hatası: {e}]", 0.0


def pdf_metin(icerik):
    """Döner: (metin, kalite). Metin katmanı yoksa sayfaları OCR'lar."""
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "a.pdf")
        with open(yol, "wb") as f:
            f.write(icerik)
        try:
            out = subprocess.run(["pdftotext", "-layout", yol, "-"],
                                 capture_output=True, text=True, timeout=180)
            metin = out.stdout.strip()
        except Exception as e:
            return f"[PDF hatası: {e}]", 0.0
        if len(metin) >= 200:
            return metin, 1.0
        try:
            subprocess.run(["pdftoppm", "-r", "250", "-png", yol, os.path.join(d, "s")],
                           capture_output=True, timeout=300)
            parcalar, kaliteler = [], []
            for ad in sorted(os.listdir(d)):
                if ad.startswith("s") and ad.endswith(".png"):
                    with open(os.path.join(d, ad), "rb") as f:
                        m, k = ocr_gorsel(f.read())
                    parcalar.append(m)
                    kaliteler.append(k)
            k = round(sum(kaliteler) / len(kaliteler), 2) if kaliteler else 0.0
            return (metin + "\n" + "\n".join(parcalar)).strip(), k
        except Exception as e:
            return metin + f"\n[PDF OCR hatası: {e}]", 0.0


GORSEL_ATTR = ("data-src", "data-original", "data-lazy-src", "src")
GORSEL_RE = re.compile(r"\.(jpe?g|png|gif|webp)(\?|$)", re.I)
ATLA_RE = re.compile(r"(headline|thumb|logo|icon|avatar|banner|reklam|ads?/|sprite)", re.I)


def gorsel_adresleri(soup, sayfa_url, anahtar=None):
    adresler = []
    for img in soup.find_all(["img", "amp-img", "source"]):
        adaylar = [img.get(a) for a in GORSEL_ATTR if img.get(a)]
        if img.get("srcset"):
            adaylar += [p.strip().split(" ")[0] for p in img["srcset"].split(",")]
        for src in adaylar:
            adresler.append(urljoin(sayfa_url, src))
    for a in soup.find_all("a", href=True):          # görsele verilen bağlantılar
        if GORSEL_RE.search(a["href"]):
            adresler.append(urljoin(sayfa_url, a["href"]))
    sonuc, gorulen = [], set()
    for u in adresler:
        if u in gorulen or not GORSEL_RE.search(u) or ATLA_RE.search(u):
            continue
        if anahtar and anahtar not in u:
            continue
        gorulen.add(u)
        sonuc.append(u)
    return sonuc


def ekleri_coz(soup, sayfa_url, kaynak_adi, anahtar=None, gorsel=True, pdf=True):
    """Sayfadaki ilan görsellerini ve PDF eklerini metne çevirir.
    Döner: (ek_metni, en_dusuk_kalite veya None, görsel adresleri)"""
    parcalar, kaliteler = [], []
    gorseller = gorsel_adresleri(soup, sayfa_url, anahtar)
    for u in (gorseller[:12] if gorsel else []):
        r = getir(u, kaynak_adi)
        if r is not None and r.status_code == 200 and len(r.content) > 5000:
            m, k = ocr_gorsel(r.content)
            kaliteler.append(k)
            parcalar.append(f"\n--- GÖRSEL (OCR, kalite {k}): {u}\n{m}")
    gorulen = set()
    for a in soup.find_all("a", href=True):
        u = urljoin(sayfa_url, a["href"])
        if not pdf or not re.search(r"\.pdf(\?|$)", u, re.I) or u in gorulen:
            continue
        gorulen.add(u)
        if len(gorulen) > 8:
            break
        r = getir(u, kaynak_adi)
        if r is not None and r.status_code == 200:
            m, k = pdf_metin(r.content)
            kaliteler.append(k)
            parcalar.append(f"\n--- PDF (kalite {k}): {u}\n{m}")
    return "\n".join(parcalar), (min(kaliteler) if kaliteler else None), gorseller


def bul(desen, metin):
    m = re.search(desen, metin, re.I)
    return m.group(1).strip() if m else ""


def kalite_etiketi(k):
    if k is None:
        return ""
    return "iyi" if k >= 0.75 else ("orta" if k >= 0.55 else "dusuk")


def kaydet(kaynak_adi, kimlik, url, baslik, metin, ek_metin, meta, sayac=None):
    ay = bugun().strftime("%Y-%m")
    klasor = os.path.join(KOK, "ilanlar", ay)
    os.makedirs(klasor, exist_ok=True)
    dosya = os.path.join(klasor, f"{kaynak_adi}-{kimlik}.md")
    tum = baslik + "\n" + metin + "\n" + (ek_metin or "")
    meta.setdefault("il", il_bul(baslik, metin))
    meta.setdefault("tur", tur_bul(baslik))
    if not meta.get("son_basvuru_iso"):
        sb = meta.get("son_basvuru") or bul(
            r"son\s+ba[şs]vuru(?:\s+tarihi)?\s*:?\s*([0-9]{1,2}[\s./]+[A-Za-zÇĞİÖŞÜçğıöşü0-9]+[\s./]+20[0-9]{2})", tum)
        if not tarih_iso(sb):
            aday = son_tarih_ara(tum)
            # ilan girişinden önceki bir tarih yanlış eşleşmedir (ör. '01 Ekim 2025 – 01 Ekim 2026' konu aralığı)
            esik = meta.get("ilan_giris_iso") or bugun().isoformat()   # giriş tarihi yoksa yalnızca ileri tarih
            if aday and tarih_iso(aday) >= esik:
                # son_basvuru_iso boş kalır (ilan 'tarihi okunamadı' sayılır, elenmez); tahmin ayrı alanda
                meta["son_basvuru_tahmini"] = tarih_iso(aday)
        meta["son_basvuru"] = sb
        meta["son_basvuru_iso"] = tarih_iso(sb)
    with open(dosya, "w", encoding="utf-8") as f:
        f.write(f"# {baslik}\n\n")
        f.write(f"- kaynak: {kaynak_adi}\n- adres: {url}\n"
                f"- alındı: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n")
        for k, v in meta.items():
            if v:
                f.write(f"- {k}: {v if not isinstance(v, list) else ' '.join(v)}\n")
        f.write("\n## Metin\n\n" + metin + "\n")
        if ek_metin:
            f.write("\n## Ekler (görsel/PDF metni)\n" + ek_metin + "\n")
    kayit = {"tarih": bugun().isoformat(), "kaynak": kaynak_adi, "kimlik": str(kimlik),
             "adres": url, "baslik": baslik, "dosya": os.path.relpath(dosya, KOK),
             **{k: v for k, v in meta.items() if k != "gorseller"},
             "gorsel_sayisi": len(meta.get("gorseller") or [])}
    with open(INDEX_DOSYASI, "a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    gunluk_yeni.append(kayit)
    kaynak[sayac or kaynak_adi]["kayit"] += 1


def durumu_yaz(durum):
    with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 1) memurlar.net
def memurlar(durum):
    ad = "memurlar"
    son = durum.get("memurlar_son", MEMURLAR_BASLANGIC)
    tekrar = [x for x in durum.get("memurlar_tekrar", []) if x <= son]  # önceki hatalılar
    log(f"[memurlar] {son + 1} numarasından başlanıyor; {len(tekrar)} numara yeniden denenecek")
    hatali = []
    for n in tekrar:
        if sure_bitti(MEMURLAR_PAY):
            hatali.append(n)
            continue
        if memurlar_tek(n) is None:
            hatali.append(n)
    bos, ardisik_hata, n, bulunan = 0, 0, son + 1, 0
    while n <= son + MEMURLAR_TUR_LIMIT and bos < MEMURLAR_ARDISIK_BOS:
        if sure_bitti(MEMURLAR_PAY):
            kaynak[ad]["not"] = f"süre bütçesi doldu, {n} numarasında kalındı"
            break
        sonuc = memurlar_tek(n)
        if sonuc is None:          # ağ/sunucu/erişim hatası: sonra tekrar dene
            hatali.append(n)
            ardisik_hata += 1
            if ardisik_hata >= MEMURLAR_ARDISIK_HATA:
                kaynak[ad]["not"] = f"art arda {ardisik_hata} erişim hatası: site bu çalışmada kapalı sayıldı"
                break
            n += 1
            continue
        ardisik_hata = 0
        if sonuc is False:         # böyle bir ilan yok
            bos += 1
            n += 1
            continue
        bos = 0
        bulunan += 1
        durum["memurlar_son"] = n
        if bulunan % 20 == 0:
            durumu_yaz(durum)
        n += 1
    son_bulunan = durum.get("memurlar_son", son)
    if bos >= MEMURLAR_ARDISIK_BOS and not kaynak[ad]["not"]:
        # Art arda 'ilan yok' (404) = en yeni ilana yetişildi; site açık sayılır
        kaynak[ad]["ok"] = max(kaynak[ad]["ok"], 1)
        kaynak[ad]["not"] = f"güncel: en yeni ilan {son_bulunan}, sonrası henüz yayımlanmamış"
    durum["memurlar_tekrar"] = sorted(set(x for x in hatali if x <= son_bulunan))[-300:]
    log(f"[memurlar] {bulunan} ilan kaydedildi, son numara {son_bulunan}, "
        f"{len(durum['memurlar_tekrar'])} numara sonraki tura kaldı")


def memurlar_tek(n):
    """True: kaydedildi, False: ilan yok, None: hata (sonra tekrar denenmeli)."""
    ad = "memurlar"
    url = f"https://ilan.memurlar.net/ilan/{n}/"
    r = getir(url, ad)
    if r is None:
        return None
    if r.status_code != 200:
        return False
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    baslik = h1.get_text(" ", strip=True) if h1 else (
        soup.title.string.strip() if soup.title and soup.title.string else "")
    # Var olmayan numara ana sayfaya ya da başka ilana yönlenebilir
    if f"/ilan/{n}/" not in r.url or not baslik:
        return False
    tam = temiz_metin(BeautifulSoup(r.text, "html.parser"))
    metin = temiz_metin(ana_icerik(BeautifulSoup(r.text, "html.parser")))
    sb_on = tarih_iso(bul(r"Son Ba[şs]vuru(?: Tarihi)?\s*:?\s*([0-9]{1,2}[\s./]+\w+[\s./]+[0-9]{4})", tam))
    # Hız: başvurusu kapanmış ya da işçi ilanında tablo OCR'ı gerekmez (görsel adresleri yine kaydedilir)
    ocr_gerek = not ((sb_on and sb_on < bugun().isoformat()) or tur_bul(baslik) == "isci")
    # Görseller memurlar.net'te ilan numarasını içeren yollarda durur
    # (ör. /common/job/images/<n>/...). Kapak görseli (headline) atlanır.
    ek, kalite, gorseller = ekleri_coz(BeautifulSoup(r.text, "html.parser"), r.url, ad,
                                       anahtar=f"/{n}/", gorsel=ocr_gerek, pdf=ocr_gerek)
    if not gorseller and ocr_gerek:   # AMP sürümünde görseller farklı etiketle olabilir
        r2 = getir(r.url.split("?")[0] + "?amp=1", ad)
        if r2 is not None and r2.status_code == 200:
            ek2, k2, g2 = ekleri_coz(BeautifulSoup(r2.text, "html.parser"), r2.url, ad,
                                     anahtar=f"/{n}/", gorsel=ocr_gerek, pdf=ocr_gerek)
            if g2:
                ek, kalite, gorseller = ek2, k2, g2
    giris = bul(r"[İI]lan Giri[şs]\s*:?\s*([0-9]{1,2}\s+\w+\s+[0-9]{4}|20\d\d-\d\d-\d\d)", tam)
    sb = bul(r"Son Ba[şs]vuru(?: Tarihi)?\s*:?\s*([0-9]{1,2}[\s./]+\w+[\s./]+[0-9]{4})", tam)
    meta = {
        "ilan_no": n,
        "ilan_giris": giris,
        "ilan_giris_iso": tarih_iso(giris),
        "son_basvuru": sb,
        "son_basvuru_iso": tarih_iso(sb),
        "kategori": bul(r"Kategori\s*:?\s*([^\n]{2,60})", tam),
        "kaynak_notu": bul(r"Kaynak\s*:\s*([^\n]{2,40})", tam),
        "ocr_kalite": kalite_etiketi(kalite) if ocr_gerek else ("atlandi" if gorseller else ""),
        "gorseller": gorseller,
    }
    kaydet(ad, n, r.url, baslik, metin, ek, meta)
    return True


# ---------------------------------------------------------------- 2) liste sayfaları
def liste_sayfalari(durum):
    gorulen = set(durum.get("gorulen", []))
    for ad, ana, desen in LISTE_SAYFALARI:
        if sure_bitti(0.60):
            kaynak[ad]["not"] = "süre bütçesi doldu, atlandı"
            continue
        r = getir(ana, ad)
        if r is None or r.status_code != 200:
            kaynak[ad]["not"] = kaynak[ad]["not"] or "liste sayfası açılmadı"
            pc_gorev_ekle(ad, [ana], desen, None, 20)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        host = urlparse(r.url).hostname
        adaylar = []
        for a in soup.find_all("a", href=True):
            u = urljoin(r.url, a["href"]).split("#")[0]
            yazi = a.get_text(" ", strip=True)
            if urlparse(u).hostname != host or u.rstrip("/") == ana.rstrip("/"):
                continue
            if len(yazi) < 15 or not (re.search(desen, yazi, re.I) or re.search(desen, u, re.I)):
                continue
            h = hashlib.sha1(u.encode()).hexdigest()[:16]
            if h in gorulen or any(h == x[2] for x in adaylar):
                continue
            adaylar.append((u, yazi, h))
        log(f"[{ad}] {len(adaylar)} yeni bağlantı")
        if not adaylar and not kaynak[ad]["not"]:
            kaynak[ad]["not"] = "açıldı, yeni bağlantı yok"
        for u, yazi, h in adaylar[:40]:
            if sure_bitti(0.60):
                break
            r2 = getir(u, ad)
            gorulen.add(h)
            if r2 is None or r2.status_code != 200:
                continue
            s2 = BeautifulSoup(r2.text, "html.parser")
            h1 = s2.find("h1")
            baslik = h1.get_text(" ", strip=True) if h1 else yazi
            metin = temiz_metin(ana_icerik(BeautifulSoup(r2.text, "html.parser")))
            ek, kalite, gorseller = ekleri_coz(s2, r2.url, ad, gorsel=False)
            yayin = ""
            m = s2.find("meta", attrs={"property": "article:published_time"}) or \
                s2.find("meta", attrs={"name": "pubdate"})
            if m and m.get("content"):
                yayin = tarih_iso(m["content"])
            kaydet(ad, h, r2.url, baslik, metin[:60000], ek,
                   {"yayin_iso": yayin, "ocr_kalite": kalite_etiketi(kalite), "gorseller": gorseller})
    durum["gorulen"] = list(gorulen)[-12000:]


# ---------------------------------------------------------------- 3) kurum sayfaları (üniversite + yerel)
def _baglanti_topla(r, alan, desen, haric, gorulen, adaylar):
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        u = urljoin(r.url, a["href"]).split("#")[0]
        h_ = (urlparse(u).hostname or "")
        if not (h_ == alan or h_.endswith("." + alan)):
            continue
        yazi = a.get_text(" ", strip=True)
        yk = kucuk(yazi)  # Türkçe büyük harf (İ/I) için önce küçült
        if len(yazi) < 12 or not re.search(desen, yk, re.I | re.S):
            continue
        if haric and re.search(haric, yk, re.I | re.S):
            continue
        h = hashlib.sha1(u.encode()).hexdigest()[:16]
        if h in gorulen or any(h == x[2] for x in adaylar):
            continue
        adaylar.append((u, yazi, h))


def _adaylari_kaydet(ad, il, adaylar, gorulen, limit, ek_meta):
    for u, yazi, h in adaylar[:limit]:
        gorulen.add(h)
        meta = ({"il": il} if il else {}) | ek_meta
        if re.search(r"\.pdf(\?|$)", u, re.I):
            r2 = getir(u, ad)
            if r2 is not None and r2.status_code == 200:
                m, k = pdf_metin(r2.content)
                kaydet(ad.replace(":", "-"), h, u, yazi, "", f"--- PDF (kalite {k}): {u}\n{m}",
                       {**meta, "ocr_kalite": kalite_etiketi(k)}, sayac=ad)
            continue
        r2 = getir(u, ad)
        if r2 is None or r2.status_code != 200:
            continue
        s2 = BeautifulSoup(r2.text, "html.parser")
        h1 = s2.find("h1")
        baslik = h1.get_text(" ", strip=True) if h1 else yazi
        if len(baslik) < 8:
            baslik = yazi
        metin = temiz_metin(ana_icerik(BeautifulSoup(r2.text, "html.parser")))
        ek, kalite, gorseller = ekleri_coz(s2, r2.url, ad)
        kaydet(ad.replace(":", "-"), h, r2.url, baslik, metin[:60000], ek,
               {**meta, "ocr_kalite": kalite_etiketi(kalite), "gorseller": gorseller}, sayac=ad)


def universiteler(durum):
    gorulen = set(durum.get("uni_gorulen", []))
    karadeniz = {a for _, a in UNIVERSITELER}
    digerleri = [a for a in TUM_UNIVERSITELER if a not in karadeniz]
    sira = durum.get("uni_sira", 0) % max(1, len(digerleri))
    parti = (digerleri[sira:] + digerleri[:sira])[:UNI_TUR_ADEDI]
    durum["uni_sira"] = (sira + len(parti)) % max(1, len(digerleri))
    for il, alan in list(UNIVERSITELER) + [("", a) for a in parti]:
        ad = "uni:" + alan
        if sure_bitti(0.75):
            kaynak[ad]["not"] = "süre bütçesi doldu, atlandı"
            continue
        acilan, adaylar = 0, []
        for kalip in UNI_ALT_ALANLAR:
            if kalip.endswith("/tr") and acilan:
                continue   # ana sayfa zaten açıldıysa /tr denemesine gerek yok
            r = getir("https://" + kalip.format(d=alan), ad)
            if r is None or r.status_code != 200:
                continue
            acilan += 1
            _baglanti_topla(r, alan, UNI_DESENI, UNI_HARIC, gorulen, adaylar)
        if not acilan:
            kaynak[ad]["not"] = "ana/personel sayfası açılmadı"
            pc_gorev_ekle(ad, ["https://" + k.format(d=alan) for k in UNI_ALT_ALANLAR],
                          UNI_DESENI, UNI_HARIC, UNI_LINK_LIMIT, {"il": il} if il else {})
            continue
        log(f"[{ad}] {len(adaylar)} yeni bağlantı")
        if not adaylar:
            kaynak[ad]["not"] = "açıldı, yeni duyuru yok"
        _adaylari_kaydet(ad, il, adaylar, gorulen, UNI_LINK_LIMIT, {})
    durum["uni_gorulen"] = list(gorulen)[-8000:]


def yerel_kurumlar(durum):
    """12 Karadeniz ilinde belediye, il özel idaresi, valilik/kaymakamlık ve MEM duyuruları."""
    global YEREL_TURKIYE
    if YEREL_TURKIYE is None:
        YEREL_TURKIYE = _turkiye_yerel_listesi()
    gorulen = set(durum.get("yerel_gorulen", []))
    sira = durum.get("yerel_sira", 0) % max(1, len(YEREL_TURKIYE))
    parti = (YEREL_TURKIYE[sira:] + YEREL_TURKIYE[:sira])[:YEREL_TUR_ADEDI]
    durum["yerel_sira"] = (sira + len(parti)) % max(1, len(YEREL_TURKIYE))
    for il, tur, alan, desen in list(YEREL_KURUMLAR) + parti:
        ad = f"yerel:{tur}:{alan}"
        if sure_bitti(0.95):
            kaynak[ad]["not"] = "süre bütçesi doldu, atlandı"
            continue
        taban = None
        for on in ("https://www.", "https://"):
            r = getir(on + alan + "/", ad)
            if r is not None and r.status_code == 200:
                taban = r.url
                break
        if not taban:
            kaynak[ad]["not"] = "site açılmadı (alan adı yanlış olabilir)"
            pc_gorev_ekle(ad, [f"https://www.{alan}/", f"https://{alan}/"], desen, YEREL_HARIC,
                          YEREL_LINK_LIMIT, {"il": il, "kurum_turu": tur})
            continue
        adaylar = []
        _baglanti_topla(r, alan, desen, YEREL_HARIC, gorulen, adaylar)
        for yol in YEREL_ALT_YOLLAR[1:]:
            r = getir(urljoin(taban, "/" + yol), ad)
            if r is not None and r.status_code == 200:
                _baglanti_topla(r, alan, desen, YEREL_HARIC, gorulen, adaylar)
        log(f"[{ad}] {len(adaylar)} yeni bağlantı")
        if not adaylar:
            kaynak[ad]["not"] = "açıldı, yeni duyuru yok"
        _adaylari_kaydet(ad, il, adaylar, gorulen, YEREL_LINK_LIMIT, {"kurum_turu": tur})
    durum["yerel_gorulen"] = list(gorulen)[-12000:]


# ---------------------------------------------------------------- raporlar
def acik_ilanlari_yaz():
    """index.jsonl'dan son başvurusu geçmemiş ya da tarihi okunamamış (son 60 gün) kayıtlar."""
    if not os.path.exists(INDEX_DOSYASI):
        return 0
    bugun_s = bugun().isoformat()
    sinir = (bugun() - dt.timedelta(days=60)).isoformat()
    acik, belirsiz = [], []
    with open(INDEX_DOSYASI, encoding="utf-8") as f:
        for satir in f:
            try:
                k = json.loads(satir)
            except ValueError:
                continue
            if k.get("tur") == "isci":
                continue
            sb = k.get("son_basvuru_iso", "")
            if sb and sb >= bugun_s:
                acik.append(k)
            elif not sb and k.get("tarih", "") >= sinir:
                belirsiz.append(k)
    acik.sort(key=lambda k: k["son_basvuru_iso"])
    with open(ACIK_DOSYASI, "w", encoding="utf-8") as f:
        f.write(f"# Açık ilanlar — {bugun_s}\n\n")
        f.write(f"## Son başvurusu geçmemiş ({len(acik)})\n\n")
        for k in acik:
            f.write(f"- {k['son_basvuru_iso']} · {k.get('il') or '-'} · {k.get('tur')} · "
                    f"{k['baslik']} · ocr:{k.get('ocr_kalite') or '-'} · {k['adres']} (`{k['dosya']}`)\n")
        f.write(f"\n## Son başvuru tarihi okunamadı, son 60 günde alınmış ({len(belirsiz)})\n\n")
        for k in belirsiz:
            f.write(f"- {k.get('tarih')} · {k.get('il') or '-'} · {k.get('tur')} · {k['baslik']} · "
                    f"{k['adres']} (`{k['dosya']}`)\n")
    return len(acik)


def tarih_tamamla():
    """Eski kayıtlarda son başvuru tarihi boşsa ilan dosyasının metninden bir kez daha dener."""
    if not os.path.exists(INDEX_DOSYASI):
        return
    with open(INDEX_DOSYASI, encoding="utf-8") as f:
        satirlar = [json.loads(x) for x in f if x.strip()]
    degisen = 0
    for k in satirlar:
        if (k.get("son_basvuru_iso") or k.get("son_basvuru_tahmini") or not k.get("dosya")
                or not k.get("ilan_giris_iso")):
            continue
        yol = k["dosya"] if os.path.isabs(k["dosya"]) else os.path.join(KOK, k["dosya"])
        if not os.path.exists(yol):
            continue
        with open(yol, encoding="utf-8") as f:
            metin = f.read()
        sb = k.get("son_basvuru") or ""
        if tarih_iso(sb):
            k["son_basvuru_iso"] = tarih_iso(sb)  # ör. '31 Kasım 2026' artık ay sonuna çekiliyor
            degisen += 1
            continue
        aday = son_tarih_ara(metin)
        if aday and tarih_iso(aday) >= k["ilan_giris_iso"]:
            k["son_basvuru_tahmini"] = tarih_iso(aday)
            degisen += 1
    if degisen:
        with open(INDEX_DOSYASI, "w", encoding="utf-8") as f:
            for k in satirlar:
                f.write(json.dumps(k, ensure_ascii=False) + "\n")
        log(f"[tarih] {degisen} eski kayda son başvuru tarihi/tahmini eklendi")


def kariyerkapisi_pc(durum):
    """LogiX'in bilgisayarından (pc/kk_tara.ps1) yüklenen Kariyer Kapısı ilan listesini index'e ekler.
    Kariyer Kapısı'nın ilan servisi yurt dışından (GitHub) yanıt vermiyor; okuma Türkiye'deki
    bilgisayarda yapılıyor ve pc/kariyerkapisi.json olarak depoya geliyor."""
    ad = "kariyerkapisi-pc"
    yol = os.path.join(KOK, "pc", "kariyerkapisi.json")
    if not os.path.exists(yol):
        kaynak[ad]["not"] = "pc/kariyerkapisi.json yok (bilgisayar okuyucusu henüz çalışmadı)"
        return
    with open(yol, encoding="utf-8-sig") as f:
        veri = json.load(f)
    zaman = veri.get("zaman", "")[:16]
    kk = (veri.get("kaynaklar") or {}).get("kariyerkapisi") or {}
    yas_gun = (bugun() - dt.date.fromisoformat(zaman[:10])).days if zaman[:10] else 99
    if kk.get("durum") != 200:
        kaynak[ad]["not"] = f"bilgisayardaki son okuma başarısız (HTTP {kk.get('durum')}, {zaman})"
        return
    kaynak[ad]["ok"] = 1
    gorulen = set(durum.get("kk_gorulen", []))
    yeni = 0
    for x in veri.get("kariyerkapisi_ilanlar") or []:
        guid = x.get("guid") or ""
        bit = (x.get("bitTarih") or "")[:10]
        if not guid or guid in gorulen or (bit and bit < bugun().isoformat()):
            continue
        gorulen.add(guid)
        baslik = x.get("ilanBaslik") or x.get("kurumAdi") or "Kariyer Kapısı ilanı"
        metin = (f"Kurum: {x.get('kurumAdi', '')}\nBirim: {x.get('birimAdi', '')}\n"
                 f"İlan türü: {x.get('ilanTuru', '')}\nBaşvuru: {(x.get('basTarih') or '')[:10]} – {bit}\n"
                 f"Kaynak: Kariyer Kapısı kamu işe alım listesi (LogiX'in bilgisayarından {zaman} okuması). "
                 f"İlan metni ve şartlar Kariyer Kapısı'nda; aynı ilanı memurlar.net/haber/kurum sitesinde ara.")
        tur = "akademik" if re.search(r"[öo]ğretim|akademik", kucuk(x.get("ilanTuru", "") + baslik)) else tur_bul(baslik)
        kaydet(ad, guid, x.get("basvuruLinki") or "https://kariyerkapisi.gov.tr/isealim", baslik, metin, "",
               {"son_basvuru": bit, "son_basvuru_iso": bit, "baslangic_iso": (x.get("basTarih") or "")[:10],
                "kurum": x.get("kurumAdi", ""), "ilan_turu": x.get("ilanTuru", ""), "tur": tur,
                "kategori": "kariyerkapisi"})
        yeni += 1
    durum["kk_gorulen"] = list(gorulen)[-5000:]
    kaynak[ad]["not"] = (f"bilgisayar okuması {zaman}: {kk.get('ilan_sayisi')} aktif ilan, {yeni} yeni kayıt"
                         + (f" (okuma {yas_gun} gün eski)" if yas_gun >= 2 else ""))
    log(f"[{ad}] {kaynak[ad]['not']}")


def pc_sonuclari(durum):
    """Bilgisayar okuyucusunun Edge ile açtığı sayfaları (pc/pc_sonuclar.json) index'e ekler."""
    ad = "pc-sayfalar"
    yol = os.path.join(KOK, "pc", "pc_sonuclar.json")
    if not os.path.exists(yol):
        kaynak[ad]["not"] = "pc/pc_sonuclar.json yok (bilgisayar okuyucusu sürüm 2 henüz çalışmadı)"
        return
    with open(yol, encoding="utf-8-sig") as f:
        veri = json.load(f)
    kaynak[ad]["ok"] = 1
    gorulen = set(durum.get("pc_gorulen", []))
    yeni = 0
    for k in veri.get("kayitlar") or []:
        url = k.get("url") or ""
        h = hashlib.sha1(url.encode()).hexdigest()[:16]
        if not url or h in gorulen:
            continue
        gorulen.add(h)
        anahtar = k.get("anahtar") or "pc"
        ek = k.get("ek") or {}
        kaynak_adi = re.sub(r"[^a-z0-9.]+", "-", kucuk(anahtar)).strip("-") + "-pc"
        meta = {"bilgisayardan": k.get("zaman", ""), "liste": k.get("liste", "")}
        if ek.get("il"):
            meta["il"] = ek["il"]
        if ek.get("kurum_turu"):
            meta["kurum_turu"] = ek["kurum_turu"]
        kaydet(kaynak_adi, h, url, k.get("baslik") or k.get("baglanti_yazisi") or url,
               (k.get("metin") or "")[:60000], "", meta, sayac=anahtar)
        if anahtar in kaynak and not kaynak[anahtar]["ok"]:
            kaynak[anahtar]["not"] = "GitHub'dan açılmadı; bilgisayardan okundu"
        yeni += 1
    durum["pc_gorulen"] = list(gorulen)[-8000:]
    gd = veri.get("gorevler") or {}
    sorunlu = [a for a, d in gd.items() if not str(d).startswith("açıldı")]
    kaynak[ad]["not"] = (f"bilgisayar okuması {str(veri.get('zaman', ''))[:16]}: {len(gd)} görev, "
                         f"{yeni} yeni kayıt" + (f"; açılmayan: {', '.join(sorunlu[:10])}" if sorunlu else ""))
    log(f"[{ad}] {kaynak[ad]['not']}")


def pc_gorevlerini_yaz():
    os.makedirs(os.path.join(KOK, "pc"), exist_ok=True)
    with open(os.path.join(KOK, "pc", "pc_gorevleri.json"), "w", encoding="utf-8") as f:
        json.dump({"zaman": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "aciklama": "GitHub taramasının açamadığı (robots engeli olmayan) sayfalar; bilgisayar okuyucusu açar.",
                   "gorevler": PC_GOREVLERI}, f, ensure_ascii=False, indent=1)


def main():
    durum = {}
    if os.path.exists(DURUM_DOSYASI):
        with open(DURUM_DOSYASI, encoding="utf-8") as f:
            durum = json.load(f)
    tarih_tamamla()
    adimlar = [kariyerkapisi_pc, memurlar, liste_sayfalari, universiteler, yerel_kurumlar, pc_sonuclari]
    for adim in adimlar:
        try:
            adim(durum)
        except Exception as e:  # bir kaynak çökerse diğerleri devam etsin
            log(f"[{adim.__name__}] beklenmeyen hata: {e!r}")
            kaynak[adim.__name__]["not"] = f"beklenmeyen hata: {e!r}"[:200]
        durumu_yaz(durum)

    pc_gorevlerini_yaz()
    acik_sayi = acik_ilanlari_yaz()
    gun = bugun().isoformat()
    ozet_kaynak = {}
    for ad, k in sorted(kaynak.items()):
        durum_s = "açıldı" if k["ok"] else ("robots engeli" if k["robots"] and not k["istek"]
                                            else "açılmadı")
        ozet_kaynak[ad] = {**k, "durum": durum_s}
    with open(KAYNAK_DOSYASI, "w", encoding="utf-8") as f:
        json.dump({"tarih": gun, "sure_dk": round(gecen_dk(), 1),
                   "memurlar_son": durum.get("memurlar_son"), "kaynaklar": ozet_kaynak},
                  f, ensure_ascii=False, indent=1)

    os.makedirs(os.path.join(KOK, "ozet"), exist_ok=True)
    with open(os.path.join(KOK, "ozet", f"{gun}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {gun} — {len(gunluk_yeni)} yeni kayıt · açık ilan {acik_sayi} · "
                f"{round(gecen_dk())} dk\n\n## Kaynaklar\n\n")
        for ad, k in ozet_kaynak.items():
            f.write(f"- {ad}: {k['durum']} (istek {k['istek']}, 200: {k['ok']}, kayıt {k['kayit']}, "
                    f"kodlar {k['kodlar']}, robots {k['robots']}, ağ hatası {k['hata']}) {k['not']}\n")
        f.write("\n## Yeni kayıtlar\n\n")
        for k in gunluk_yeni:
            sb = f" — son başvuru: {k['son_basvuru_iso'] or k.get('son_basvuru', '')}" \
                if k.get("son_basvuru_iso") or k.get("son_basvuru") else ""
            oc = " — OCR düşük" if k.get("ocr_kalite") == "dusuk" else ""
            f.write(f"- [{k['kaynak']}] {k['baslik']}{sb}{oc} — {k['adres']} (`{k['dosya']}`)\n")
    log(f"Bitti: {len(gunluk_yeni)} yeni kayıt, {round(gecen_dk(), 1)} dk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
