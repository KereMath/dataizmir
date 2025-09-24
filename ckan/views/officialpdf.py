# encoding: utf-8
import os
import logging
from flask import Blueprint, request, Response, redirect, send_from_directory # <-- DEĞİŞİKLİK: send_from_directory eklendi
from werkzeug.utils import secure_filename
import ckan.lib.base as base
import ckan.lib.helpers as h
import ckan.logic as logic
import ckan.model as model
from ckan.common import _, config, g
from ckan import authz
from flask.views import MethodView

log = logging.getLogger(__name__)

officialpdf = Blueprint(u'officialpdf', __name__, url_prefix=u'/dashboard/legal')

# Desteklenen PDF dosyaları ve açıklamaları (Değişiklik yok)
LEGAL_PDFS = {
    'veripnihai.pdf': {
        'title': 'Veri Politikası',
        'description': 'Platformun veri kullanım politikası',
        'footer_text': 'Veri&nbsp;Politikası'
    },
    'kvkknihai.pdf': {
        'title': 'KVKK',
        'description': 'Kişisel Verilerin Korunması Kanunu',
        'footer_text': 'KVKK'
    },
    'kosulnihai.pdf': {
        'title': 'Kullanım Koşulları',
        'description': 'Platform kullanım koşulları ve şartları',
        'footer_text': 'Kullanım&nbsp;Koşulları'
    }
}

def check_admin_access():
    """Admin yetkisi kontrolü"""
    if not g.user:
        base.abort(403, _('Unauthorized to access this page'))
    
    try:
        context = {'user': g.user, 'auth_user_obj': g.userobj}
        logic.check_access('sysadmin', context)
    except logic.NotAuthorized:
        base.abort(403, _('You must be a system administrator to access this page'))

def get_pdf_storage_path():
    """PDF dosyalarının saklanacağı dizin"""
    # <-- DEĞİŞİKLİK: Dizin yolu yeni güvenli klasör olarak güncellendi
    return config.get('ckan.legal_documents_path', '/var/lib/ckan/legal_documents')

def get_pdf_file_path(filename):
    """Belirli bir PDF dosyasının tam yolu"""
    return os.path.join(get_pdf_storage_path(), filename)

def is_allowed_file(filename):
    """Dosya uzantısı kontrolü"""
    return filename and filename.lower().endswith('.pdf')

class LegalDocsView(MethodView):
    """Legal dokümanlar ana sayfa"""
    
    def get(self):
        check_admin_access()
        
        pdf_status = {}
        for filename, info in LEGAL_PDFS.items():
            file_path = get_pdf_file_path(filename)
            file_exists = os.path.exists(file_path)
            pdf_status[filename] = {
                'title': info['title'],
                'description': info['description'],
                'exists': file_exists,
                'size': os.path.getsize(file_path) if file_exists else 0,
                # <-- DEĞİŞİKLİK: URL artık dosyayı sunan yeni 'serve' view'ını işaret ediyor
                'url': h.url_for('officialpdf.serve', filename=filename) if file_exists else None
            }
        
        extra_vars = {
            'pdf_status': pdf_status,
            'page_title': 'Legal Dokümanlar Yönetimi'
        }
        
        return base.render('legal/index.html', extra_vars)

# <-- YENİ: PDF dosyalarını güvenli bir şekilde sunmak için yeni bir View sınıfı
class ServePDFView(MethodView):
    """Güvenli dizinden PDF dosyası sunar"""
    
    def get(self, filename):
        # Güvenlik: Sadece tanımlı PDF'lerin sunulmasını sağla
        if filename not in LEGAL_PDFS:
            base.abort(404, _('File not found'))
            
        # Güvenlik: Dosya adını temizle
        safe_filename = secure_filename(filename)
        if safe_filename != filename:
            base.abort(400, _('Invalid filename'))
            
        storage_path = get_pdf_storage_path()
        
        # Dosyanın varlığını kontrol et
        if not os.path.exists(os.path.join(storage_path, safe_filename)):
            base.abort(404, _('File not found'))
            
        # Flask'in güvenli send_from_directory fonksiyonunu kullan
        return send_from_directory(storage_path, safe_filename, as_attachment=False)

# Diğer sınıflar (PDFUploadView, PDFDeleteView, PDFRestoreView) aynı kalabilir,
# çünkü onlar zaten get_pdf_storage_path() fonksiyonunu kullanıyorlar.
# Aşağıya bu sınıfları tekrar ekliyorum, böylece tek bir blokta tam kod olur.

