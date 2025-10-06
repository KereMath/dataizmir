# 🗺️ Mekansal Veri İlişkilendirme - Kullanım Kılavuzu

## 📌 Nedir?

Mekansal veri ilişkilendirme özelliği, **mekansal olarak işaretlenmiş** harita verileri (GeoJSON, SHP, KML vb.) ile **mekansal olmayan** diğer veri setlerini (CSV, Excel, PDF vb.) bağlamanızı sağlar.

> **ÖNEMLİ:**
> - İlişkilendirme popup'ında **sadece mekansal olarak işaretlenmiş veriler** görünür
> - Bu mekansal verilere **sadece mekansal olmayan veriler** (CSV, Excel, PDF vb.) bağlanabilir
> - Mekansal veri + Mekansal olmayan veri = İlişki ✅

**Örnek Kullanım Senaryoları:**
- 🏭 Hava kalitesi istasyon konumları → İstasyonların ölçüm değerleri (CSV)
- 🏫 Okul konumları → Öğrenci sayıları ve eğitim istatistikleri (Excel)
- 🚌 Otobüs durakları → Otobüs hat bilgileri ve zaman çizelgeleri (PDF)
- 🏥 Hastane konumları → Yatak kapasiteleri ve hizmet bilgileri (CSV)

## 🎯 Özellikler

✅ **Bir mekansal veriye birden fazla veri seti bağlama**
✅ **Gelişmiş filtreleme ve arama**
✅ **Harita üzerinde ilişkili verileri görüntüleme**
✅ **Kolay ekleme/silme işlemleri**

---

## 📋 Kullanım Adımları

### 1️⃣ Mekansal Veri İlişkilendirme

#### Adım 1: İlişkilendirme Sayfasına Git
- Tarayıcıda `/mekansal/index` adresine git
- Veya ana menüden "Mekansal Gösterim Yönetimi" linkine tıkla

#### Adım 2: İlişkilendirme Popup'ını Aç
- Sağ alt köşede **🔗 (Link) butonu**nu gör
  - Bu buton, 🗺️ (Harita) butonunun hemen üstündedir
- Butona tıkla

#### Adım 3: Popup'ta Gezin
Popup açıldığında göreceksin:

**Üst Kısım - Filtreler:**
```
┌─────────────────────────────────────┐
│ 🔍 Mekansal kaynak veya veri ara... │
├─────────────────────────────────────┤
│ [Mekansal Veri] [Format] [Kuruluş] │
└─────────────────────────────────────┘
```

**Alt Kısım - Mekansal Veriler:**
Her mekansal veri için bir kart:
```
┌────────────────────────────────────────┐
│ 🗺️ Hava Kalitesi İstasyonları       │
│                            2 ilişki   │
├────────────────────────────────────────┤
│ Mevcut İlişkiler:                      │
│ 📄 İstasyon Ölçümleri (CSV) [🗑️ Kaldır]│
│ 📄 İstasyon Bilgileri (XLSX) [🗑️ Kaldır]│
├────────────────────────────────────────┤
│ [Veri Seti 1]  [Veri Seti 2]  ...     │
│ [➕ İlişkilendir] [➕ İlişkilendir]    │
└────────────────────────────────────────┘
```

#### Adım 4: İlişki Ekle
1. **Filtreleri kullan** (opsiyonel):
   - Mekansal veri seç
   - Format seç (CSV, Excel, PDF, vb.)
   - Kuruluş seç

2. **Arama yap** (opsiyonel):
   - Üstteki arama kutusuna kelime yaz
   - Sonuçlar anında filtrelenir

3. **İlişkilendir:**
   - İlişkilendirmek istediğin veri setini bul
   - **"➕ İlişkilendir"** butonuna tıkla
   - ✅ İlişki başarıyla oluşturulur
   - Veri seti "Mevcut İlişkiler" kısmına eklenir

#### Adım 5: İlişki Kaldır
- Mevcut ilişkiler kısmında **"🗑️ Kaldır"** butonuna tıkla
- Onay popup'ı çıkar, "Evet" seç
- ✅ İlişki silinir

---

### 2️⃣ İlişkili Verileri Haritada Görüntüleme

#### Adım 1: Harita Sayfasına Git
- Tarayıcıda `/home/mekansal` adresine git
- Veya ana menüden "Mekansal Gösterim" linkine tıkla

#### Adım 2: Mekansal Katmanı Aktive Et
- Sol taraftaki panelden bir mekansal veriyi seç
- Toggle (açma/kapama) butonuna tıkla
- Veri haritada görünür

#### Adım 3: Aktif Katmanlar Paneline Bak
- Sağ üstte aktif katmanların listesi görünür:
```
┌────────────────────────────────────┐
│ 🟢 Hava Kalitesi İstasyonları     │
│      [🎯] [📊] [✕]                │
└────────────────────────────────────┘
```

