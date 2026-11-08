# Kazıyıcı

Bir web sitesinin yazılarını, fotoğraflarını, videolarını, seslerini ve belgelerini toplayıp analiz eden araç.

## Tarayıcı sürümü (`index.html`)

GitHub Pages açıksa doğrudan sitesinden, değilse dosyayı indirip çift tıklayarak açılır. Kurulum gerekmez.

- **Metin analizi:** kelime/cümle sayısı, okuma süresi, en sık kelimeler ve ikili ifadeler, başlık yapısı
- **Medya envanteri:** tür, biçim, çözünürlük, yön, dosya boyutları, en ağır dosyalar
- **SEO / erişilebilirlik:** sayfa başına 100 üzerinden puan ve eksikler
- **Bağlantı analizi:** iç/dış bağlantılar, en çok bağlantı verilen alan adları, metinsiz bağlantılar

Tarayıcılar başka sitelerden doğrudan veri çekmeye izin vermediği için (CORS) istekler açık bir aracı sunucudan geçer. Çalışmayan sitelerde sayfayı `Ctrl+S` ile kaydedip "HTML yapıştır" bölümüne yükleyebilirsin.

## Python sürümü (`kaziyici.py`)

Dosyaları klasörlere ayırarak bilgisayara indirir.

```
pip install requests beautifulsoup4 lxml
python kaziyici.py https://ornek.com --derinlik 1
python kaziyici.py https://ornek.com --tarayici   # JavaScript'li siteler (Playwright gerekir)
```
