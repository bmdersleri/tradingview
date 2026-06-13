# tvcli — Aktif Görevler & Durum Takip Dosyası

## Tamamlanan Altyapı İyileştirmeleri
- [x] SQLite WAL (Write-Ahead Logging) Moduna Geçiş
- [x] SQLite Bağlantı Zaman Aşımları (`timeout=10.0`)
- [x] Süreç Genelinde Veritabanı Başına Tek Seferlik Şema Doğrulama
- [x] Sunucu Kapanışlarında/Çökmelerinde Takılı Kalan (`running`) Görevlerin Sıfırlanması
- [x] Dış İsteklerde (VAP Rapor İndirmeleri) Üstel Geri Çekilme (Exponential Backoff) & 3 Kez Yeniden Deneme (Retry)
- [x] Dashboard Görselleri için HTTP ETag / Cache-Control Desteği
- [x] FastAPI Arayüz Yanıtlarında GZip Sıkıştırması (`GZipMiddleware`)
- [x] Yapılandırılmış JSON Formatında stderr Günlükleme (Structured JSON Logging)
- [x] `justfile` Geliştirici Kısayolları (`reset-db`, `serve-dev`, `test-fast`)

## Yapılacak Görevler
- [x] Geliştirici Test Verisi Seeding Komutu (`tvcli dev seed-db`) eklenmesi
- [ ] Arayüz üzerinden İnteraktif İndikatör Kontrolleri (SMA, EMA, WMA, RSI, MACD)
- [ ] Arayüz üzerinden Alarm Eşikleri ve Telegram Ayarları Paneli (Settings UI)
- [ ] Kronolojik Alarm Geçmişi ve Günlüğü Tablosu (Alert History)
- [ ] KAP Duyuruları Entegrasyonu ve Grafik Üzerinde Duyuru İkonları (KAP News)
- [ ] Sektör Detay ve Karşılaştırma Sayfası (Sector Analysis)

## Aktif Geliştirme Detayları
- **FastAPI Sunucusu Portu**: 8789
- **Aktif Veritabanı Yolu**: `~/.local/share/tvcli/archive.sqlite3`
