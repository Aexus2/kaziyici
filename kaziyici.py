#!/usr/bin/env python3
"""
kaziyici.py — Bir web sitesine girip yazıları, fotoğrafları, videoları,
sesleri ve belgeleri ayrı klasörlere indiren otomatik kazıyıcı.

Kullanım örnekleri:
    python kaziyici.py https://ornek.com
    python kaziyici.py https://ornek.com --derinlik 2 --max-sayfa 50
    python kaziyici.py https://ornek.com --tarayici        # JavaScript'li siteler için
    python kaziyici.py https://ornek.com --cikti indirilenler --bekleme 1.5

Çıktı yapısı:
    cikti/<alan-adi>/
        yazilar/      her sayfanın düz metni (.txt) ve başlık/paragraf yapısı (.md)
        fotograflar/  jpg, png, webp, gif, svg ...
        videolar/     mp4, webm, mov ...
        sesler/       mp3, wav, ogg ...
        belgeler/     pdf, docx, xlsx, zip ...
        rapor.json    hangi dosya hangi sayfadan, hangi adresten geldi
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- ayarlar

UZANTILAR = {
    "fotograflar": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico", ".tif", ".tiff", ".heic"},
    "videolar": {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi", ".ogv", ".m3u8"},
    "sesler": {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus"},
    "belgeler": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
                 ".csv", ".txt", ".zip", ".rar", ".7z", ".epub"},
}
MIME_ONEK = {"image/": "fotograflar", "video/": "videolar", "audio/": "sesler"}
MIME_BELGE = {"application/pdf", "application/zip", "application/msword", "text/csv",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "application/vnd.openxmlformats-officedocument.presentationml.presentation"}

TARAYICI_KIMLIGI = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 kaziyici/1.0"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- yardımcılar

def temiz_ad(s, maks=80):
    s = re.sub(r"[^\w\-.]+", "_", s, flags=re.UNICODE).strip("._")
    return (s or "dosya")[:maks]


def uzanti_bul(url):
    yol = urlparse(url).path.lower()
    _, uz = os.path.splitext(yol)
    return uz


def kategori_uzantidan(url):
    uz = uzanti_bul(url)
    for kat, kume in UZANTILAR.items():
        if uz in kume:
            return kat
    return None


def kategori_mimeden(mime):
    mime = (mime or "").split(";")[0].strip().lower()
    for onek, kat in MIME_ONEK.items():
        if mime.startswith(onek):
            return kat
    if mime in MIME_BELGE:
        return "belgeler"
    return None


def srcset_coz(deger):
    """srcset="a.jpg 1x, b.jpg 2x" -> en büyük olanı da dahil tüm adresler"""
    adresler = []
    for parca in (deger or "").split(","):
        parca = parca.strip()
        if parca:
            adresler.append(parca.split()[0])
    return adresler


CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


# ---------------------------------------------------------------- kazıyıcı

class Kaziyici:
    def __init__(self, baslangic, cikti, derinlik, max_sayfa, bekleme,
                 disari_cik, robots, tarayici, max_mb, sadece):
        self.baslangic = baslangic
        self.alan = urlparse(baslangic).netloc
        self.kok = os.path.join(cikti, temiz_ad(self.alan))
        self.derinlik = derinlik
        self.max_sayfa = max_sayfa
        self.bekleme = bekleme
        self.disari_cik = disari_cik
        self.max_bayt = int(max_mb * 1024 * 1024)
        self.sadece = sadece  # None ya da {"yazilar", "fotograflar", ...}
        self.tarayici_modu = tarayici

        self.oturum = requests.Session()
        self.oturum.headers.update({"User-Agent": TARAYICI_KIMLIGI,
                                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"})

        self.gezilen = set()
        self.indirilen = {}      # medya url -> yerel yol
        self.ozetler = set()     # aynı içerik iki kez kaydedilmesin
        self.rapor = {"baslangic": baslangic, "sayfalar": [], "dosyalar": [], "hatalar": []}

        self.robots = None
        if robots:
            self.robots = robotparser.RobotFileParser()
            self.robots.set_url(f"{urlparse(baslangic).scheme}://{self.alan}/robots.txt")
            try:
                self.robots.read()
            except Exception:
                self.robots = None

        self._pw = self._tarayici = None
        if tarayici:
            self._tarayiciyi_ac()

        for kat in ["yazilar", *UZANTILAR.keys()]:
            if self._istenen(kat):
                os.makedirs(os.path.join(self.kok, kat), exist_ok=True)

    # ------------------------------------------------ tarayıcı (JS'li siteler)
    def _tarayiciyi_ac(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("! Playwright kurulu değil: pip install playwright && playwright install chromium")
            sys.exit(1)
        self._pw = sync_playwright().start()
        self._tarayici = self._pw.chromium.launch(headless=True)
        self._baglam = self._tarayici.new_context(user_agent=TARAYICI_KIMLIGI, locale="tr-TR")

    def _tarayici_ile_al(self, url):
        sayfa = self._baglam.new_page()
        medya_ag = set()

        def yanit_yakala(yanit):
            kat = kategori_mimeden(yanit.headers.get("content-type", ""))
            if kat in ("videolar", "sesler", "fotograflar"):
                medya_ag.add(yanit.url)

        sayfa.on("response", yanit_yakala)
        try:
            sayfa.goto(url, wait_until="networkidle", timeout=45000)
            # tembel yüklenen (lazy-load) görseller için aşağı kaydır
            for _ in range(8):
                sayfa.mouse.wheel(0, 2500)
                sayfa.wait_for_timeout(400)
            html = sayfa.content()
            son_url = sayfa.url
        finally:
            sayfa.close()
        return html, son_url, medya_ag

    def kapat(self):
        if self._tarayici:
            self._tarayici.close()
        if self._pw:
            self._pw.stop()

    # ------------------------------------------------ kurallar
    def _istenen(self, kat):
        return self.sadece is None or kat in self.sadece

    def _izinli(self, url):
        if self.robots is None:
            return True
        try:
            return self.robots.can_fetch(TARAYICI_KIMLIGI, url)
        except Exception:
            return True

    def _ayni_site(self, url):
        n = urlparse(url).netloc
        return n == self.alan or n.endswith("." + self.alan) or self.alan.endswith("." + n)

    # ------------------------------------------------ ana döngü
    def calistir(self):
        kuyruk = deque([(self.baslangic, 0)])
        while kuyruk and len(self.gezilen) < self.max_sayfa:
            url, d = kuyruk.popleft()
            url = urldefrag(url)[0]
            if url in self.gezilen:
                continue
            if not self._izinli(url):
                log(f"  robots.txt izin vermiyor, atlandı: {url}")
                continue
            self.gezilen.add(url)
            log(f"[{len(self.gezilen)}/{self.max_sayfa}] sayfa: {url}")

            try:
                yeni_linkler = self._sayfayi_isle(url)
            except Exception as e:
                self.rapor["hatalar"].append({"url": url, "hata": str(e)})
                log(f"  ! hata: {e}")
                continue

            if d < self.derinlik:
                for link in yeni_linkler:
                    if link not in self.gezilen and (self.disari_cik or self._ayni_site(link)):
                        kuyruk.append((link, d + 1))
            time.sleep(self.bekleme)

        self._raporu_yaz()

    # ------------------------------------------------ tek sayfa
    def _sayfayi_isle(self, url):
        ag_medyasi = set()
        if self.tarayici_modu:
            html, son_url, ag_medyasi = self._tarayici_ile_al(url)
        else:
            y = self.oturum.get(url, timeout=30)
            y.raise_for_status()
            tur = y.headers.get("content-type", "")
            if "html" not in tur:
                # adres doğrudan bir medya dosyasıysa onu indir
                self._indir(url, sayfa=url)
                return []
            y.encoding = y.apparent_encoding or y.encoding
            html, son_url = y.text, y.url

        corba = BeautifulSoup(html, "lxml")
        baslik = (corba.title.string or "").strip() if corba.title and corba.title.string else ""

        medya = self._medya_adresleri(corba, son_url) | ag_medyasi
        linkler = self._sayfa_linkleri(corba, son_url)

        sayfa_kaydi = {"url": son_url, "baslik": baslik, "medya_sayisi": len(medya)}
        if self._istenen("yazilar"):
            sayfa_kaydi["yazi_dosyasi"] = self._yaziyi_kaydet(corba, son_url, baslik)
        self.rapor["sayfalar"].append(sayfa_kaydi)

        for m in sorted(medya):
            self._indir(m, sayfa=son_url)
        return linkler

    # ------------------------------------------------ yazılar
    def _yaziyi_kaydet(self, corba, url, baslik):
        govde = BeautifulSoup(str(corba), "lxml")
        for etiket in govde(["script", "style", "noscript", "template", "svg", "iframe"]):
            etiket.decompose()

        # Markdown: başlıklar, paragraflar, listeler, alıntılar sırasıyla
        md = [f"# {baslik or url}", f"_Kaynak: {url}_", ""]
        for el in govde.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "figcaption", "td"]):
            metin = " ".join(el.get_text(" ", strip=True).split())
            if not metin:
                continue
            if el.name[0] == "h" and el.name[1:].isdigit():
                md.append("#" * int(el.name[1]) + " " + metin)
            elif el.name == "li":
                md.append("- " + metin)
            elif el.name == "blockquote":
                md.append("> " + metin)
            elif el.name == "pre":
                md.append("```\n" + el.get_text() + "\n```")
            else:
                md.append(metin)
            md.append("")

        duz = "\n".join(s for s in (" ".join(l.split()) for l in govde.get_text("\n").splitlines()) if s)

        ad = temiz_ad(urlparse(url).path.strip("/").replace("/", "_") or "anasayfa")
        ad = f"{len(self.rapor['sayfalar'])+1:03d}_{ad}"
        hedef = os.path.join(self.kok, "yazilar", ad)
        with open(hedef + ".md", "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        with open(hedef + ".txt", "w", encoding="utf-8") as f:
            f.write(duz)
        return os.path.relpath(hedef + ".md", self.kok)

    # ------------------------------------------------ adres toplama
    def _medya_adresleri(self, corba, taban):
        bulunan = set()

        def ekle(a):
            if not a or a.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:")):
                return
            bulunan.add(urldefrag(urljoin(taban, a.strip()))[0])

        # görseller (tembel yükleme özellikleri dahil)
        for img in corba.find_all("img"):
            for oz in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "data-full"):
                ekle(img.get(oz))
            for oz in ("srcset", "data-srcset"):
                for a in srcset_coz(img.get(oz)):
                    ekle(a)
        for src in corba.select("picture source, video source, audio source, track"):
            ekle(src.get("src"))
            for a in srcset_coz(src.get("srcset")):
                ekle(a)
        for v in corba.find_all(["video", "audio", "embed"]):
            ekle(v.get("src"))
            ekle(v.get("poster"))
        for o in corba.find_all("object"):
            ekle(o.get("data"))
        # sosyal paylaşım önizlemeleri
        for meta in corba.find_all("meta"):
            ozellik = (meta.get("property") or meta.get("name") or "").lower()
            if ozellik in ("og:image", "og:image:url", "og:video", "og:video:url", "og:audio",
                           "twitter:image", "twitter:player:stream"):
                ekle(meta.get("content"))
        for link in corba.find_all("link", rel=True):
            rel = " ".join(link.get("rel")).lower()
            if "icon" in rel or "image_src" in rel:
                ekle(link.get("href"))
        # satır içi CSS arka planları
        for el in corba.find_all(style=True):
            for a in CSS_URL.findall(el["style"]):
                ekle(a)
        for st in corba.find_all("style"):
            for a in CSS_URL.findall(st.get_text()):
                if kategori_uzantidan(urljoin(taban, a)):
                    ekle(a)
        # medya dosyasına giden bağlantılar (pdf, mp4, jpg ...)
        for a in corba.find_all("a", href=True):
            tam = urljoin(taban, a["href"])
            if kategori_uzantidan(tam):
                ekle(a["href"])
        return bulunan

    def _sayfa_linkleri(self, corba, taban):
        linkler = []
        for a in corba.find_all("a", href=True):
            tam = urldefrag(urljoin(taban, a["href"]))[0]
            if urlparse(tam).scheme in ("http", "https") and not kategori_uzantidan(tam):
                linkler.append(tam)
        return linkler

    # ------------------------------------------------ indirme
    def _indir(self, url, sayfa):
        if url in self.indirilen:
            return
        self.indirilen[url] = None
        tahmin = kategori_uzantidan(url)
        if tahmin and not self._istenen(tahmin):
            return
        try:
            with self.oturum.get(url, timeout=60, stream=True, headers={"Referer": sayfa}) as y:
                y.raise_for_status()
                kat = kategori_mimeden(y.headers.get("content-type")) or tahmin
                if kat is None or not self._istenen(kat):
                    return
                boyut = int(y.headers.get("content-length") or 0)
                if boyut and boyut > self.max_bayt:
                    log(f"  - çok büyük, atlandı ({boyut/1e6:.1f} MB): {url}")
                    return

                ozet = hashlib.sha1()
                parcalar, toplam = [], 0
                for p in y.iter_content(64 * 1024):
                    toplam += len(p)
                    if toplam > self.max_bayt:
                        log(f"  - sınır aşıldı, atlandı: {url}")
                        return
                    ozet.update(p)
                    parcalar.append(p)
                h = ozet.hexdigest()
                if h in self.ozetler:
                    return  # aynı dosya başka adresten zaten indi
                self.ozetler.add(h)

                uz = uzanti_bul(url)
                if not uz or len(uz) > 6:
                    uz = mimetypes.guess_extension((y.headers.get("content-type") or "").split(";")[0]) or ""
                ad = temiz_ad(os.path.splitext(os.path.basename(urlparse(url).path))[0] or "dosya", 60)
                yol = os.path.join(self.kok, kat, f"{ad}_{h[:8]}{uz}")
                with open(yol, "wb") as f:
                    for p in parcalar:
                        f.write(p)

            self.indirilen[url] = yol
            self.rapor["dosyalar"].append({"tur": kat, "dosya": os.path.relpath(yol, self.kok),
                                           "adres": url, "sayfa": sayfa, "bayt": toplam})
            log(f"  + {kat}: {os.path.basename(yol)}")
        except Exception as e:
            self.rapor["hatalar"].append({"url": url, "hata": str(e)})

    # ------------------------------------------------ rapor
    def _raporu_yaz(self):
        sayac = {}
        for d in self.rapor["dosyalar"]:
            sayac[d["tur"]] = sayac.get(d["tur"], 0) + 1
        self.rapor["ozet"] = {"sayfa": len(self.rapor["sayfalar"]), **sayac,
                              "hata": len(self.rapor["hatalar"])}
        with open(os.path.join(self.kok, "rapor.json"), "w", encoding="utf-8") as f:
            json.dump(self.rapor, f, ensure_ascii=False, indent=2)
        log("\nBitti → " + self.kok)
        for k, v in self.rapor["ozet"].items():
            log(f"  {k:12} {v}")


# ---------------------------------------------------------------- komut satırı

def main():
    p = argparse.ArgumentParser(description="Siteden yazı, fotoğraf, video, ses ve belgeleri ayrı ayrı indirir.")
    p.add_argument("url", help="Başlangıç adresi (https://...)")
    p.add_argument("--cikti", default="cikti", help="Çıktı klasörü (varsayılan: cikti)")
    p.add_argument("--derinlik", type=int, default=0,
                   help="Bağlantıları kaç seviye takip etsin (0 = sadece bu sayfa)")
    p.add_argument("--max-sayfa", type=int, default=30, help="En fazla kaç sayfa gezilsin")
    p.add_argument("--bekleme", type=float, default=1.0, help="Sayfalar arası bekleme, saniye")
    p.add_argument("--max-mb", type=float, default=200, help="Tek dosya için üst sınır (MB)")
    p.add_argument("--disari-cik", action="store_true", help="Başka sitelere giden bağlantıları da gez")
    p.add_argument("--robots-yoksay", action="store_true", help="robots.txt kurallarını yok say")
    p.add_argument("--tarayici", action="store_true",
                   help="Gerçek tarayıcıyla aç (JavaScript ile yüklenen siteler için, Playwright gerekir)")
    p.add_argument("--sadece", default=None,
                   help="Sadece şunları indir, virgülle: yazilar,fotograflar,videolar,sesler,belgeler")
    a = p.parse_args()

    if not a.url.startswith(("http://", "https://")):
        a.url = "https://" + a.url
    sadece = set(s.strip() for s in a.sadece.split(",")) if a.sadece else None

    k = Kaziyici(a.url, a.cikti, a.derinlik, a.max_sayfa, a.bekleme, a.disari_cik,
                 not a.robots_yoksay, a.tarayici, a.max_mb, sadece)
    try:
        k.calistir()
    except KeyboardInterrupt:
        log("\nDurduruldu, o ana kadar inenler kaydediliyor...")
        k._raporu_yaz()
    finally:
        k.kapat()


if __name__ == "__main__":
    main()