**Butonlar:**
- 🎯 = Odaklan (haritayı bu katmana odakla)
- 📊 = **İlişkili verileri göster**
- ✕ = Kapat

#### Adım 4: İlişkili Verileri Gör
- **📊 İlişkili verileri göster** butonuna tıkla
- Popup açılır ve ilişkili veri setleri listelenir:

```
┌──────────────────────────────────────────┐
│ 📊 İlişkili Veri Setleri            [✕] │
├──────────────────────────────────────────┤
│ 🗺️ Hava Kalitesi İstasyonları          │
│    2 ilişkili veri seti                  │
├──────────────────────────────────────────┤
│ 📄 İstasyon Ölçümleri                    │
│ 📁 Hava Kalitesi Veri Seti          CSV │
│ [👁️ Görüntüle] [⬇️ İndir]              │
├──────────────────────────────────────────┤
│ 📄 İstasyon Bilgileri                    │
│ 📁 Hava Kalitesi Veri Seti         XLSX │
│ [👁️ Görüntüle] [⬇️ İndir]              │
└──────────────────────────────────────────┘
```

#### Adım 5: Veri Setine Eriş
- **"👁️ Görüntüle"** → Veri seti sayfası açılır (yeni sekmede)
- **"⬇️ İndir"** → Dosya indirilir

---

## 💡 İpuçları ve Püf Noktaları

### 🔍 Filtreleme
- **Çoklu filtre:** Filtreler birlikte çalışır (AND mantığı)
- **Arama:** Hem mekansal veri adında hem ilişkili veri adında arar
- **Temizleme:** Filtreyi kaldırmak için "Tümü" seçeneğini seç

### 🎨 Görsel İpuçları
- **Yeşil arkaplan** = İlişki var
- **Hover efekti** = Kartın üzerine gelince renk değişir
- **Badge renkleri:**
  - 🟢 CSV = Yeşil
  - 🔵 JSON = Sarı
  - 🟣 Excel = Gri
  - 🔴 PDF = Mavi

### ⚡ Performans
- Popup ilk açıldığında tüm veriler yüklenir (1-2 saniye)
- Filtreler anında çalışır (300ms gecikme ile)
- Her mekansal veri için max 20 ilişki gösterilir

### ⚠️ Kısıtlamalar
- **Sadece admin kullanıcılar** ilişki ekleyebilir/silebilir
- **Mekansal kaynak kendine bağlanamaz**
- **Aynı ilişki 2 kez oluşturulamaz**
- **Sadece mekansal olmayan veriler** ilişkilendirilebilir

---

## 🔧 Sık Sorulan Sorular

### ❓ İlişki oluşturamıyorum, neden?
**Cevap:** Birkaç neden olabilir:
1. Admin yetkisi yok → Sistem yöneticinize başvurun
2. Kaynak zaten ilişkili → Mevcut ilişkiler kısmına bakın
3. Mekansal kaynak kendine bağlanamaz → Farklı bir kaynak seçin

### ❓ İlişkili veri görünmüyor, neden?
**Cevap:**
1. Katman aktif mi? → Toggle butonuna basın
2. İlişki var mı? → İlişkilendirme sayfasından kontrol edin
3. Sayfa yenilenmiş mi? → Sayfayı yenileyin (F5)

### ❓ Birden fazla veri ekleyebilir miyim?
**Cevap:** Evet! Bir mekansal kaynağa **sınırsız sayıda** veri ekleyebilirsiniz.

### ❓ İlişki silince veri de silinir mi?
**Cevap:** Hayır! Sadece ilişki silinir, veri setleri ve mekansal kaynak silinmez.

### ❓ Hangi formatlar destekleniyor?
**Cevap:**
- **Mekansal:** GeoJSON, SHP, KML, GPX, WMS, WFS, GeoTIFF
- **İlişkilendirilebilir:** CSV, Excel, PDF, JSON, XML, vb. (mekansal olmayan tüm formatlar)

---

## 📞 Destek

Sorun yaşıyorsanız:
1. **Konsolu kontrol edin:** Tarayıcıda F12 → Console
2. **Hata loglarını paylaşın:** Sistem yöneticinizle iletişime geçin
3. **Dokümantasyonu okuyun:** `SPATIAL_RELATIONSHIP_IMPLEMENTATION.md`

---

## ✅ Kontrol Listesi

İlişkilendirme yapmadan önce:
- [ ] Admin yetkisi var mı?
- [ ] Mekansal kaynak "spatial" olarak işaretli mi?
- [ ] İlişkilendirilecek veri mekansal değil mi?
- [ ] İlişki zaten yok mu?

İlişkili veriyi görmeden önce:
- [ ] Mekansal katman aktif mi?
- [ ] İlişki oluşturulmuş mu?
- [ ] Popup açıldı mı? (📊 buton)

---

**🎉 Başarılar! Artık mekansal verilerinizi diğer veri setleriyle ilişkilendirebilirsiniz.**
