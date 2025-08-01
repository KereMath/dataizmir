import os
import logging
import ckan.lib.helpers as h
import ckan.model as model
from ckan.common import config
from ckan.lib.uploader import get_storage_path
import ckan.lib.munge as munge
from werkzeug.datastructures import FileStorage
import mimetypes

log = logging.getLogger(__name__)

def upload_pdfs(context, data_dict):
    """
    PDF dosyalarını public dizinine yükler.
    
    :param context: CKAN context
    :param data_dict: Upload edilecek dosya bilgileri
    :returns: Upload edilen dosyaların bilgileri
    """
    
    # Yetkili olup olmadığını kontrol et
    model.check_access('sysadmin', context)
    
    # Upload dizinini ayarla
    storage_path = get_storage_path()
    if not storage_path:
        storage_path = config.get('ckan.storage_path', '/tmp')
    
    public_path = os.path.join(storage_path, 'storage', 'uploads', 'public')
    
    # Dizin yoksa oluştur
    if not os.path.exists(public_path):
        os.makedirs(public_path, exist_ok=True)
    
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
                    log.info(f'PDF silindi: {filename}')
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
                'url': h.url_for_static(f'/storage/uploads/public/{filename}', qualified=True)
            }
            
            log.info(f'PDF başarıyla yüklendi: {filename} ({file_size} bytes)')
            
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


def get_uploaded_pdfs(context, data_dict):
    """
    Yüklenmiş PDF dosyalarının listesini getirir.
    
    :param context: CKAN context
    :param data_dict: Parametre dict'i (kullanılmıyor)
    :returns: PDF dosyalarının bilgileri
    """
    
    storage_path = get_storage_path()
    if not storage_path:
        storage_path = config.get('ckan.storage_path', '/tmp')
    
    public_path = os.path.join(storage_path, 'storage', 'uploads', 'public')
    
    result = {}
    
    pdf_types = {
        'data_policy': 'veri-politikasi.pdf',
        'kvkk': 'kvkk.pdf',
        'terms_of_use': 'kullanim-kosullari.pdf'
    }
    
    for pdf_type, filename in pdf_types.items():
        file_path = os.path.join(public_path, filename)
        
        if os.path.exists(file_path):
            try:
                file_size = os.path.getsize(file_path)
                file_mtime = os.path.getmtime(file_path)
                
                result[pdf_type] = {
                    'exists': True,
                    'filename': filename,
                    'size': file_size,
                    'modified_time': file_mtime,
                    'url': h.url_for_static(f'/storage/uploads/public/{filename}', qualified=True)
                }
            except Exception as e:
                log.error(f'PDF bilgisi alınırken hata: {filename} - {str(e)}')
                result[pdf_type] = {'exists': False, 'error': str(e)}
        else:
            result[pdf_type] = {'exists': False}
    
    return result


def delete_pdf(context, data_dict):
    """
    Belirtilen PDF dosyasını siler.
    
    :param context: CKAN context
    :param data_dict: {'pdf_type': 'data_policy|kvkk|terms_of_use'}
    :returns: Silme işleminin sonucu
    """
    
    # Yetkili olup olmadığını kontrol et
    model.check_access('sysadmin', context)
    
    pdf_type = data_dict.get('pdf_type')
    if not pdf_type:
        return {'error': 'PDF tipi belirtilmedi'}
    
    pdf_types = {
        'data_policy': 'veri-politikasi.pdf',
        'kvkk': 'kvkk.pdf',
        'terms_of_use': 'kullanim-kosullari.pdf'
    }
    
    if pdf_type not in pdf_types:
        return {'error': 'Geçersiz PDF tipi'}
    
    filename = pdf_types[pdf_type]
    
    storage_path = get_storage_path()
    if not storage_path:
        storage_path = config.get('ckan.storage_path', '/tmp')
    
    public_path = os.path.join(storage_path, 'storage', 'uploads', 'public')
    file_path = os.path.join(public_path, filename)
    
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            log.info(f'PDF silindi: {filename}')
            return {'deleted': True, 'filename': filename}
        except Exception as e:
            log.error(f'PDF silinirken hata: {filename} - {str(e)}')
            return {'error': f'Dosya silinirken hata: {str(e)}'}
    else:
        return {'error': 'Dosya bulunamadı'}