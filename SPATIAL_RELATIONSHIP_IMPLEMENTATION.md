# Mekansal Veri İlişkilendirme Sistemi - Uygulama Dokümantasyonu

## 🎯 Genel Bakış

Bu doküman, CKAN tabanlı mekansal veri yönetim sistemine eklenen "veri ilişkilendirme" özelliğinin detaylarını içerir. Bu özellik, mekansal veriler ile diğer veri setleri arasında ilişki kurulmasını ve bu ilişkili verilerin harita üzerinde görüntülenmesini sağlar.

## 📋 Özellikler

### 1. **Mekansal Veri İlişkilendirme**
- Mekansal kaynaklara birden fazla veri seti ilişkilendirme
- İlişkilendirme yönetimi için özel popup arayüzü
- Gelişmiş filtreleme ve arama özellikleri
- Mekansal olmayan veri setlerinin kolayca seçilmesi

### 2. **İlişkili Veri Görüntüleme**
- Harita üzerinde aktif mekansal katman için ilişkili verileri görüntüleme
- Her ilişkili veri için:
  - Dosya formatı bilgisi
  - Veri setini görüntüleme linki
  - İndirme linki
  - Paket bilgisi

### 3. **Kullanıcı Arayüzü**
- Modern, responsive tasarım
- Kolay kullanılabilir popup'lar
- Gerçek zamanlı filtreleme
- Görsel geri bildirimler

## 🗄️ Veritabanı Yapısı

### Tablo: `spatial_resource_relationships`

```sql
CREATE TABLE spatial_resource_relationships (
    id SERIAL PRIMARY KEY,
    spatial_resource_id TEXT NOT NULL,
    related_resource_id TEXT NOT NULL,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    FOREIGN KEY (spatial_resource_id) REFERENCES resource(id) ON DELETE CASCADE,
    FOREIGN KEY (related_resource_id) REFERENCES resource(id) ON DELETE CASCADE,
    UNIQUE (spatial_resource_id, related_resource_id)
);
```

**İndeksler:**
- `idx_spatial_resource_relationships_spatial_id` - Mekansal kaynak bazlı sorgular için
- `idx_spatial_resource_relationships_related_id` - İlişkili kaynak bazlı sorgular için

**Önemli Özellikler:**
- `CASCADE DELETE`: Kaynak silindiğinde ilişkiler otomatik temizlenir
- `UNIQUE constraint`: Aynı ilişki birden fazla kez oluşturulamaz
- Foreign key ile referans bütünlüğü sağlanır

## 🔌 API Endpoints

### 1. GET `/api/spatial-resources/<resource_id>/relationships`

**Amaç:** Bir mekansal kaynağın tüm ilişkilerini getirir

**Yetkilendirme:** Admin

**Response:**
```json
{
  "success": true,
  "spatial_resource_id": "uuid",
  "spatial_resource_name": "Resource Name",
  "count": 2,
  "relationships": [
    {
      "relationship_id": 1,
      "related_resource_id": "uuid",
      "related_resource_name": "Related Resource",
      "related_resource_format": "CSV",
      "related_resource_url": "http://...",
      "package_id": "uuid",
      "package_name": "package-name",
      "package_title": "Package Title",
      "created_date": "2025-10-06T...",
      "created_by": "admin"
    }
  ]
}
```

### 2. POST `/api/spatial-resources/<resource_id>/relationships`

**Amaç:** Yeni ilişki oluşturur

**Yetkilendirme:** Admin

**Request Body:**
```json
{
  "related_resource_id": "uuid"
}
```

**Response:**
```json
{
  "success": true,
  "spatial_resource_id": "uuid",
  "related_resource_id": "uuid",
  "created_by": "admin",
  "message": "Relationship created successfully"
}
```

**Validasyonlar:**
- Mekansal kaynak var mı ve spatial olarak işaretli mi?
- İlişkilendirilecek kaynak var mı?
- Kendisiyle ilişki kurulmaya çalışılıyor mu? (engellenir)
- İlişki zaten var mı? (409 Conflict döner)

### 3. DELETE `/api/spatial-resources/<resource_id>/relationships/<related_id>`

**Amaç:** Var olan ilişkiyi siler

**Yetkilendirme:** Admin

**Response:**
```json
{
  "success": true,
  "spatial_resource_id": "uuid",
  "related_resource_id": "uuid",
  "deleted_by": "admin",
  "message": "Relationship deleted successfully"
}
```

