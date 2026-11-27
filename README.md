# kamu-ilan-tarama

Her gün otomatik çalışan kamu ilanı toplayıcısı (GitHub Actions, 06:17 Türkiye saati).

- **Kaynaklar:**
  - ilan.memurlar.net'teki her ilan numarası tek tek açılır.
  - Haber ve ilan sitelerinin liste sayfaları ile akademiktr taranır.
  - Üniversiteler (tüm Türkiye, ~200): Karadeniz ve çevresindeki 25 üniversite her çalışmada, diğerleri her çalışmada 45'er sırayla (yaklaşık 2 günde bir tam tur) ana sayfa ve personel sayfalarından taranır.
  - Yerel kurumlar — tüm Türkiye: Karadeniz dışındaki 69 ilin belediye, valilik, il MEM ve il özel idaresi siteleri her çalışmada 70'er sırayla (yaklaşık 2 günde bir tam tur). Ayrıntılı olarak 12 Karadeniz ili: belediyeler (Ordu'nun 19 ilçesi ve diğer illerin büyük ilçeleri dâhil), il özel idareleri, valilik ve kaymakamlıklar (SYDV duyuruları dâhil), il ve ilçe milli eğitim müdürlükleri (ücretli/sözleşmeli öğretmen duyuruları). Yerel kayıtların `kurum_turu` alanı vardır.
- **Ekler:** Görsel tablolar büyütülüp OCR'lanır; PDF'ler pdftotext ile metne çevrilir. Her OCR için bir kalite puanı tutulur. `ocr_kalite: dusuk` olan ilan başka bir kaynaktan doğrulanmalıdır.
- **Uyulan kurallar:** robots.txt'ye uyulur. Erişime kapalı resmi portallara (Kariyer Kapısı, ilan.gov.tr vb.) hiç istek atılmaz.
- **Süre:** Tarama kendi süre bütçesiyle (95 dk) durur. Yarıda kalsa bile o ana kadar toplananlar commit edilir.
- **Çıktılar:**
  - `ilanlar/`: her ilanın tam metni.
  - `index.jsonl`: her ilan için tek satır özet. Alanlar: `son_basvuru_iso`, `son_basvuru_tahmini` (başlıkta tarih yoksa metinden çıkarılan tahmin; doğrulanmalı, `son_basvuru_iso` boş kalır), `ilan_giris_iso`, `il`, `tur` (kamu/akademik/isci), `ocr_kalite`.
  - `acik_ilanlar.md`: son başvurusu geçmemiş ilanlar ile tarihi okunamamış ilanlar.
  - `ozet/AAAA-AA-GG.md`: o gün yeni bulunanlar ve her kaynağın durumu.
  - `kaynak_durumu.json`: son çalışmada her kaynak açıldı mı, kaç istek atıldı, hangi HTTP kodları döndü.
  - `durum.json`: taramanın kaldığı yer.

Elle çalıştırmak için: **Actions** sekmesinde **ilan-tarama**'yı aç, **Run workflow**'a bas.

## Bilgisayar okuyucusu (Kariyer Kapısı)

Kariyer Kapısı'nın ilan servisi yurt dışından (GitHub sunucuları) yanıt vermiyor. Bu yüzden LogiX'in
Windows bilgisayarında `pc/kk_tara.ps1` çalışıyor (oturum açılışında ve her gün 09:00/18:00, en sık
4 saatte bir). Kamu işe alım sayfasının herkese açık ilan listesini bir kez okuyup `pc/kariyerkapisi.json`
olarak buraya yükler; `tara.py` bunu `kariyerkapisi-pc` kaynağıyla index'e ekler.
Sürüm 2'den itibaren ayrıca `pc/pc_gorevleri.json`'daki sayfaları (ilan.gov.tr personel listesi her zaman,
artı o gün GitHub'dan açılamayan ve robots.txt yasağı olmayan üniversite/yerel/liste sayfaları) Edge'i
ekran açmadan kullanarak okur ve `pc/pc_sonuclar.json`'a yükler; `tara.py` bunları `<kaynak>-pc`
adlarıyla index'e ekler. Betik her çalışmada GitHub'daki en yeni sürüme kendini günceller.
Kurulum: `irm https://raw.githubusercontent.com/ahmetozduran/kamu-ilan-tarama/main/pc/kur.ps1 | iex`
Kaldırma: `irm https://raw.githubusercontent.com/ahmetozduran/kamu-ilan-tarama/main/pc/kaldir.ps1 | iex`
