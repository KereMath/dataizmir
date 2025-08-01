import os
import logging
import ckan.lib.helpers as h
import ckan.model as model
from ckan.common import config
import ckan.lib.munge as munge
from werkzeug.datastructures import FileStorage
import mimetypes

log = logging.getLogger(__name__)

def upload_pdfs(context, data_dict):
    """
    PDF dosyalarını CKAN public dizinine yükler.
    """
    log.info(f"upload_pdfs çağrıldı! data_dict keys: {list(data_dict.keys())}")

    # Yetkili olup olmadığını kontrol et
    model.check_access('sysadmin', context)
    
    # CKAN public dizinini kullan
    ckan_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    public_path = os.path.join(ckan_root, 'ckan', 'public')
    
    # Alternatif: Direkt path belirt
    # public_path = '/usr/lib/ckan/default/src/ckan/ckan/public'
    
    # Dizin yoksa oluştur
    if not os.path.exists(public_path):
        os.makedirs(public_path, exist_ok=True)
    
    log.info(f'PDF upload path: {public_path}')
    
    result = {}
    
    # Upload edilecek PDF türleri
    pdf_types = {
        'data_policy': 'veri-politikasi.pdf',
        'kvkk': 'kvkk.pdf', 
        'terms_of_use': 'kullanim-kosullari.pdf'
    }
    
    for pdf_type, filename in pdf_types.items():
        file_key = f'{pdf_type}_upload'
        clear_key = f'clear_{pdf_type}_upload'
        
        # Dosyayı sil komutu kontrol et
        if data_dict.get(clear_key):
            file_path = os.path.join(public_path, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    log.info(f'PDF silindi: {file_path}')
                    result[pdf_type] = {'deleted': True, 'filename': filename}
                except Exception as e:
                    log.error(f'PDF silinirken hata: {filename} - {str(e)}')
                    result[pdf_type] = {'error': f'Dosya silinirken hata: {str(e)}'}
            continue
        
        # Upload edilen dosyayı kontrol et
        uploaded_file = data_dict.get(file_key)
        if not uploaded_file or not hasattr(uploaded_file, 'filename'):
            continue
        
        # Dosya tipini kontrol et
        if not uploaded_file.filename.lower().endswith('.pdf'):
            result[pdf_type] = {'error': 'Sadece PDF dosyaları yüklenebilir'}
            continue
        
        # MIME type kontrol et
        mime_type, _ = mimetypes.guess_type(uploaded_file.filename)
        if mime_type != 'application/pdf':
            result[pdf_type] = {'error': 'Geçersiz dosya tipi. Sadece PDF dosyaları desteklenir.'}
            continue
        
        try:
            # Dosyayı kaydet
            file_path = os.path.join(public_path, filename)
            
            log.info(f'Dosya kaydediliyor: {file_path}')
            
            # Mevcut dosya varsa yedekle
            if os.path.exists(file_path):
                backup_path = file_path + '.backup'
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.rename(file_path, backup_path)
            
            # Yeni dosyayı kaydet
            uploaded_file.save(file_path)
            
            # Dosya izinlerini ayarla
            os.chmod(file_path, 0o644)
            
            # Dosya boyutunu al
            file_size = os.path.getsize(file_path)
            
            # Başarılı upload bilgilerini kaydet
            result[pdf_type] = {
                'uploaded': True,
                'filename': filename,
                'original_filename': uploaded_file.filename,
                'size': file_size,
                'path': file_path,  # Debug için path'i de ekle
                'url': h.url_for_static(f'/{filename}', qualified=True)
            }
            
            log.info(f'PDF başarıyla yüklendi: {file_path} ({file_size} bytes)')
            
        except Exception as e:
            log.error(f'PDF yüklenirken hata: {filename} - {str(e)}')
            result[pdf_type] = {'error': f'Dosya yüklenirken hata: {str(e)}'}
            
            # Hata durumunda yedek dosyayı geri yükle
            backup_path = os.path.join(public_path, filename + '.backup')
            if os.path.exists(backup_path):
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    os.rename(backup_path, file_path)
                except:
                    pass
    
    return result