## 🎨 Kullanıcı Arayüzü Bileşenleri

### 1. **İlişkilendirme Yönetimi Popup** (`/mekansal/index.html`)

**Konum:** Mekansal kaynak yönetim sayfası

**Özellikler:**
- Sağ alt köşede 🔗 butonu (Anasayfa harita butonunun üstünde)
- Açıldığında tüm mekansal kaynakları listeler
- Her mekansal kaynak için:
  - Mevcut ilişkileri gösterir
  - Yeni ilişki ekleme seçenekleri sunar
  - İlişkileri kaldırma imkanı verir

**Filtreleme:**
- Mekansal kaynak bazlı filtreleme
- Format bazlı filtreleme
- Kuruluş bazlı filtreleme
- Arama (metin bazlı)

**Akış:**
1. 🔗 butonuna tıkla
2. Popup açılır, tüm mekansal kaynaklar yüklenir
3. Her mekansal kaynak için mevcut ilişkiler gösterilir
4. Alt kısımda ilişkilendirilebilir veri setleri grid şeklinde listelenir
5. "➕ İlişkilendir" butonuna basarak yeni ilişki ekle
6. "🗑️ Kaldır" butonuyla mevcut ilişkiyi sil

### 2. **İlişkili Veri Görüntüleme Popup** (`/home/mekansal.html`)

**Konum:** Harita görünümü sayfası

**Özellikler:**
- Aktif katman chip'lerinde 📊 butonu
- İlişkili veri setlerini listeler
- Her veri için görüntüleme ve indirme linkleri

**Akış:**
1. Haritada bir mekansal katmanı aktive et
2. Sağ üstteki aktif katman chip'inde 📊 butonuna tıkla
3. İlişkili veri setleri popup'ta açılır
4. "👁️ Görüntüle" ile veri setini aç
5. "⬇️ İndir" ile dosyayı indir

## 📁 Dosya Değişiklikleri

### 1. Database Migration
**Dosya:** `/usr/lib/ckan/default/src/ckan/ckan/migration/versions/101_ddce982f5b80_create_spatial_resource_relationships.py`

- Yeni migration dosyası
- `spatial_resource_relationships` tablosunu oluşturur
- İndeksleri ve constraint'leri tanımlar

### 2. Backend API
**Dosya:** `/usr/lib/ckan/default/src/ckan/ckan/views/spatial_api.py`

**Eklenen fonksiyonlar:**
- `get_spatial_resource_relationships()` - İlişkileri getir
- `create_spatial_relationship()` - İlişki oluştur
- `delete_spatial_relationship()` - İlişki sil

### 3. Frontend - Yönetim Sayfası
**Dosya:** `/usr/lib/ckan/default/src/ckan/ckan/templates/mekansal/index.html`

**Eklenenler:**
- İlişkilendirme butonu (🔗)
- İlişkilendirme popup'ı
- CSS stilleri
- JavaScript fonksiyonları:
  - `openRelationshipModal()`
  - `closeRelationshipModal()`
  - `loadRelationships()`
  - `filterRelationships()`
  - `renderRelationships()`
  - `addRelationship()`
  - `removeRelationship()`

### 4. Frontend - Harita Sayfası
**Dosya:** `/usr/lib/ckan/default/src/ckan/ckan/templates/home/mekansal.html`

