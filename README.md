# kamu-ilan-tarama

Her gün otomatik çalışan kamu ilanı toplayıcısı (GitHub Actions, 06:17 Türkiye saati).

- **Kaynaklar:**
  - ilan.memurlar.net'teki her ilan numarası tek tek açılır.
  - Haber ve ilan sitelerinin liste sayfaları ile akademiktr taranır.
  - Karadeniz ve çevresindeki üniversitelerin ana sayfası ile personel sayfaları taranır.
- **Ekler:** Görsel tablolar büyütülüp OCR'lanır; PDF'ler pdftotext ile metne çevrilir. Her OCR için bir kalite puanı tutulur. `ocr_kalite: dusuk` olan ilan başka bir kaynaktan doğrulanmalıdır.
- **Uyulan kurallar:** robots.txt'ye uyulur. Erişime kapalı resmi portallara (Kariyer Kapısı, ilan.gov.tr vb.) hiç istek atılmaz.
- **Süre:** Tarama kendi süre bütçesiyle (95 dk) durur. Yarıda kalsa bile o ana kadar toplananlar commit edilir.
- **Çıktılar:**
  - `ilanlar/`: her ilanın tam metni.
  - `index.jsonl`: her ilan için tek satır özet. Alanlar: `son_basvuru_iso`, `ilan_giris_iso`, `il`, `tur` (kamu/akademik/isci), `ocr_kalite`.
  - `acik_ilanlar.md`: son başvurusu geçmemiş ilanlar ile tarihi okunamamış ilanlar.
  - `ozet/AAAA-AA-GG.md`: o gün yeni bulunanlar ve her kaynağın durumu.
  - `kaynak_durumu.json`: son çalışmada her kaynak açıldı mı, kaç istek atıldı, hangi HTTP kodları döndü.
  - `durum.json`: taramanın kaldığı yer.

Elle çalıştırmak için: **Actions** sekmesinde **ilan-tarama**'yı aç, **Run workflow**'a bas.
