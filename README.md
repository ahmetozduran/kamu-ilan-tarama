# kamu-ilan-tarama

Her gün otomatik çalışan kamu ilanı toplayıcısı (GitHub Actions).

- **Kaynaklar:** ilan.memurlar.net'teki her ilan numarası tek tek açılır. Ayrıca birkaç ilan sitesi ve herkese açık ilan takip projeleri taranır.
- **Ekler:** ilanlardaki görsel tablolar OCR ile, PDF ekler pdftotext ile metne çevrilir.
- **Uyulan kurallar:** robots.txt'ye uyulur. Erişime kapalı resmi portallara (Kariyer Kapısı, ilan.gov.tr vb.) hiç istek atılmaz.
- **Çıktılar:**
  - `ilanlar/` klasöründe her ilanın tam metni durur.
  - `index.jsonl` her ilan için tek satırlık özet listesidir.
  - `ozet/` klasöründe o gün yeni bulunanlar listelenir.

Elle çalıştırmak için: **Actions** sekmesinde **ilan-tarama**'yı aç, **Run workflow**'a bas.