**Eklenenler:**
- İlişkili veri görüntüleme butonu (📊) chip'lerde
- İlişkili veri popup'ı
- JavaScript fonksiyonları:
  - `showRelatedData()`
  - `closeRelatedDataModal()`
  - `getFormatClass()` (format badge'leri için)

## 🚀 Kurulum ve Kullanım

### 1. Migration'ı Çalıştırma

```bash
cd /usr/lib/ckan/default/src/ckan
ckan -c /etc/ckan/default/ckan.ini db upgrade
```

### 2. CKAN'ı Yeniden Başlatma

```bash
sudo systemctl restart ckan
# veya
sudo supervisorctl restart ckan-uwsgi:*
```

### 3. Kullanım Adımları

#### İlişki Oluşturma:
1. `/mekansal/index` sayfasına git
2. Sağ alttaki 🔗 butonuna tıkla
3. Bir mekansal kaynak seç
4. İlişkilendirmek istediğin veri setini bul (filtreler kullanabilirsin)
5. "➕ İlişkilendir" butonuna tıkla

#### İlişkili Veriyi Görüntüleme:
1. `/home/mekansal` harita sayfasına git
2. Bir mekansal katmanı aktive et (toggle ile)
3. Sağ üstteki aktif katman chip'inde 📊 butonuna tıkla
4. İlişkili veri setlerini gör
5. "Görüntüle" veya "İndir" butonlarını kullan

## 🔒 Güvenlik

### Yetkilendirme
- Tüm API endpoint'leri **sysadmin** yetkisi gerektirir
- Frontend'de butonlar görünür ama backend kontrolü vardır

### SQL Injection Koruması
- Parameterized queries kullanılır
- SQLAlchemy `text()` ile güvenli sorgular

### Veri Bütünlüğü
- Foreign key constraint'ler
- Unique constraint (aynı ilişki 2 kez oluşturulamaz)
- CASCADE delete (kaynak silinince ilişkiler de silinir)

### Validasyonlar
- Kaynak varlığı kontrolü
- Mekansal kaynak kontrolü
- Self-reference engelleme
- Duplicate ilişki engelleme

## 🎨 UI/UX Özellikleri

### Responsive Tasarım
- Mobil uyumlu
- Tablet uyumlu
- Desktop optimize

### Kullanıcı Deneyimi
- Gerçek zamanlı arama
- Filtreleme (300ms debounce ile)
- Görsel geri bildirimler (hover, active states)
- Loading durumları
- Hata mesajları
- Başarı onayları

### Erişilebilirlik
- Buton title attribute'ları
- Emoji kullanımı ile görsel zenginlik
- Renkli badge'ler
- Açık etiketler

## 📊 Veri Akışı

```
1. İlişki Oluşturma Akışı:
   [Kullanıcı] → [🔗 Buton] → [Popup Açılır] → [Filtreler/Ara]
   → [Veri Seç] → [➕ İlişkilendir] → [API POST] → [DB Insert]
   → [Başarı Mesajı] → [Liste Yenilenir]

2. İlişkili Veri Görüntüleme Akışı:
   [Harita] → [Katman Aktive] → [📊 Buton] → [API GET]
   → [İlişkiler Yüklenir] → [Popup'ta Göster]
   → [Görüntüle/İndir Linkleri]

3. İlişki Silme Akışı:
   [Popup] → [🗑️ Kaldır] → [Onay] → [API DELETE]
   → [DB Delete] → [Başarı] → [Liste Yenilenir]
```

## 🐛 Hata Yönetimi

### Frontend Hataları
- API çağrısı başarısız: Kullanıcıya alert ile bildirilir
- Boş sonuç: "Sonuç bulunamadı" mesajı gösterilir
- Yükleme hatası: Hata ikonu ve mesajı

### Backend Hataları
- 400 Bad Request: Eksik veya hatalı parametreler
- 403 Forbidden: Yetkilendirme hatası
- 404 Not Found: Kaynak bulunamadı
- 409 Conflict: İlişki zaten var
- 500 Internal Server Error: Veritabanı veya diğer hatalar

Tüm hatalar detaylı log'lanır ve kullanıcıya anlamlı mesajlar gösterilir.

## 📝 Notlar

1. **Çoklu İlişki Desteği:** Bir mekansal kaynağa sınırsız sayıda veri ilişkilendirilebilir

2. **Mekansal Olmayan Veriler:** Sadece mekansal olmayan kaynaklar ilişkilendirilebilir (mantıksal kısıtlama)

3. **Performans:**
   - İlişki sayısı arttıkça popup yükleme süresi artabilir
   - Filtreleme ile performans korunur
   - Grid'de max 20 kaynak gösterilir

4. **Tema Uyumluluğu:** Mevcut CKAN temasına tam uyumlu tasarım

5. **Genişletilebilirlik:**
   - Yeni veri formatları kolayca eklenebilir
   - Filtreleme kriterleri genişletilebilir
   - API endpoint'leri genişletilebilir

## 🔄 Gelecek Geliştirmeler (Öneriler)

1. **Toplu İşlemler:** Birden fazla ilişkiyi aynı anda ekleme/silme
2. **İlişki Notları:** Her ilişkiye açıklama ekleme
3. **İlişki Kategorileri:** İlişki tiplerini sınıflandırma
4. **Dashboard Widget:** İlişki istatistikleri widget'ı
5. **Export/Import:** İlişkileri dışa/içe aktarma
6. **İlişki Grafiği:** İlişkileri görselleştirme

---

**Geliştirici:** AI Assistant
**Tarih:** 2025-10-06
**Versiyon:** 1.0
**Durum:** ✅ Production Ready
