# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
import ckan.plugins.toolkit as toolkit
import ckan.model as model
import ckan.authz as authz
from sqlalchemy import text
import pandas as pd
import requests
import re
import io
import os
import shutil
import zipfile
import tempfile
import urllib.parse
import json
from urllib.parse import urljoin, urlparse
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
import math

# SHP ve diğer formatlar için yeni kütüphaneler
try:
    import fiona
    from shapely.geometry import shape, mapping
    SPATIAL_SUPPORT = True
    print("Spatial support (fiona/shapely) available")
except ImportError:
    print("Warning: fiona/shapely not available. SHP support disabled.")
    SPATIAL_SUPPORT = False

spatial_api = Blueprint('spatial_api', __name__)

def convert_numpy_types(obj):
    """NumPy tiplerini Python native tiplerine çevir (JSON serialization için)"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    return obj

def get_absolute_url(base_url, resource_url, package_name=None, resource_id=None):
    """Resource URL'ini mutlak URL'ye çevirir. URL zaten tam ise olduğu gibi döndürür."""
    if not resource_url:
        return None

    # Eğer zaten tam URL ise, olduğu gibi döndür
    if resource_url.startswith(('http://', 'https://')):
        return resource_url

    # Eğer sadece dosya adı ise (path'te / yoksa veya sadece /filename ise)
    # CKAN download URL pattern'ini kullan
    if package_name and resource_id:
        # Dosya adını temizle (baştaki / varsa kaldır)
        filename = resource_url.lstrip('/')

        # CKAN standard download URL formatı
        site_url = base_url or toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        download_url = f"{site_url}/dataset/{package_name}/resource/{resource_id}/download/{filename}"
        print(f"[DEBUG] Constructed download URL: {download_url}")
        return download_url

    # Eğer göreceli bir URL ise, base URL ile birleştir
    if base_url:
        return urljoin(base_url, resource_url)

    # Fallback: CKAN storage URL'i ile birleştir
    site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
    return urljoin(site_url, resource_url)

@spatial_api.route('/api/spatial-resources')
def get_spatial_resources():
    """Tüm resource'ları spatial durumlarıyla birlikte getir"""
    try:
        # Admin kontrolü
        if not authz.is_sysadmin(toolkit.c.userobj.name if toolkit.c.userobj else None):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403
    
    query = text("""
        SELECT
            p.id as package_id,
            p.name as package_name,
            p.title as package_title,
            p.owner_org,
            r.id as resource_id,
            r.name as resource_name,
            r.format,
            r.url,
            r.size,
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
    
    # Base URL
    site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
    
    resources = []
    for row in result:
        org_title = org_dict.get(row.owner_org, 'Bilinmiyor')

        # URL'yi mutlak hale getir
        absolute_url = get_absolute_url(site_url, row.url, row.package_name, row.resource_id)

        resources.append({
            'package_id': row.package_id,
            'package_name': row.package_name,
            'package_title': row.package_title,
            'resource_id': row.resource_id,
            'resource_name': row.resource_name or 'İsimsiz Kaynak',
            'format': row.format,
            'url': absolute_url,
            'original_url': row.url,
            'size': row.size,
            'organization_title': org_title,
            'is_spatial': row.is_spatial,
            'show_on_homepage': row.show_on_homepage,
            'color': row.color or '#3388ff',
            'added_by': row.added_by,
            'updated_date': row.updated_date.isoformat() if row.updated_date else None
        })
    
    return jsonify({
        'success': True,
        'count': len(resources),
        'resources': resources
    })

@spatial_api.route('/api/spatial-resources/toggle', methods=['POST'])
def toggle_spatial_resource():
    """Resource'un spatial durumunu değiştir"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    resource_id = data.get('resource_id')
    is_spatial = data.get('is_spatial', False)

    if not resource_id:
        return jsonify({'error': 'resource_id gerekli'}), 400

    try:
        # Resource'un var olup olmadığını kontrol et
        resource_check = model.Session.execute(
            text("SELECT id FROM resource WHERE id = :resource_id"),
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
                    'added_by': user.name
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
                    'added_by': user.name
                }
            )

        model.Session.commit()

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'is_spatial': is_spatial,
            'updated_by': user.name
        })

    except Exception as e:
        model.Session.rollback()
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/color', methods=['POST'])
def set_resource_color(resource_id):
    """Resource'un harita rengini kaydet"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    color = data.get('color', '#3388ff')

    if not resource_id:
        return jsonify({'error': 'resource_id gerekli'}), 400

    # Hex color validation
    if not color or not color.startswith('#') or len(color) != 7:
        return jsonify({'error': 'Geçersiz renk formatı. #RRGGBB formatında olmalı'}), 400

    try:
        # Spatial resource var mı kontrol et
        existing = model.Session.execute(
            text("SELECT id FROM spatial_resources WHERE resource_id = :resource_id"),
            {'resource_id': resource_id}
        ).fetchone()

        if not existing:
            return jsonify({'error': 'Resource spatial olarak işaretlenmemiş'}), 404

        # Rengi güncelle
        model.Session.execute(
            text("""
                UPDATE spatial_resources
                SET color = :color,
                    updated_date = CURRENT_TIMESTAMP
                WHERE resource_id = :resource_id
            """),
            {
                'resource_id': resource_id,
                'color': color
            }
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'color': color,
            'updated_by': user.name
        })

    except Exception as e:
        model.Session.rollback()
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/color', methods=['GET'])
def get_resource_color(resource_id):
    """Resource'un harita rengini getir"""
    try:
        query = text("""
            SELECT color
            FROM spatial_resources
            WHERE resource_id = :resource_id
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result:
            return jsonify({'success': False, 'color': '#3388ff'})

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'color': result.color or '#3388ff'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/list')
def get_spatial_resource_list():
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
                sr.added_by,
                sr.updated_date,
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

        # Base URL
        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')

        resources = []
        for row in result:
            # URL'yi mutlak hale getir
            absolute_url = get_absolute_url(site_url, row.url, row.package_name, row.resource_id)

            resource_dict = {
                'package_id': row.package_id,
                'package_name': row.package_name,
                'package_title': row.package_title,
                'resource_id': row.resource_id,
                'resource_name': row.resource_name or 'İsimsiz Kaynak',
                'format': row.format,
                'url': absolute_url,
                'original_url': row.url,
                'size': row.size,
                'added_by': row.added_by,
                'updated_date': row.updated_date.isoformat() if row.updated_date else None,
                'color': row.color or '#3388ff'
            }
            print(f"[DEBUG LIST] Resource {row.resource_id[:8]}... has color: {row.color}")
            resources.append(resource_dict)

        return jsonify({
            'success': True,
            'count': len(resources),
            'spatial_resources': resources,
            'DEBUG_FILE_TIMESTAMP': '2025-10-13-13:20:00',
            'DEBUG_COLOR_FIELD_EXISTS': True
        })

    except Exception as e:
        print(f"Spatial resource list error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Spatial resource listesi alınırken hata: {str(e)}',
            'spatial_resources': []
        }), 500

@spatial_api.route('/api/spatial-resources/homepage-map')
def get_homepage_map_resources():
    """Anasayfa haritasında gösterilecek spatial resource'ları getir"""
    try:
        query = text("""
            SELECT
                r.id as resource_id,
                r.name as resource_name,
                r.format,
                r.url,
                sr.show_on_homepage,
                COALESCE(sr.color, '#3388ff') as color
            FROM resource r
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.state = 'active'
            AND sr.is_spatial = true
            AND COALESCE(sr.show_on_homepage, false) = true
            ORDER BY r.name
        """)

        result = model.Session.execute(query).fetchall()

        resources = []
        for row in result:
            # URL'i mutlak hale getir
            url = row.url
            if url and not url.startswith(('http://', 'https://')):
                url = request.host_url.rstrip('/') + url

            resources.append({
                'resource_id': row.resource_id,
                'resource_name': row.resource_name or 'İsimsiz Kaynak',
                'format': row.format,
                'url': url,
                'show_on_homepage': row.show_on_homepage,
                'color': row.color or '#3388ff'
            })

        return jsonify({
            'success': True,
            'count': len(resources),
            'resources': resources
        })

    except Exception as e:
        print(f"Homepage map resources error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Anasayfa harita kaynakları alınırken hata: {str(e)}',
            'resources': []
        }), 500

