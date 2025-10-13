# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
import ckan.plugins.toolkit as toolkit
import ckan.lib.base as base
import ckan.authz as authz
import ckan.model as model
from sqlalchemy import text

mekansal = Blueprint('mekansal', __name__)

def mekansal_view():
    """Mekansal gösterim ve resource yönetim sayfası - Sadece adminler erişebilir"""
    try:
        # Kullanıcının giriş yapmış olup olmadığını kontrol et
        toolkit.check_access('site_read', {})
        
        # Mevcut kullanıcıyı al
        current_user = toolkit.c.userobj
        
        # Admin kontrolü
        if not current_user:
            return base.abort(401, toolkit._('Bu sayfaya erişmek için giriş yapmalısınız'))
        
        # Sysadmin kontrolü
        if not authz.is_sysadmin(current_user.name):
            return base.abort(403, toolkit._('Bu sayfaya sadece sistem yöneticileri erişebilir'))
        
        # Admin ise sayfayı göster - CKAN toolkit.render kullan
        extra_vars = {
            'is_admin': True,
            'current_user': current_user.name
        }
        return toolkit.render('mekansal/index.html', extra_vars)
        
    except toolkit.NotAuthorized:
        return base.abort(403, toolkit._('Bu sayfaya erişim yetkiniz yok'))
    except Exception as e:
        return base.abort(500, toolkit._('Bir hata oluştu: {}').format(str(e)))

def get_spatial_resources():
    """Tüm resource'ları spatial durumlarıyla birlikte getir"""
    try:
        # Admin kontrolü
        current_user = toolkit.c.userobj
        if not current_user or not authz.is_sysadmin(current_user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        query = text("""
            SELECT
                p.id as package_id,
                p.name as package_name,
                p.title as package_title,
                r.id as resource_id,
                r.name as resource_name,
                r.format,
                r.url,
                r.size,
                p.owner_org,
                COALESCE(sr.is_spatial, false) as is_spatial,
                COALESCE(sr.show_on_homepage, false) as show_on_homepage,
                COALESCE(sr.color, '#3388ff') as color,
                sr.added_by,
                sr.updated_date
            FROM package p
            JOIN resource r ON p.id = r.package_id
            LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE p.state = 'active' AND r.state = 'active'
            ORDER BY p.title, r.name
        """)
        
        result = model.Session.execute(query).fetchall()
        
        # Organizasyon isimlerini çek
        org_query = text("SELECT id, title FROM \"group\" WHERE type = 'organization' AND state = 'active'")
        org_result = model.Session.execute(org_query).fetchall()
        org_dict = {row.id: row.title for row in org_result}
        
        resources = []
        for row in result:
            org_title = org_dict.get(row.owner_org, 'Bilinmiyor')
            
            resources.append({
                'package_id': row.package_id,
                'package_name': row.package_name,
                'package_title': row.package_title,
                'resource_id': row.resource_id,
                'resource_name': row.resource_name or 'İsimsiz Kaynak',
                'format': row.format or '',
                'url': row.url or '',
                'size': row.size,
                'organization_title': org_title,
                'is_spatial': bool(row.is_spatial),
                'show_on_homepage': bool(row.show_on_homepage),
                'color': row.color or '#3388ff',
                'added_by': row.added_by,
                'updated_date': row.updated_date.isoformat() if row.updated_date else None
            })
        
        return jsonify({
            'success': True,
            'count': len(resources),
            'resources': resources
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def toggle_spatial_resource():
    """Resource'un spatial durumunu değiştir"""
    try:
        # Admin kontrolü
        current_user = toolkit.c.userobj
        if not current_user or not authz.is_sysadmin(current_user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON data gerekli'}), 400
        
    resource_id = data.get('resource_id')
    is_spatial = data.get('is_spatial', False)
    
    if not resource_id:
        return jsonify({'error': 'resource_id gerekli'}), 400
    
    try:
        # Resource'un var olup olmadığını kontrol et
        resource_check = model.Session.execute(
            text("SELECT id FROM resource WHERE id = :resource_id AND state = 'active'"),
            {'resource_id': resource_id}
        ).fetchone()
        
        if not resource_check:
            return jsonify({'error': 'Resource bulunamadı'}), 404
        
        # Spatial resource kaydını güncelle veya oluştur
        existing = model.Session.execute(
            text("SELECT id FROM spatial_resources WHERE resource_id = :resource_id"),
            {'resource_id': resource_id}
        ).fetchone()
        
        if existing:
            # Güncelle
            model.Session.execute(
                text("""
                    UPDATE spatial_resources 
                    SET is_spatial = :is_spatial, 
                        added_by = :added_by,
                        updated_date = CURRENT_TIMESTAMP
                    WHERE resource_id = :resource_id
                """),
                {
                    'resource_id': resource_id,
                    'is_spatial': is_spatial,
                    'added_by': current_user.name
                }
            )
        else:
            # Oluştur
            model.Session.execute(
                text("""
                    INSERT INTO spatial_resources (resource_id, is_spatial, added_by)
                    VALUES (:resource_id, :is_spatial, :added_by)
                """),
                {
                    'resource_id': resource_id,
                    'is_spatial': is_spatial,
                    'added_by': current_user.name
                }
            )
        
        model.Session.commit()
        
        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'is_spatial': is_spatial,
            'updated_by': current_user.name
        })
        
    except Exception as e:
        model.Session.rollback()
        return jsonify({'error': str(e)}), 500

def get_spatial_only():
    """Sadece spatial olarak işaretlenmiş resource'ları getir"""
    try:
        query = text("""
            SELECT
                p.id as package_id,
                p.name as package_name,
                p.title as package_title,
                r.id as resource_id,
                r.name as resource_name,
                r.format,
                r.url,
                r.size,
                COALESCE(sr.color, '#3388ff') as color
            FROM package p
            JOIN resource r ON p.id = r.package_id
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE p.state = 'active'
            AND r.state = 'active'
            AND sr.is_spatial = true
            ORDER BY p.title, r.name
        """)
        
        result = model.Session.execute(query).fetchall()
        
        resources = []
        for row in result:
            resources.append({
                'package_id': row.package_id,
                'package_name': row.package_name,
                'package_title': row.package_title,
                'resource_id': row.resource_id,
                'resource_name': row.resource_name or 'İsimsiz Kaynak',
                'format': row.format or '',
                'url': row.url or '',
                'size': row.size,
                'color': row.color or '#3388ff'
            })
        
        return jsonify({
            'success': True,
            'count': len(resources),
            'spatial_resources': resources
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Route'ları ekle
mekansal.add_url_rule('/dashboard/mekansal', view_func=mekansal_view, methods=['GET'])
mekansal.add_url_rule('/api/spatial-resources', view_func=get_spatial_resources, methods=['GET'])
mekansal.add_url_rule('/api/spatial-resources/toggle', view_func=toggle_spatial_resource, methods=['POST'])
mekansal.add_url_rule('/api/spatial-resources/list', view_func=get_spatial_only, methods=['GET'])