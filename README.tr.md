# tuik-data

[![CI](https://github.com/YusufBeratZengin/tuik-data/actions/workflows/ci.yml/badge.svg)](https://github.com/YusufBeratZengin/tuik-data/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-green.svg)](LICENSE)

**TÜİK Açık Veri ve SDMX servisleri için sıfır bağımlılıklı Python istemcisi ve CLI.**

TÜİK Veri Portalı SDMX 2.1 ve JSON-stat 2.0'a geçtiğinden beri her tablo çok
boyutlu bir küp: boyutlar, kod listeleri, roller, gizlilik bayrakları ve sırasını
tutturmanız gereken düz bir değer dizisi. `tuik-data` bu küpleri düz satırlara
(ve CSV / Polars'a) çeviriyor; API anahtarı → Bearer token işini de sizin yerinize
yapıyor. Üstelik yalnızca standart kütüphaneyle.

```python
from tuik_data import TuikClient

client = TuikClient()                        # TUIK_API_KEY ortam değişkenini okur
tufe = client.get_indicator("tufe_genel", last_n=12)
print(tufe.to_csv(labels="both"))
```

pandas yok, requests yok, lxml yok. Python 3.10+.

## Kurulum

```bash
pip install tuik-data
pip install "tuik-data[polars]"    # isteğe bağlı: Dataset.to_polars()
```

## 30 saniyelik örnek (API anahtarı gerekmez)

Veri Portalı'ndan indirdiğiniz herhangi bir JSON-stat 2.0 ya da SDMX-JSON 2.0
dosyasını çevrimdışı düzleştirebilirsiniz. Depodaki `examples/tufe_example.json`
**uydurma** sayılar içerir (dosyanın kendisi de bunu söylüyor):

```console
$ tuik-data flatten examples/tufe_example.json
TIME_PERIOD  COICOP  value   status
-----------  ------  ------  ------
2024-01      01      1000.0
2024-01      04      1120.5
2024-01      07              p
2024-02      01      1010.2
2024-02      04      1135.7
2024-02      07      980.1
2024-03      01              c
2024-03      04      1150.0
2024-03      07      990.5
```

Boş hücreler `None` olur (`p` geçici, `c` gizli demek) ve komşu hücreleri
kaydırmaz. Kütüphanenin asıl derdi bu.

Türkçe etiketler bozulmadan gelir: `labels="both"` ile
`"01 - Gıda ve Alkolsüz İçecekler"`, `"TR34 - İstanbul"` gibi sütunlar alırsınız;
CSV çıktısı UTF-8'dir.

## Canlı veri (API anahtarı)

TÜİK, `nsiws.tuik.gov.tr` servisini kısa ömürlü Bearer token ile koruyor. Token'ı
bir kez, <https://veriportali.tuik.gov.tr> üzerinden SMS doğrulamalı **API
Anahtarı** alarak elde ediyorsunuz (*Kullanıcı Bilgileri → API Anahtarı*).
Anahtarı ortam değişkenine koyun; kütüphane anahtarı diske yazmaz ve hata
mesajlarında göstermez:

```bash
export TUIK_API_KEY=xxxxxx
```

```python
from tuik_data import TuikClient

with TuikClient() as client:
    tufe = client.get_indicator("tufe_genel", start_year=2024)
    print(tufe.records(labels="both")[:3])

    # Kolaylık fonksiyonu: TÜİK'in o yıl için yayımladığı gözlemleri getirir.
    # Ortalama ya da yıllık değişim oranı HESAPLAMAZ.
    print(client.get_tufe_annual(2025).to_csv("tufe-2025.csv", labels="both"))
```

## Komut satırı

```console
$ tuik-data fetch tufe_genel --son-12-ay --format table
$ tuik-data fetch tufe_genel --start-year 2024 --format csv --out tufe.csv
$ tuik-data fetch nufus_il --key CITIES=TR34 --labels both
$ tuik-data dims nufus_il                 # hangi kırılımla filtreleyebilirim?
$ tuik-data indicators                    # katalog
$ tuik-data search "işgücü"               # çevrimdışı arama, Türkçe veya İngilizce
$ tuik-data flatten ~/Indirilenler/tufe.json --format csv --out tufe.csv
$ tuik-data save tufe_genel               # yerel SQLite deposuna kaydet
$ tuik-data mcp                           # MCP stdio sunucusu
```

`--son-12-ay` son 12 dönemi tutar; genel biçimi `--last-n N`. Çıkış kodları:
`0` başarılı, `2` TÜİK/yapılandırma/ayrıştırma hatası.

## Sık karşılaşılan hata: "boyut uyuşmazlığı"

Yanlış bir kırılım kodu verdiğinizde TÜİK boş küme döner. Kütüphane boş sonucu
sessizce yutmak yerine geçerli seçenekleri yazar:

```text
error: the filter key 'TR99' matched no observations in TR,DF_ADNKS_T30,1.1.
Available categories:
  - CITIES: TR01 (Adana), TR02 (Adıyaman), ... TR34 (İstanbul) ...
```

Filtreyi önceden görmek için `tuik-data dims nufus_il`.

## Neler var?

* **JSON-stat 2.0 ve SDMX-JSON 2.0** ayrıştırıcı: boyut sırası, roller, kod/etiket,
  gizlilik bayrağı, eksik hücre, kısaltılmış değer dizisi — hepsi tek modelde.
* **Token yaşam döngüsü**: anahtarı token'a çevirir, bellekte tutar, süresi
  dolmadan 30 saniye önce yeniler, 401'de bir kez yeniden dener.
* **Yerel depo**: `sqlite3` ile uzun format; ağ yokken de rapor çalışır.
* **Polars**: `Dataset.to_polars()`, pandas gerekmez.
* **MCP sunucusu**: stdio JSON-RPC 2.0; `tuik_get_indicator`,
  `tuik_list_indicators`, `tuik_search`, `tuik_describe_indicator`.

## Katalog

| Kod | Gösterge | Sıklık |
| --- | --- | --- |
| `tufe_genel` | TÜFE, ana harcama grupları | aylık |
| `tufe_katki` | TÜFE, grupların katkısı | aylık |
| `tufe_sepet` | TÜFE sepeti ve ağırlıkları | yıllık |
| `yi_ufe` | Yurt içi üretici fiyat endeksi | aylık |
| `nufus_il` | ADNKS, illere göre nüfus | yıllık |
| `nufus_il_yas_cinsiyet` | ADNKS, il / yaş / cinsiyet | yıllık |
| `issizlik` | Temel işgücü göstergeleri | aylık |
| `ihracat_birim_deger` | İhracat birim değer endeksi | aylık |

Veri akışı kodlarını koda dokunmadan değiştirebilirsiniz (portal tablo adlarını
yenileyebiliyor):

```bash
export TUIK_DATASET_TUFE_GENEL="TR,DF_TUFE_SDMX_TT01,1.1"
```

## Bağımlılıklar

Çalışma zamanında **hiç yok**: `urllib`, `json`, `sqlite3`, `csv`, `gzip`.
Gerekçeleri README'nin İngilizcesinde ayrıntılı; özetle `requests` ve DuckDB bu
işin boyutuna göre ağır kalıyordu. `polars` isteğe bağlı, MCP sunucusu da
standart kütüphaneyle yazıldı.

## Dürüst sınırlar

* **Depoda hiçbir resmî değer yok.** Sayılar istendiği anda TÜİK'ten gelir;
  uyuşmazlıkta Resmî Gazete ve TÜİK bültenleri esastır. `get_tufe_annual()`
  yayımlanmış gözlemleri seçer, oran hesaplamaz.
* **Veri akışı kodları** TÜİK Veri Portalı SDMX kataloğundan alınmıştır ve haber
  verilmeden değişebilir; belirli bir değer iddiası için `NEEDS_SOURCE`.
* **Canlı uç noktalar test edilmiyor** (kişisel, SMS onaylı anahtar gerekiyor).
  Ağ kodunun tamamı sahte taşıyıcı ve yerel HTTP sunucusuyla test edilir;
  `tests/test_network.py` yalnızca `TUIK_LIVE=1 TUIK_API_KEY=...` ile çalışır.
* **Tüm küpü çekmek büyük olabilir.** Dönem ve boyut filtreleyin ya da `last_n`
  kullanın; MCP aracı 500 satırda keser ve bunu söyler.
* Yalnızca `dataset` sınıfı JSON-stat kabul edilir; koleksiyonlar açık bir
  `TuikParseError` ile reddedilir.

## Geliştirme

```bash
python -m pip install -r requirements-dev.txt
bash scripts/check.sh    # lint + biçim + mypy --strict + testler + kapsam >= %85
```

Kapsam %93'ün üzerinde (dal kapsamı dâhil); testler hata yollarını, UTF-8'i,
gizli hücreleri, yeniden denemeyi, token yenilemeyi, önbelleği ve MCP çerçevesini
kapsıyor. Ağ testleri varsayılan olarak atlanır.

## Lisans

MIT © 2026 YusufBeratZengin. Bu proje TÜİK'e bağlı değildir.