@spatial_api.route('/api/spatial-resources/homepage-map/toggle', methods=['POST'])
def toggle_homepage_map_resource():
    """Resource'un anasayfa haritasında gösterilme durumunu değiştir"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    resource_id = data.get('resource_id')
    show_on_homepage = data.get('show_on_homepage', False)

    if not resource_id:
        return jsonify({'error': 'resource_id gerekli'}), 400

    try:
        # Resource'un spatial olup olmadığını kontrol et
        check = model.Session.execute(
            text("""
                SELECT sr.id, sr.is_spatial
                FROM spatial_resources sr
                WHERE sr.resource_id = :resource_id
            """),
            {'resource_id': resource_id}
        ).fetchone()

        if not check:
            return jsonify({'error': 'Resource spatial olarak işaretlenmemiş'}), 404

        if not check.is_spatial:
            return jsonify({'error': 'Resource önce spatial olarak işaretlenmelidir'}), 400

        # show_on_homepage alanını güncelle
        model.Session.execute(
            text("""
                UPDATE spatial_resources
                SET show_on_homepage = :show_on_homepage,
                    updated_date = CURRENT_TIMESTAMP
                WHERE resource_id = :resource_id
            """),
            {
                'resource_id': resource_id,
                'show_on_homepage': show_on_homepage
            }
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'show_on_homepage': show_on_homepage,
            'updated_by': user.name
        })

    except Exception as e:
        model.Session.rollback()
        print(f"Homepage map toggle error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/data')
def get_spatial_data(resource_id):
    """Resource'un spatial verisini parse ederek harita için hazırlar"""

    # --- YENİ KARAR MEKANİZMASI ---

    # 1. URL'leri hatalı olan ve manuel olarak düzeltilmesi gereken TÜM kaynakların listesi
    MANUAL_URLS = {
        # Millet Bahçeleri
        "2541d889-fd96-4611-bfae-7a7419e10a7d": "https://dataizmir.izka.org.tr/dataset/ec344742-2007-4a4e-896a-de12e492395c/resource/2541d889-fd96-4611-bfae-7a7419e10a7d/download/izmir_millet_bahceleri.geojson",
        # TUSAGA-Aktif İstasyon Bilgileri
        "471fafb6-42d2-424d-a256-ada765e52945": "https://dataizmir.izka.org.tr/dataset/da6be125-b702-4bb7-b2ee-ad404621be7f/resource/471fafb6-42d2-424d-a256-ada765e52945/download/izmir_tusaga_aktif_istasyonlari.geojson",
        # Banliyö İstasyonları
        "5d8f0495-e7ff-42cd-91bd-ff127c980bd0": "https://acikveri.bizizmir.com/dataset/e3854620-a776-47d4-a63c-9180fc1d4e9e/resource/5d8f0495-e7ff-42cd-91bd-ff127c980bd0/download/izban.geojson",
        # Tramvay Hatları
        "97905570-23b5-4066-80e1-37d611d2962c": "https://acikveri.bizizmir.com/dataset/9447be73-ecc1-4715-b6be-f0cbe915aed9/resource/97905570-23b5-4066-80e1-37d611d2962c/download/tramvay.geojson",
        # İzmir Deprem Tehlike Verisi
        "6c68a5ff-78cd-417b-a061-5a7afcba018c": "https://dataizmir.izka.org.tr/dataset/9a713c11-30b9-497c-8504-afe00d69f14f/resource/6c68a5ff-78cd-417b-a061-5a7afcba018c/download/izmri_deprem_tehlike.geojson",
        # Bisiklet Altyapı Haritası
        "966cffaa-ccf1-4306-8da9-ac0cffbebb88": "https://acikveri.bizizmir.com/dataset/e3553de7-c06d-4082-892b-974d2187a234/resource/966cffaa-ccf1-4306-8da9-ac0cffbebb88/download/bisikletyollari.geojson",
        # Metro İstasyonları
        "7424cbc7-fa12-417f-9770-a0c104fc9475": "https://acikveri.bizizmir.com/dataset/d5c5522a-d6f6-4758-a2ec-0dd520e06f55/resource/7424cbc7-fa12-417f-9770-a0c104fc9475/download/metro.geojson",
        "3f35e70e-0d3b-4c44-b92b-043d85bc903c":"https://dataizmir.izka.org.tr/dataset/660bad49-6d99-4099-a827-4d3734b1d57c/resource/3f35e70e-0d3b-4c44-b92b-043d85bc903c/download/merkez.csv",
        # YENİ EKLENEN CSV/EXCEL KAYNAKLARI
        # EGMS AEPND V2022.0 (XLSX)
        "d5662ab6-60b4-4bc8-8f5c-3d840391e73e": "https://dataizmir.izka.org.tr/dataset/ede74092-347f-4fa3-ac5b-d158fee0e1a5/resource/d5662ab6-60b4-4bc8-8f5c-3d840391e73e/download/egms_aepnd_v2022.0.xlsx",
        # EGMS AEPND V2023.0 (CSV)
        "84dac4aa-a10d-4ed6-afad-915fc8585edb": "https://dataizmir.izka.org.tr/dataset/ede74092-347f-4fa3-ac5b-d158fee0e1a5/resource/84dac4aa-a10d-4ed6-afad-915fc8585edb/download/egms_aepnd_v2023.0.csv",
        # Yeni Hayvan İçme Suyu (HİS) Göletleri
        "413173de-3443-4abb-a194-611b102d201c": "https://dataizmir.izka.org.tr/dataset/09e585f5-00b4-4f47-8a03-6a841a242cff/resource/413173de-3443-4abb-a194-611b102d201c/download/hisgoleti-yeni.xlsx",
        # Otobüs Hat Güzergahlarının Konum Bilgileri
        "211488": "https://openfiles.izmir.bel.tr/211488/docs/eshot-otobus-hat-guzergahlari.csv"
    }

    # 2. CORS sorunu olmayan ve FRONTEND'de işlenecek kaynakların ID'leri
    FRONTEND_FETCH_IDS = {
        "2541d889-fd96-4611-bfae-7a7419e10a7d",  # Millet Bahçeleri
        "471fafb6-42d2-424d-a256-ada765e52945"   # TUSAGA-Aktif İstasyon Bilgileri
    }

    # 3. Karar verme mantığı
    if resource_id in MANUAL_URLS:
        correct_url = MANUAL_URLS[resource_id]
        
        # CSV/Excel kaynakları için format'ı belirle
        if correct_url.endswith('.csv'):
            print(f"Manuel CSV URL kullanılıyor: {resource_id}")
            return process_tabular_data(correct_url, 'csv', resource_id)
        elif correct_url.endswith('.xlsx') or correct_url.endswith('.xls'):
            print(f"Manuel Excel URL kullanılıyor: {resource_id}")
            return process_tabular_data(correct_url, 'xlsx', resource_id)
        elif resource_id in FRONTEND_FETCH_IDS:
            # Bu kaynak sorunsuz, URL'ini frontend'e gönder o halletsin.
            print(f"Manuel URL (Frontend Fetch) kullanılıyor: {resource_id}")
            return jsonify({
                'success': True, 'type': 'geojson_url', 'url': correct_url,
                'message': 'Manual GeoJSON URL will be fetched by the frontend.'
            })
        else:
            # Bu kaynak CORS sorunu yaşıyor, backend'de işleyip veriyi hazır gönderelim.
            print(f"Manuel URL (Backend Fetch) kullanılıyor: {resource_id}")
            return process_geojson(correct_url)

    # Manuel listede olmayan kaynaklar için standart mantık devam ediyor
    try:
        query = text("""
            SELECT r.url, r.format, p.name as package_name
            FROM resource r
            JOIN package p ON r.package_id = p.id
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND sr.is_spatial = true
        """)
        
        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result: return jsonify({'error': 'Spatial resource bulunamadı'}), 404

        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url, result.package_name, resource_id)

        if not url: return jsonify({'error': 'Geçersiz resource URL'}), 400
        
        format_type = (result.format or '').lower()
        
        print(f"Processing spatial data: {url} (format: {format_type})")

        # Varsayılan olarak tüm GeoJSON'lar artık backend'de işlenecek
        if format_type == 'geojson':
            return process_geojson(url)

        # Diğer formatlar için mantık aynı kalıyor
        if format_type in ['json', 'api', 'rest', 'soap']:
            return jsonify({'success': True, 'type': 'api', 'url': url})
        
        if format_type in ['wms', 'wfs']:
            return jsonify({'success': True, 'type': format_type, 'url': url})
        
        elif format_type == 'geotiff':
            return jsonify({'success': True, 'type': 'geotiff', 'url': url})
        
        elif format_type in ['shp', 'zip']:
            # SHP dosyalarını backend'de işleyip GeoJSON'a çevir
            if SPATIAL_SUPPORT:
                return process_spatial_files(url, 'shp')
            else:
                # Fallback: Frontend'e URL gönder (shp.js ile işlesin)
                proxy_url = f"{site_url}/dataset/{result.package_name}/resource/{resource_id}/download"
                return jsonify({'success': True, 'type': 'shp', 'url': proxy_url})

        elif format_type in ['csv', 'xls', 'xlsx']:
            return process_tabular_data(url, format_type, resource_id)
        elif format_type in ['kml', 'gpx'] and SPATIAL_SUPPORT:
            return process_spatial_files(url, format_type)
        else:
            return jsonify({'error': f'Desteklenmeyen format: {format_type}'}), 400
            
    except Exception as e:
        print(f"Spatial data processing error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@spatial_api.route('/api/spatial-resources/<resource_id>/columns')
def get_resource_columns(resource_id):
    """Resource'un sütunlarını döndür (manuel seçim için)"""
    try:
        query = text("""
            SELECT r.url, r.format, p.name as package_name
            FROM resource r
            JOIN package p ON r.package_id = p.id
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND sr.is_spatial = true
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result:
            return jsonify({'error': 'Resource bulunamadı'}), 404

        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url, result.package_name, resource_id)
        format_type = (result.format or '').lower()
        
        if format_type not in ['csv', 'xls', 'xlsx', 'json', 'api', 'rest']:
            return jsonify({'error': 'Sadece CSV/Excel/JSON/API dosyalar için sütun listesi'}), 400
        
        if format_type == 'csv':
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()

            # Delimiter auto-detection (öncelik noktalı virgülde)
            df = None
            delimiters = [';', ',', '\t', '|']
            encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1254']

            for delimiter in delimiters:
                for encoding in encodings:
                    try:
                        df = pd.read_csv(io.StringIO(response.text), delimiter=delimiter, encoding=encoding, nrows=5)
                        if len(df.columns) > 1:
                            print(f"Column detection CSV parsed: delimiter='{delimiter}', encoding='{encoding}', columns={len(df.columns)}")
                            break
                    except:
                        continue
                if df is not None and len(df.columns) > 1:
                    break

            if df is None or len(df.columns) <= 1:
                # Fallback to default
                df = pd.read_csv(io.StringIO(response.text), nrows=5)
        elif format_type in ['xls', 'xlsx']:
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            df = pd.read_excel(io.BytesIO(response.content), nrows=5)
        elif format_type in ['json', 'api', 'rest']:
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            json_data = response.json()
            if isinstance(json_data, dict):
                if 'data' in json_data and isinstance(json_data['data'], list):
                    json_data = json_data['data']
                elif 'results' in json_data and isinstance(json_data['results'], list):
                    json_data = json_data['results']
                elif 'items' in json_data and isinstance(json_data['items'], list):
                    json_data = json_data['items']
                elif 'onemliyer' in json_data and isinstance(json_data['onemliyer'], list):
                    json_data = json_data['onemliyer']
                elif 'records' in json_data and isinstance(json_data['records'], list):
                    json_data = json_data['records']
                else:
                    for key, value in json_data.items():
                        if isinstance(value, list) and len(value) > 0:
                            json_data = value
                            break
            if isinstance(json_data, list) and len(json_data) > 0:
                df = pd.DataFrame(json_data[:5])
            else:
                return jsonify({'error': 'JSON verisi uygun formatta değil'}), 400
        
        # NaN değerlerini None'a çevir (JSON için)
        df = df.where(pd.notna(df), None)

        return jsonify({
            'success': True,
            'columns': list(df.columns),
            'sample_data': df.to_dict('records')
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/process-manual', methods=['POST'])
def process_with_manual_columns(resource_id):
    """Manuel seçilen sütunlarla veriyi işle"""
    try:
        data = request.get_json()
        lat_col = data.get('lat_column')
        lon_col = data.get('lon_column')
        
        if not lat_col or not lon_col:
            return jsonify({'error': 'Enlem ve boylam sütunları gerekli'}), 400
        
        query = text("""
            SELECT r.url, r.format, p.name as package_name
            FROM resource r
            JOIN package p ON r.package_id = p.id
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND sr.is_spatial = true
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result:
            return jsonify({'error': 'Resource bulunamadı'}), 404

        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url, result.package_name, resource_id)
        format_type = (result.format or '').lower()
        
        if format_type == 'csv':
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            try:
                df = pd.read_csv(io.StringIO(response.text), encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(io.StringIO(response.text), encoding='latin-1')
        elif format_type in ['xls', 'xlsx']:
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            df = pd.read_excel(io.BytesIO(response.content))
        elif format_type in ['json', 'api', 'rest']:
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            json_data = response.json()
            if isinstance(json_data, dict):
                if 'data' in json_data and isinstance(json_data['data'], list):
                    json_data = json_data['data']
                elif 'results' in json_data and isinstance(json_data['results'], list):
                    json_data = json_data['results']
                elif 'items' in json_data and isinstance(json_data['items'], list):
                    json_data = json_data['items']
                elif 'onemliyer' in json_data and isinstance(json_data['onemliyer'], list):
                    json_data = json_data['onemliyer']
                elif 'records' in json_data and isinstance(json_data['records'], list):
                    json_data = json_data['records']
                else:
                    for key, value in json_data.items():
                        if isinstance(value, list) and len(value) > 0:
                            json_data = value
                            break
            if isinstance(json_data, list):
                df = pd.DataFrame(json_data)
            else:
                return jsonify({'error': 'JSON verisi uygun formatta değil'}), 400
        else:
            return jsonify({'error': 'Desteklenmeyen format'}), 400
        
        # Seçilen sütunların var olup olmadığını kontrol et
        if lat_col not in df.columns:
            return jsonify({'error': f'Enlem sütunu bulunamadı: {lat_col}'}), 400
        if lon_col not in df.columns:
            return jsonify({'error': f'Boylam sütunu bulunamadı: {lon_col}'}), 400
        
        coord_columns = {'lat': lat_col, 'lon': lon_col}
        geojson_data = convert_to_geojson(df, coord_columns)
        
        return jsonify({
            'success': True,
            'type': 'manual_processing',
            'data': geojson_data,
            'used_columns': coord_columns,
            'total_features': len(geojson_data.get('features', []))
        })
        
    except Exception as e:
        print(f"Manual column processing error: {str(e)}")
        return jsonify({'error': f'Manuel işleme hatası: {str(e)}'}), 500

# YARDIMCI FONKSİYONLAR

def process_geojson(url):
    """GeoJSON dosyasını direkt döndür"""
    try:
        print(f"[DEBUG] GeoJSON URL: {url}")
        
        # SSL ve timeout ayarları
        response = requests.get(
            url, 
            timeout=60,  # Timeout artırıldı
            verify=False,  # SSL doğrulama kapalı
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/geo+json, application/json, */*',
                'Connection': 'keep-alive'
            }
        )
        response.raise_for_status()
        
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Content-Type: {response.headers.get('content-type')}")
        print(f"[DEBUG] Response size: {len(response.content)} bytes")
        
        geojson_data = response.json()
        
        if geojson_data.get('type') != 'FeatureCollection':
            if geojson_data.get('type') == 'Feature':
                geojson_data = {
                    "type": "FeatureCollection",
                    "features": [geojson_data]
                }
            else:
                return jsonify({'error': 'Geçersiz GeoJSON formatı. "Feature" veya "FeatureCollection" olmalı.'}), 400

        return jsonify({
            'success': True,
            'type': 'geojson',
            'data': geojson_data
        })
    except requests.exceptions.SSLError as e:
        print(f"[DEBUG] SSL Error: {str(e)}")
        return jsonify({'error': f'SSL sertifika hatası: {str(e)}'}), 500
    except requests.exceptions.Timeout as e:
        print(f"[DEBUG] Timeout Error: {str(e)}")
        return jsonify({'error': f'Bağlantı zaman aşımı: {str(e)}'}), 500
    except Exception as e:
        print(f"[DEBUG] General Error: {str(e)}")
        return jsonify({'error': f'GeoJSON yüklenemedi: {str(e)}'}), 500

def process_spatial_files(url, format_type):
    """KML, GPX gibi spatial dosyaları işle"""
    
    if not SPATIAL_SUPPORT:
        return jsonify({
            'error': 'Spatial file desteği yüklü değil',
            'suggestion': 'pip install fiona geopandas shapely komutlarını çalıştırın'
        }), 500
    
    temp_dir = None
    
    try:
        print(f"Processing spatial file: {url} (format: {format_type})")
        
        response = requests.get(url, stream=True, timeout=60, verify=False)
        response.raise_for_status()
        
        temp_dir = tempfile.mkdtemp(prefix='spatial_processing_')
        print(f"Created temp directory: {temp_dir}")
        
        parsed_url = urlparse(url)
        file_extension = os.path.splitext(parsed_url.path)[1].lower()
        
        if not file_extension:
            file_extension = f'.{format_type}'
        
        temp_file_path = os.path.join(temp_dir, f'data{file_extension}')
        
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
        
        print(f"File downloaded to: {temp_file_path}")
        
        geojson_data = None

        if format_type in ['kml', 'gpx']:
            geojson_data = process_other_formats(temp_file_path, format_type)
        elif format_type == 'shp':
            geojson_data = process_shp_file(temp_file_path)
        else:
            return jsonify({
                'error': f'Desteklenmeyen spatial format: {format_type}'
            }), 400
        
        if not geojson_data:
            return jsonify({
                'error': 'Spatial dosya işlenemedi'
            }), 500
        
        return jsonify({
            'success': True,
            'type': f'spatial_{format_type}',
            'data': geojson_data,
        })
        
    except Exception as e:
        print(f"Spatial file processing error: {str(e)}")
        return jsonify({
            'error': f'{format_type.upper()} dosyası işlenemedi: {str(e)}',
            'suggestion': 'Dosyanın bozuk olmadığından ve doğru formatta olduğundan emin olun'
        }), 500
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"Warning: Could not clean temp directory: {e}")

def process_other_formats(file_path, format_type):
    """KML, GPX gibi diğer formatları işle"""
    try:
        print(f"Processing {format_type} file: {file_path}")
        
        geojson_features = []
        with fiona.open(file_path, 'r') as source:
            print(f"{format_type.upper()} CRS: {source.crs}")
            print(f"Feature count: {len(source)}")
            
            for feature in source:
                geojson_features.append({
                    "type": "Feature",
                    "geometry": feature['geometry'],
                    "properties": feature['properties'] or {}
                })
        
        geojson_data = {
            "type": "FeatureCollection",
            "features": geojson_features
        }
        
        print(f"Converted {len(geojson_features)} features from {format_type.upper()} to GeoJSON")
        return geojson_data
        
    except Exception as e:
        print(f"{format_type.upper()} processing error: {str(e)}")
        raise

def process_api_data(url, format_type, resource_id):
    """API/JSON endpoint'lerini parse et ve koordinat sütunlarını tespit et"""
    try:
        print(f"API verisi işleniyor: {url} ({format_type})")

        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        }

        try:
            response = requests.get(url, timeout=45, verify=False, headers=headers)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"API isteği başarısız oldu: {e}")
            return jsonify({
                'success': False,
                'error': f"Hedef API'ye ulaşılamadı: {e.__class__.__name__}",
                'details': str(e)
            }), 502

        json_data = response.json()
        
        if isinstance(json_data, dict):
            possible_keys = ['data', 'results', 'items', 'onemliyer', 'records', 'features']
            data_found = False
            for key in possible_keys:
                if key in json_data and isinstance(json_data[key], list):
                    json_data = json_data[key]
                    data_found = True
                    break
            
            if not data_found:
                 for key, value in json_data.items():
                    if isinstance(value, list) and len(value) > 0:
                        json_data = value
                        break

        if not isinstance(json_data, list):
            return jsonify({'success': False, 'error': 'API yanıtı beklenen liste formatında değil'}), 400

        if len(json_data) == 0:
            return jsonify({'success': False, 'error': 'API yanıtı boş veri döndürdü'}), 400
        
        df = pd.DataFrame(json_data)
        coord_result = smart_detect_coordinate_columns(df)
        
        if not coord_result['found']:
            return jsonify({
                'success': False,
                'error': 'Koordinat sütunları otomatik bulunamadı',
                'columns': list(df.columns)
            })
        
        geojson_data = convert_to_geojson(df, coord_result['columns'])
        
        return jsonify({
            'success': True,
            'type': 'api_json',
            'data': geojson_data
        })
        
    except Exception as e:
        print(f"API data işleme hatası (genel): {e}")
        return jsonify({'success': False, 'error': f'API verisi işlenemedi: {str(e)}'}), 500

def clean_coordinate_value(value):
    """Koordinat değerini temizle - derece sembolü ve diğer karakterleri kaldır"""
    if pd.isna(value):
        return None
    
    # String'e çevir
    value_str = str(value).strip()
    
    # Boş string kontrolü
    if not value_str or value_str.lower() == 'nan':
        return None
    
    # Derece sembolünü ve diğer özel karakterleri kaldır
    # Unicode derece sembolü: ° (\u00B0)
    # Diğer varyasyonlar: º, ˚
    value_str = value_str.replace('°', '')
    value_str = value_str.replace('º', '')
    value_str = value_str.replace('˚', '')
    value_str = value_str.replace('′', '')  # Dakika işareti
    value_str = value_str.replace('″', '')  # Saniye işareti
    value_str = value_str.replace("'", '')  # Dakika için alternatif
    value_str = value_str.replace('"', '')  # Saniye için alternatif
    
    # Virgülü noktaya çevir
    value_str = value_str.replace(',', '.')
    
    # Gereksiz boşlukları temizle
    value_str = value_str.strip()
    
    # Float'a çevirmeyi dene
    try:
        coord = float(value_str)
        return coord
    except (ValueError, TypeError):
        return None

def process_tabular_data(url, format_type, resource_id):
    """CSV/Excel dosyalarını parse et ve koordinat sütunlarını akıllıca tespit et"""
    try:
        response = requests.get(url, timeout=30, verify=False)
        response.raise_for_status()
    except Exception as e:
        print(f"Tabular data işleme hatası: {str(e)}")
        return jsonify({'error': f'Tabular data işlenemedi: {str(e)}'}), 500

    df = None
    if format_type == 'csv':
        # ÖNCELİKLE NOKTALÎ VİRGÜL DENENMELİ!
        delimiters = [';', ',', '\t', '|']
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1254', 'utf-8-sig']

        for delimiter in delimiters:
            for encoding in encodings:
                try:
                    df = pd.read_csv(io.StringIO(response.text), delimiter=delimiter, encoding=encoding)
                    if len(df.columns) > 1:
                        print(f"CSV başarıyla parse edildi: delimiter='{delimiter}', encoding='{encoding}', sütun sayısı={len(df.columns)}")
                        break
                except Exception as e:
                    try:
                        # Binary olarak da dene
                        df = pd.read_csv(io.BytesIO(response.content), delimiter=delimiter, encoding=encoding)
                        if len(df.columns) > 1:
                            print(f"CSV başarıyla parse edildi (binary): delimiter='{delimiter}', encoding='{encoding}'")
                            break
                    except:
                        continue
            if df is not None and len(df.columns) > 1:
                break
        
        if df is None or len(df.columns) <= 1:
            return jsonify({'error': 'CSV dosyası parse edilemedi. Farklı delimiter ve encoding deneyin.'}), 500
    elif format_type in ['xls', 'xlsx']:
        df = pd.read_excel(io.BytesIO(response.content))
    
    print(f"DataFrame sütunları: {list(df.columns)}")
    print(f"İlk 3 satır:\n{df.head(3)}")
    
    coord_result = smart_detect_coordinate_columns(df)
    
    if not coord_result['found']:
        # NaN değerlerini temizle
        clean_df = df.head(3).where(pd.notna(df.head(3)), None)

        return jsonify({
            'success': False,
            'error': 'Koordinat sütunları otomatik tespit edilemedi',
            'columns': list(df.columns),
            'sample_data': clean_df.to_dict('records'),
            'suggestions': coord_result['suggestions'],
        }), 400
    
    geojson_data = convert_to_geojson(df, coord_result['columns'])
    
    return jsonify({
        'success': True,
        'type': 'tabular',
        'data': geojson_data,
        'detected_columns': coord_result['columns'],
        'detection_confidence': coord_result['confidence']
    })

def smart_detect_coordinate_columns(df):
    """DataFrame'den koordinat sütunlarını akıllıca tespit et"""
    
    print(f"Koordinat tespiti başlıyor. Sütunlar: {list(df.columns)}")
    
    lat_patterns = [
        r'^(lat|latitude|enlem|y|kuzey)$',
        r'^lat', r'lat$', r'^latitude', r'latitude$', r'^#+latitude$', r'^#+lat$',
        r'^enlem', r'enlem$', r'^y_', r'_y$',
        r'.*lat.*', r'.*enlem.*', r'.*kuzey.*',
        r'.*coord.*lat.*', r'.*geo.*lat.*', r'.*point.*lat.*'
    ]

    lon_patterns = [
        r'^(lon|lng|longitude|boylam|x|dogu)$',
        r'^lon', r'lon$', r'^lng', r'lng$',
        r'^longitude', r'longitude$', r'^boylam', r'boylam$',
        r'^x_', r'_x$',
        r'.*lon.*', r'.*lng.*', r'.*boylam.*', r'.*dogu.*',
        r'.*coord.*lon.*', r'.*geo.*lon.*', r'.*point.*lon.*'
    ]
    columns_lower = [col.lower().strip() for col in df.columns]
    
    lat_candidates = []
    lon_candidates = []
    
    for i, col_lower in enumerate(columns_lower):
        original_col = df.columns[i]
        
        print(f"Sütun kontrol ediliyor: '{original_col}' -> '{col_lower}'")
        
        for priority, pattern in enumerate(lat_patterns):
            if re.search(pattern, col_lower, re.IGNORECASE):
                print(f"   Latitude pattern bulundu: '{pattern}' -> '{original_col}'")
                numeric_ratio = check_numeric_ratio(df[original_col])
                print(f"   Numeric ratio: {numeric_ratio}")
                if numeric_ratio > 0.3:
                    coord_check = check_coordinate_range(df[original_col], 'lat')
                    print(f"   Coordinate range check: {coord_check}")
                    lat_candidates.append({
                        'column': original_col,
                        'priority': priority,
                        'numeric_ratio': numeric_ratio,
                        'coord_range_check': coord_check
                    })
                break
        
        for priority, pattern in enumerate(lon_patterns):
            if re.search(pattern, col_lower, re.IGNORECASE):
                print(f"   Longitude pattern bulundu: '{pattern}' -> '{original_col}'")
                numeric_ratio = check_numeric_ratio(df[original_col])
                print(f"   Numeric ratio: {numeric_ratio}")
                if numeric_ratio > 0.3:
                    coord_check = check_coordinate_range(df[original_col], 'lon')
                    print(f"   Coordinate range check: {coord_check}")
                    lon_candidates.append({
                        'column': original_col,
                        'priority': priority,
                        'numeric_ratio': numeric_ratio,
                        'coord_range_check': coord_check
                    })
                break
    
    print(f"Latitude adayları: {[c['column'] for c in lat_candidates]}")
    print(f"Longitude adayları: {[c['column'] for c in lon_candidates]}")
    
    best_lat = select_best_candidate(lat_candidates)
    best_lon = select_best_candidate(lon_candidates)
    
    if best_lat and best_lon:
        confidence = calculate_confidence(best_lat, best_lon)
        print(f"Tespit başarılı: lat='{best_lat['column']}', lon='{best_lon['column']}', confidence={confidence}")
        return {
            'found': True,
            'columns': {'lat': best_lat['column'], 'lon': best_lon['column']},
            'confidence': confidence,
            'suggestions': []
        }
    
    suggestions = generate_suggestions(df, lat_candidates, lon_candidates)
    print(f"Tespit başarısız. Öneriler: {suggestions}")
    
    return {
        'found': False,
        'columns': None,
        'confidence': 0,
        'suggestions': suggestions
    }

def check_numeric_ratio(series):
    """Sütunun ne kadarının sayısal olduğunu kontrol et - derece sembolleri dahil"""
    try:
        cleaned_values = []
        for val in series:
            cleaned = clean_coordinate_value(val)
            cleaned_values.append(cleaned)
        
        # None olmayan değerlerin oranını hesapla
        non_none = sum(1 for v in cleaned_values if v is not None)
        ratio = non_none / len(series) if len(series) > 0 else 0
        
        return ratio
    except Exception as e:
        print(f"check_numeric_ratio error: {e}")
        return 0

def check_coordinate_range(series, coord_type):
    """Koordinat değer aralığını kontrol et - derece sembolleri dahil"""
    try:
        # Değerleri temizle ve float'a çevir
        cleaned_values = []
        for val in series:
            cleaned = clean_coordinate_value(val)
            if cleaned is not None:
                cleaned_values.append(cleaned)
        
        if len(cleaned_values) == 0:
            return False
        
        min_val = min(cleaned_values)
        max_val = max(cleaned_values)
        
        if coord_type == 'lat':
            # Enlem için geçerli aralık: -90 ile 90
            result = -95 <= min_val <= 95 and -95 <= max_val <= 95
        else:
            # Boylam için geçerli aralık: -180 ile 180
            result = -185 <= min_val <= 185 and -185 <= max_val <= 185
        
        print(f"Range check for {coord_type}: min={min_val}, max={max_val}, valid={result}")
        return result
    except Exception as e:
        print(f"check_coordinate_range error: {e}")
        return False

def select_best_candidate(candidates):
    """En iyi koordinat sütununu seç"""
    if not candidates:
        return None
    
    candidates.sort(key=lambda x: (
        x['priority'],
        not x['coord_range_check'],
        -x['numeric_ratio']
    ))
    
    return candidates[0]

def calculate_confidence(lat_candidate, lon_candidate):
    """Tespit güvenilirlik skorunu hesapla"""
    score = 0
    score += max(0, 20 - lat_candidate['priority'] * 5)
    score += max(0, 20 - lon_candidate['priority'] * 5)
    score += lat_candidate['numeric_ratio'] * 15
    score += lon_candidate['numeric_ratio'] * 15
    if lat_candidate['coord_range_check']:
        score += 15
    if lon_candidate['coord_range_check']:
        score += 15
    return min(100, score)

def generate_suggestions(df, lat_candidates, lon_candidates):
    """Koordinat sütunu önerileri oluştur"""
    suggestions = []
    all_columns = list(df.columns)
    suggestions.append(f"Toplam {len(all_columns)} sütun: {', '.join(all_columns)}")
    numeric_columns = []
    for col in df.columns:
        ratio = check_numeric_ratio(df[col])
        if ratio > 0.2:
            numeric_columns.append(f"{col} ({ratio:.1%})")
    
    if numeric_columns:
        suggestions.append(f"Sayısal sütunlar: {', '.join(numeric_columns)}")
    else:
        suggestions.append("Hiç sayısal sütun bulunamadı!")
    
    if lat_candidates:
        lat_names = [f"{c['column']} (öncelik:{c['priority']}, sayısal:{c['numeric_ratio']:.1%})" for c in lat_candidates[:3]]
        suggestions.append(f"Enlem adayları: {', '.join(lat_names)}")
    else:
        suggestions.append("Enlem için aday sütun bulunamadı. 'lat', 'latitude', 'enlem', 'y' gibi isimler arıyoruz.")
    
    if lon_candidates:
        lon_names = [f"{c['column']} (öncelik:{c['priority']}, sayısal:{c['numeric_ratio']:.1%})" for c in lon_candidates[:3]]
        suggestions.append(f"Boylam adayları: {', '.join(lon_names)}")
    else:
        suggestions.append("Boylam için aday sütun bulunamadı. 'lon', 'longitude', 'boylam', 'x' gibi isimler arıyoruz.")
    
    try:
        # NaN değerlerini temizle
        clean_df = df.head(2).where(pd.notna(df.head(2)), None)
        sample_data = clean_df.to_dict('records')
        suggestions.append(f"Örnek veri: {sample_data}")
    except:
        pass
    
    return suggestions

def convert_to_geojson(df, coord_columns):
    """DataFrame'i GeoJSON'a çevir - derece sembolleri dahil"""
    features = []
    lat_col = coord_columns['lat']
    lon_col = coord_columns['lon']

    print(f"GeoJSON'a çeviriliyor: lat='{lat_col}', lon='{lon_col}'")

    for idx, row in df.iterrows():
        try:
            # Koordinat değerlerini temizle
            lat = clean_coordinate_value(row[lat_col])
            lon = clean_coordinate_value(row[lon_col])

            # Geçersiz koordinatları atla
            if lat is None or lon is None:
                print(f"Satır atlandı (idx={idx}): lat veya lon değeri None")
                continue

            # Koordinat aralığı kontrolü
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                print(f"Geçersiz koordinat atlandı: lat={lat}, lon={lon}")
                continue

            # Properties oluştur
            properties = {}
            for col in df.columns:
                if col not in [lat_col, lon_col]:
                    value = row[col]
                    # NaN veya None değerleri None olarak kaydet
                    if pd.notna(value):
                        # Eğer değer string ve derece sembolü içeriyorsa, temizle
                        if isinstance(value, str) and '°' in value:
                            properties[col] = value.replace('°', '').strip()
                        else:
                            properties[col] = value
                    else:
                        properties[col] = None

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)]
                },
                "properties": properties
            }
            features.append(feature)
        except (ValueError, TypeError) as e:
            print(f"Satır atlandı (idx={idx}): {e}")
            continue

    print(f"Toplam {len(features)} feature oluşturuldu")

    return {
        "type": "FeatureCollection",
        "features": features
    }

# === SPATIAL RESOURCE RELATIONSHIPS ENDPOINTS ===

@spatial_api.route('/api/spatial-resources/<resource_id>/relationships')
def get_resource_relationships(resource_id):
    """Get all related resources for a spatial resource"""
    # Public endpoint - herkes ilişkili verileri görüntüleyebilir

    try:
        # Resource'un var olup olmadığını ve spatial olup olmadığını kontrol et
        resource_check = model.Session.execute(
            text("""
                SELECT r.id, r.name, sr.is_spatial
                FROM resource r
                LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
                WHERE r.id = :resource_id AND r.state = 'active'
            """),
            {'resource_id': resource_id}
        ).fetchone()

        if not resource_check:
            return jsonify({'error': 'Resource not found'}), 404

        if not resource_check.is_spatial:
            return jsonify({'error': 'Resource is not marked as spatial'}), 400

        # İlişkili kaynakları getir
        query = text("""
            SELECT
                srr.id as relationship_id,
                srr.related_resource_id,
                r.name as related_resource_name,
                r.format as related_resource_format,
                r.url as related_resource_url,
                p.id as package_id,
                p.name as package_name,
                p.title as package_title,
                srr.created_date,
                srr.created_by
            FROM spatial_resource_relationships srr
            JOIN resource r ON srr.related_resource_id = r.id
            JOIN package p ON r.package_id = p.id
            WHERE srr.spatial_resource_id = :resource_id
            AND r.state = 'active'
            AND p.state = 'active'
            ORDER BY srr.created_date DESC
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchall()

        # Base URL
        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')

        relationships = []
        for row in result:
            # URL'yi mutlak hale getir
            absolute_url = get_absolute_url(site_url, row.related_resource_url, row.package_name, row.related_resource_id)

            relationships.append({
                'relationship_id': row.relationship_id,
                'related_resource_id': row.related_resource_id,
                'related_resource_name': row.related_resource_name or 'İsimsiz Kaynak',
                'related_resource_format': row.related_resource_format,
                'related_resource_url': absolute_url,
                'package_id': row.package_id,
                'package_name': row.package_name,
                'package_title': row.package_title,
                'created_date': row.created_date.isoformat() if row.created_date else None,
                'created_by': row.created_by
            })

        return jsonify({
            'success': True,
            'spatial_resource_id': resource_id,
            'spatial_resource_name': resource_check.name,
            'count': len(relationships),
            'relationships': relationships
        })

    except Exception as e:
        print(f"Get relationships error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/relationships', methods=['POST'])
def add_resource_relationship(resource_id):
    """Add a relationship between a spatial resource and another resource"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    related_resource_id = data.get('related_resource_id')

    if not related_resource_id:
        return jsonify({'error': 'related_resource_id is required'}), 400

    try:
        # Spatial resource'un var olup olmadığını kontrol et
        spatial_check = model.Session.execute(
            text("""
                SELECT r.id, sr.is_spatial
                FROM resource r
                LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
                WHERE r.id = :resource_id AND r.state = 'active'
            """),
            {'resource_id': resource_id}
        ).fetchone()

        if not spatial_check:
            return jsonify({'error': 'Spatial resource not found'}), 404

        if not spatial_check.is_spatial:
            return jsonify({'error': 'Resource is not marked as spatial'}), 400

        # Related resource'un var olup olmadığını kontrol et
        related_check = model.Session.execute(
            text("SELECT id FROM resource WHERE id = :resource_id AND state = 'active'"),
            {'resource_id': related_resource_id}
        ).fetchone()

        if not related_check:
            return jsonify({'error': 'Related resource not found'}), 404

        # Kendine referans kontrolü
        if resource_id == related_resource_id:
            return jsonify({'error': 'A resource cannot be related to itself'}), 400

        # İlişkinin zaten var olup olmadığını kontrol et
        existing = model.Session.execute(
            text("""
                SELECT id FROM spatial_resource_relationships
                WHERE spatial_resource_id = :spatial_id AND related_resource_id = :related_id
            """),
            {'spatial_id': resource_id, 'related_id': related_resource_id}
        ).fetchone()

        if existing:
            return jsonify({'error': 'Relationship already exists'}), 409

        # İlişkiyi oluştur
        model.Session.execute(
            text("""
                INSERT INTO spatial_resource_relationships
                (spatial_resource_id, related_resource_id, created_by)
                VALUES (:spatial_id, :related_id, :created_by)
            """),
            {
                'spatial_id': resource_id,
                'related_id': related_resource_id,
                'created_by': user.name
            }
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'spatial_resource_id': resource_id,
            'related_resource_id': related_resource_id,
            'created_by': user.name,
            'message': 'Relationship created successfully'
        }), 201

    except Exception as e:
        model.Session.rollback()
        print(f"Add relationship error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/relationships/<related_id>', methods=['DELETE'])
def delete_resource_relationship(resource_id, related_id):
    """Remove a relationship between a spatial resource and a related resource"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        # İlişkinin var olup olmadığını kontrol et
        existing = model.Session.execute(
            text("""
                SELECT id FROM spatial_resource_relationships
                WHERE spatial_resource_id = :spatial_id AND related_resource_id = :related_id
            """),
            {'spatial_id': resource_id, 'related_id': related_id}
        ).fetchone()

        if not existing:
            return jsonify({'error': 'Relationship not found'}), 404

        # İlişkiyi sil
        model.Session.execute(
            text("""
                DELETE FROM spatial_resource_relationships
                WHERE spatial_resource_id = :spatial_id AND related_resource_id = :related_id
            """),
            {'spatial_id': resource_id, 'related_id': related_id}
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'spatial_resource_id': resource_id,
            'related_resource_id': related_id,
            'deleted_by': user.name,
            'message': 'Relationship deleted successfully'
        })

    except Exception as e:
        model.Session.rollback()
        print(f"Delete relationship error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# === METADATA MAPPINGS ENDPOINTS ===

@spatial_api.route('/api/spatial-resources/<resource_id>/metadata-fields')
def get_resource_metadata_fields(resource_id):
    """Get all metadata fields for a spatial resource by fetching sample data"""
    try:
        # Admin kontrolü
        if not authz.is_sysadmin(toolkit.c.userobj.name if toolkit.c.userobj else None):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        # Resource'un var olup olmadığını ve spatial olup olmadığını kontrol et
        query = text("""
            SELECT r.url, r.format, sr.is_spatial, p.name as package_name
            FROM resource r
            JOIN package p ON r.package_id = p.id
            LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND r.state = 'active'
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result:
            return jsonify({'error': 'Resource not found'}), 404

        if not result.is_spatial:
            return jsonify({'error': 'Resource is not marked as spatial'}), 400

        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url, result.package_name, resource_id)
        format_type = (result.format or '').lower()

        # Edge case: Fix known 404 URLs
        url_fixes = {
            'd5662ab6-60b4-4bc8-8f5c-3d840391e73e': 'https://dataizmir.izka.org.tr/dataset/ede74092-347f-4fa3-ac5b-d158fee0e1a5/resource/d5662ab6-60b4-4bc8-8f5c-3d840391e73e/download/egms_aepnd_v2022.0.xlsx',
            '84dac4aa-a10d-4ed6-afad-915fc8585edb': 'https://dataizmir.izka.org.tr/dataset/ede74092-347f-4fa3-ac5b-d158fee0e1a5/resource/84dac4aa-a10d-4ed6-afad-915fc8585edb/download/egms_aepnd_v2023.0.csv'
        }

        if resource_id in url_fixes:
            print(f"Using edge case URL fix for resource: {resource_id}")
            url = url_fixes[resource_id]

        # Veri tipine göre örnek veriyi çek
        fields = []
        sample_data = None

        if format_type == 'geojson':
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            geojson_data = response.json()

            if geojson_data.get('type') == 'FeatureCollection' and geojson_data.get('features'):
                # İlk feature'dan property'leri al
                first_feature = geojson_data['features'][0]
                if 'properties' in first_feature:
                    fields = list(first_feature['properties'].keys())
                    sample_data = first_feature['properties']

        elif format_type in ['csv', 'xls', 'xlsx']:
            if format_type == 'csv':
                response = requests.get(url, timeout=30, verify=False)
                response.raise_for_status()

                # Delimiter auto-detection (öncelik noktalı virgülde)
                df = None
                delimiters = [';', ',', '\t', '|']
                encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1254']

                for delimiter in delimiters:
                    for encoding in encodings:
                        try:
                            df = pd.read_csv(io.StringIO(response.text), delimiter=delimiter, encoding=encoding, nrows=5)
                            if len(df.columns) > 1:
                                print(f"Metadata fields CSV parsed: delimiter='{delimiter}', encoding='{encoding}', columns={len(df.columns)}")
                                break
                        except:
                            continue
                    if df is not None and len(df.columns) > 1:
                        break

                if df is None or len(df.columns) <= 1:
                    # Fallback to default
                    df = pd.read_csv(io.StringIO(response.text), nrows=5)
            else:
                response = requests.get(url, timeout=30, verify=False)
                response.raise_for_status()
                df = pd.read_excel(io.BytesIO(response.content), nrows=5)

            # NaN değerlerini None'a çevir (JSON için)
            df = df.where(pd.notna(df), None)

            fields = list(df.columns)
            # CSV/Excel için her column'u ayrı ayrı listelemek için
            sample_data = {}
            if len(df) > 0:
                for col in df.columns:
                    value = df.iloc[0][col]
                    # NaN, None veya inf değerlerini kontrol et
                    if pd.isna(value):
                        sample_data[col] = None
                    elif isinstance(value, (int, float)):
                        if math.isinf(value) or math.isnan(value):
                            sample_data[col] = None
                        else:
                            sample_data[col] = value
                    else:
                        sample_data[col] = value

        elif format_type in ['json', 'api', 'rest']:
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            json_data = response.json()

            # API response'unu parse et
            if isinstance(json_data, dict):
                for key in ['data', 'results', 'items', 'onemliyer', 'records']:
                    if key in json_data and isinstance(json_data[key], list) and len(json_data[key]) > 0:
                        json_data = json_data[key]
                        break

            if isinstance(json_data, list) and len(json_data) > 0:
                first_item = json_data[0]
                if isinstance(first_item, dict):
                    fields = list(first_item.keys())
                    sample_data = first_item

        # Tüm format tiplerinde NumPy tiplerini Python native tiplerine çevir
        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'format': format_type,
            'fields': convert_numpy_types(fields),
            'sample_data': convert_numpy_types(sample_data),
            'total_fields': len(fields)
        })

    except Exception as e:
        print(f"Get metadata fields error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/metadata-mappings')
def get_metadata_mappings(resource_id):
    """Get metadata mappings for a spatial resource"""
    # Admin kontrolü kaldırıldı

    try:
        query = text("""
            SELECT field_mappings, hidden_fields, visibility_mode, visible_fields,
                   default_fields, created_by, created_date, updated_by, updated_date
            FROM spatial_metadata_mappings
            WHERE resource_id = :resource_id
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()

        if not result:
            # Henüz mapping yok, boş döndür
            return jsonify({
                'success': True,
                'resource_id': resource_id,
                'field_mappings': {},
                'default_fields': [],
                'hidden_fields': [],
                'visibility_mode': 'show_all',
                'visible_fields': [],
                'exists': False
            })

        # JSON string'i parse et
        field_mappings = result.field_mappings
        if isinstance(field_mappings, str):
            try:
                field_mappings = json.loads(field_mappings)
            except:
                field_mappings = {}

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'field_mappings': field_mappings,
            'hidden_fields': result.hidden_fields or [],
            'visibility_mode': result.visibility_mode or 'show_all',
G            'visible_fields': result.visible_fields or [],
            'default_fields': result.default_fields or [],
            'created_by': result.created_by,
            'created_date': result.created_date.isoformat() if result.created_date else None,
            'updated_by': result.updated_by,
            'updated_date': result.updated_date.isoformat() if result.updated_date else None,
            'exists': True
        })

    except Exception as e:
        print(f"Get metadata mappings error: {str(e)}")
        return jsonify({'error': str(e)}), 500
@spatial_api.route('/api/spatial-resources/<resource_id>/metadata-mappings', methods=['POST'])
def save_metadata_mappings(resource_id):
    """Save or update metadata mappings for a spatial resource"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    field_mappings = data.get('field_mappings', {})
    hidden_fields = data.get('hidden_fields', [])
    visibility_mode = data.get('visibility_mode', 'show_all')
    visible_fields = data.get('visible_fields', [])
    default_fields = data.get('default_fields', [])

    try:
        # Resource'un var olup olmadığını ve spatial olup olmadığını kontrol et
        resource_check = model.Session.execute(
            text("""
                SELECT r.id, sr.is_spatial
                FROM resource r
                LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
                WHERE r.id = :resource_id AND r.state = 'active'
            """),
            {'resource_id': resource_id}
        ).fetchone()

        if not resource_check:
            return jsonify({'error': 'Resource not found'}), 404

        if not resource_check.is_spatial:
            return jsonify({'error': 'Resource is not marked as spatial'}), 400

        # Mevcut mapping var mı kontrol et
        existing = model.Session.execute(
            text("SELECT id FROM spatial_metadata_mappings WHERE resource_id = :resource_id"),
            {'resource_id': resource_id}
        ).fetchone()

        if existing:
            # Güncelle (default_fields güncelleme yapılırsa güncelle, yoksa dokunma)
            update_params = {
                'resource_id': resource_id,
                'field_mappings': json.dumps(field_mappings),
                'hidden_fields': hidden_fields,
                'visibility_mode': visibility_mode,
                'visible_fields': visible_fields,
                'updated_by': user.name
            }

            # Eğer default_fields gönderildiyse güncelle
            if default_fields:
                update_params['default_fields'] = default_fields
                model.Session.execute(
                    text("""
                        UPDATE spatial_metadata_mappings
                        SET field_mappings = :field_mappings,
                            hidden_fields = :hidden_fields,
                            visibility_mode = :visibility_mode,
                            visible_fields = :visible_fields,
                            default_fields = :default_fields,
                            updated_by = :updated_by,
                            updated_date = CURRENT_TIMESTAMP
                        WHERE resource_id = :resource_id
                    """),
                    update_params
                )
            else:
                model.Session.execute(
                    text("""
                        UPDATE spatial_metadata_mappings
                        SET field_mappings = :field_mappings,
                            hidden_fields = :hidden_fields,
                            visibility_mode = :visibility_mode,
                            visible_fields = :visible_fields,
                            updated_by = :updated_by,
                            updated_date = CURRENT_TIMESTAMP
                        WHERE resource_id = :resource_id
                    """),
                    update_params
                )
        else:
            # Oluştur
            model.Session.execute(
                text("""
                    INSERT INTO spatial_metadata_mappings
                    (resource_id, field_mappings, hidden_fields, visibility_mode, visible_fields, default_fields, created_by, updated_by)
                    VALUES (:resource_id, :field_mappings, :hidden_fields, :visibility_mode, :visible_fields, :default_fields, :created_by, :updated_by)
                """),
                {
                    'resource_id': resource_id,
                    'field_mappings': json.dumps(field_mappings),
                    'hidden_fields': hidden_fields,
                    'visibility_mode': visibility_mode,
                    'visible_fields': visible_fields,
                    'default_fields': default_fields,
                    'created_by': user.name,
                    'updated_by': user.name
                }
            )

        model.Session.commit()

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'field_mappings': field_mappings,
            'hidden_fields': hidden_fields,
            'visibility_mode': visibility_mode,
            'visible_fields': visible_fields,
            'saved_by': user.name,
            'message': 'Metadata mappings saved successfully'
        })

    except Exception as e:
        model.Session.rollback()
        print(f"Save metadata mappings error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# === SPATIAL LEGENDS ENDPOINTS ===

@spatial_api.route('/api/spatial-resources/<resource_id>/legends')
def get_resource_legends(resource_id):
    """Get all legend entries for a spatial resource"""
    # Admin kontrolü kaldırıldı

    try:
        query = text("""
            SELECT id, resource_id, color, description, order_index,
                   created_by, created_date, updated_by, updated_date
            FROM spatial_legends
            WHERE resource_id = :resource_id
            ORDER BY order_index ASC, created_date ASC
        """)

        result = model.Session.execute(query, {'resource_id': resource_id}).fetchall()

        legends = []
        for row in result:
            legends.append({
                'id': row.id,
                'resource_id': row.resource_id,
                'color': row.color,
                'description': row.description,
                'order_index': row.order_index,
                'created_by': row.created_by,
                'created_date': row.created_date.isoformat() if row.created_date else None,
                'updated_by': row.updated_by,
                'updated_date': row.updated_date.isoformat() if row.updated_date else None
            })

        return jsonify({
            'success': True,
            'resource_id': resource_id,
            'count': len(legends),
            'legends': legends
        })

    except Exception as e:
        print(f"Get legends error: {str(e)}")
        return jsonify({'error': str(e)}), 500
@spatial_api.route('/api/spatial-resources/<resource_id>/legends', methods=['POST'])
def add_legend_entry(resource_id):
    """Add a new legend entry for a spatial resource"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    color = data.get('color')
    description = data.get('description')
    order_index = data.get('order_index', 0)

    if not color or not description:
        return jsonify({'error': 'color ve description gerekli'}), 400

    # Hex color validation
    if not color.startswith('#') or len(color) != 7:
        return jsonify({'error': 'Geçersiz renk formatı. #RRGGBB formatında olmalı'}), 400

    try:
        # Resource'un var olup olmadığını ve spatial olup olmadığını kontrol et
        resource_check = model.Session.execute(
            text("""
                SELECT r.id, sr.is_spatial
                FROM resource r
                LEFT JOIN spatial_resources sr ON r.id = sr.resource_id
                WHERE r.id = :resource_id AND r.state = 'active'
            """),
            {'resource_id': resource_id}
        ).fetchone()

        if not resource_check:
            return jsonify({'error': 'Resource not found'}), 404

        if not resource_check.is_spatial:
            return jsonify({'error': 'Resource is not marked as spatial'}), 400

        # Legend entry'yi oluştur
        result = model.Session.execute(
            text("""
                INSERT INTO spatial_legends
                (resource_id, color, description, order_index, created_by, updated_by)
                VALUES (:resource_id, :color, :description, :order_index, :created_by, :updated_by)
                RETURNING id
            """),
            {
                'resource_id': resource_id,
                'color': color,
                'description': description,
                'order_index': order_index,
                'created_by': user.name,
                'updated_by': user.name
            }
        )

        legend_id = result.fetchone()[0]
        model.Session.commit()

        return jsonify({
            'success': True,
            'legend_id': legend_id,
            'resource_id': resource_id,
            'color': color,
            'description': description,
            'order_index': order_index,
            'created_by': user.name,
            'message': 'Legend entry created successfully'
        }), 201

    except Exception as e:
        model.Session.rollback()
        print(f"Add legend error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/legends/<int:legend_id>', methods=['PUT'])
def update_legend_entry(resource_id, legend_id):
    """Update a legend entry"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    color = data.get('color')
    description = data.get('description')
    order_index = data.get('order_index')

    if not color or not description:
        return jsonify({'error': 'color ve description gerekli'}), 400

    # Hex color validation
    if not color.startswith('#') or len(color) != 7:
        return jsonify({'error': 'Geçersiz renk formatı. #RRGGBB formatında olmalı'}), 400

    try:
        # Legend entry'nin var olup olmadığını kontrol et
        existing = model.Session.execute(
            text("SELECT id FROM spatial_legends WHERE id = :legend_id AND resource_id = :resource_id"),
            {'legend_id': legend_id, 'resource_id': resource_id}
        ).fetchone()

        if not existing:
            return jsonify({'error': 'Legend entry not found'}), 404

        # Güncelle
        model.Session.execute(
            text("""
                UPDATE spatial_legends
                SET color = :color,
                    description = :description,
                    order_index = :order_index,
                    updated_by = :updated_by,
                    updated_date = CURRENT_TIMESTAMP
                WHERE id = :legend_id AND resource_id = :resource_id
            """),
            {
                'legend_id': legend_id,
                'resource_id': resource_id,
                'color': color,
                'description': description,
                'order_index': order_index if order_index is not None else 0,
                'updated_by': user.name
            }
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'legend_id': legend_id,
            'resource_id': resource_id,
            'color': color,
            'description': description,
            'updated_by': user.name,
            'message': 'Legend entry updated successfully'
        })

    except Exception as e:
        model.Session.rollback()
        print(f"Update legend error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/legends/<int:legend_id>', methods=['DELETE'])
def delete_legend_entry(resource_id, legend_id):
    """Delete a legend entry"""
    try:
        # Admin kontrolü
        user = toolkit.c.userobj
        if not user or not authz.is_sysadmin(user.name):
            return jsonify({'error': 'Unauthorized'}), 403
    except:
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        # Legend entry'nin var olup olmadığını kontrol et
        existing = model.Session.execute(
            text("SELECT id FROM spatial_legends WHERE id = :legend_id AND resource_id = :resource_id"),
            {'legend_id': legend_id, 'resource_id': resource_id}
        ).fetchone()

        if not existing:
            return jsonify({'error': 'Legend entry not found'}), 404

        # Sil
        model.Session.execute(
            text("DELETE FROM spatial_legends WHERE id = :legend_id AND resource_id = :resource_id"),
            {'legend_id': legend_id, 'resource_id': resource_id}
        )

        model.Session.commit()

        return jsonify({
            'success': True,
            'legend_id': legend_id,
            'resource_id': resource_id,
            'deleted_by': user.name,
            'message': 'Legend entry deleted successfully'
        })

    except Exception as e:
        model.Session.rollback()
        print(f"Delete legend error: {str(e)}")
        return jsonify({'error': str(e)}), 500