class PDFUploadView(MethodView):
    """PDF yükleme/güncelleme"""
    
    def post(self, pdf_type):
        check_admin_access()
        
        if pdf_type not in LEGAL_PDFS:
            h.flash_error('Geçersiz PDF tipi')
            return redirect('/dashboard/legal/')
        
        if 'pdf_file' not in request.files:
            h.flash_error('Dosya seçilmedi')
            return redirect('/dashboard/legal/')
        
        file = request.files['pdf_file']
        
        if file.filename == '':
            h.flash_error('Dosya seçilmedi')
            return redirect('/dashboard/legal/')
        
        if not is_allowed_file(file.filename):
            h.flash_error('Sadece PDF dosyaları yüklenebilir')
            return redirect('/dashboard/legal/')
        
        try:
            target_filename = pdf_type
            target_path = get_pdf_file_path(target_filename)
            
            storage_dir = get_pdf_storage_path()
            if not os.path.exists(storage_dir):
                # Bu artık sunucuda manuel olarak yapıldığı için bir hata durumudur.
                log.error(f'Storage directory {storage_dir} does not exist!')
                h.flash_error('Dosya yükleme dizini sunucuda bulunamadı. Lütfen yöneticiyle iletişime geçin.')
                return redirect('/dashboard/legal/')

            if not os.access(storage_dir, os.W_OK):
                log.error(f'No write permission to storage directory {storage_dir}')
                h.flash_error('Dosya yükleme dizinine yazma izni yok')
                return redirect('/dashboard/legal/')

            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            max_size = 10 * 1024 * 1024
            if file_size > max_size:
                h.flash_error(f'{LEGAL_PDFS[pdf_type]["title"]} dosyası çok büyük. Maksimum 10MB olmalı.')
                return redirect('/dashboard/legal/')
            
            if os.path.exists(target_path):
                backup_path = target_path + '.backup'
                try:
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(target_path, backup_path)
                    log.info(f'Backup created for {target_path}')
                except Exception as e:
                    log.warning(f'Could not create backup: {str(e)}')
            
            file.save(target_path)
            log.info(f'File saved to {target_path}')
            h.flash_success(f'{LEGAL_PDFS[pdf_type]["title"]} başarıyla güncellendi!')
            log.info(f'Legal PDF updated successfully: {pdf_type} by user {g.user}')
            
        except Exception as e:
            log.error(f'Unexpected error uploading PDF {pdf_type}: {str(e)}')
            h.flash_error(f'Dosya yüklenirken hata oluştu: {str(e)}')
        
        return redirect('/dashboard/legal/')

class PDFDeleteView(MethodView):
    """PDF silme"""
    
    def post(self, pdf_type):
        check_admin_access()
        if pdf_type not in LEGAL_PDFS:
            h.flash_error('Geçersiz PDF tipi')
            return redirect('/dashboard/legal/')
        
        file_path = get_pdf_file_path(pdf_type)
        if not os.path.exists(file_path):
            h.flash_error('Dosya bulunamadı')
            return redirect('/dashboard/legal/')
        
        try:
            backup_path = file_path + '.deleted'
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(file_path, backup_path)
            h.flash_success(f'{LEGAL_PDFS[pdf_type]["title"]} başarıyla silindi!')
            log.info(f'Legal PDF deleted: {pdf_type} by user {g.user}')
        except Exception as e:
            log.error(f'Error deleting PDF {pdf_type}: {str(e)}')
            h.flash_error(f'Dosya silinirken hata oluştu: {str(e)}')
        
        return redirect('/dashboard/legal/')

class PDFRestoreView(MethodView):
    """PDF geri yükleme"""
    
    def post(self, pdf_type):
        check_admin_access()
        if pdf_type not in LEGAL_PDFS:
            h.flash_error('Geçersiz PDF tipi')
            return redirect('/dashboard/legal/')
        
        file_path = get_pdf_file_path(pdf_type)
        backup_path = file_path + '.backup'
        if not os.path.exists(backup_path):
            h.flash_error('Yedek dosya bulunamadı')
            return redirect('/dashboard/legal/')
        
        try:
            if os.path.exists(file_path):
                temp_backup = file_path + '.temp'
                os.rename(file_path, temp_backup)
            
            os.rename(backup_path, file_path)
            
            temp_backup = file_path + '.temp'
            if os.path.exists(temp_backup):
                os.remove(temp_backup)
            
            h.flash_success(f'{LEGAL_PDFS[pdf_type]["title"]} önceki versiyonuna geri yüklendi!')
            log.info(f'Legal PDF restored: {pdf_type} by user {g.user}')
        except Exception as e:
            log.error(f'Error restoring PDF {pdf_type}: {str(e)}')
            h.flash_error(f'Dosya geri yüklenirken hata oluştu: {str(e)}')
        
        return redirect('/dashboard/legal/')


@officialpdf.context_processor
def inject_legal_docs_for_footer():
    """
    Bu fonksiyon, var olan yasal PDF'lerin bir listesini oluşturur ve
    tüm şablonların (örneğin footer) erişebilmesi için context'e enjekte eder.
    """
    docs_for_footer = []
    # LEGAL_PDFS sözlüğündeki her bir doküman için kontrol yap
    for filename, info in LEGAL_PDFS.items():
        file_path = get_pdf_file_path(filename)
        # Sadece sunucuda gerçekten var olan dosyalar için link oluştur
        if os.path.exists(file_path):
            docs_for_footer.append({
                'url': h.url_for('officialpdf.serve', filename=filename),
                'text': info.get('footer_text', info['title']) # footer_text'i kullan, yoksa başlığı al
            })
    return {'legal_docs_for_footer': docs_for_footer}


# URL routing
_legal_docs_view = LegalDocsView.as_view(str(u'index'))
_pdf_upload_view = PDFUploadView.as_view(str(u'upload'))
_pdf_delete_view = PDFDeleteView.as_view(str(u'delete'))
_pdf_restore_view = PDFRestoreView.as_view(str(u'restore'))
_pdf_serve_view = ServePDFView.as_view(str(u'serve')) # <-- YENİ: Serve view'ı için as_view

officialpdf.add_url_rule(u'/', view_func=_legal_docs_view, methods=[u'GET'])
officialpdf.add_url_rule(u'/upload/<pdf_type>', view_func=_pdf_upload_view, methods=[u'POST'])
officialpdf.add_url_rule(u'/delete/<pdf_type>', view_func=_pdf_delete_view, methods=[u'POST'])
officialpdf.add_url_rule(u'/restore/<pdf_type>', view_func=_pdf_restore_view, methods=[u'POST'])
officialpdf.add_url_rule(u'/serve/<filename>', view_func=_pdf_serve_view, methods=[u'GET']) # <-- YENİ: Dosya sunumu için yeni URL kuralı


