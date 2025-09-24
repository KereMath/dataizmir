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

def get_absolute_url(base_url, resource_url):
    """Resource URL'ini mutlak URL'ye çevirir. URL zaten tam ise olduğu gibi döndürür."""
    if not resource_url:
        return None
    
    # Eğer zaten tam URL ise, olduğu gibi döndür
    if resource_url.startswith(('http://', 'https://')):
        return resource_url
    
    # Eğer göreceli bir URL ise, base URL ile birleştir
    if base_url:
        return urljoin(base_url, resource_url)
    
    # Eğer sadece dosya adı ise (önceki kontrol başarısız olduysa), CKAN storage URL'i ile birleştir
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
        absolute_url = get_absolute_url(site_url, row.url)
        
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
                sr.updated_date
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
            absolute_url = get_absolute_url(site_url, row.url)
            
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
                'added_by': row.added_by,
                'updated_date': row.updated_date.isoformat() if row.updated_date else None
            })
        
        return jsonify({
            'success': True,
            'count': len(resources),
            'spatial_resources': resources
        })
        
    except Exception as e:
        print(f"Spatial resource list error: {str(e)}")
        return jsonify({
            'success': False, 
            'error': f'Spatial resource listesi alınırken hata: {str(e)}',
            'spatial_resources': []
        }), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/data')
def get_spatial_data(resource_id):
    """Resource'un spatial verisini parse ederek harita için hazırlar"""
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
            return jsonify({'error': 'Spatial resource bulunamadı'}), 404
        
        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url)
        
        if not url:
            return jsonify({'error': 'Geçersiz resource URL'}), 400
        
        format_type = (result.format or '').lower()
        
        print(f"Processing spatial data: {url} (format: {format_type})")
        
        # WMS, WFS ve GeoTIFF için sadece URL'i döndürerek iş yükünü frontend'e bırak
        if format_type in ['wms', 'wfs']:
            return jsonify({
                'success': True,
                'type': format_type,
                'url': url,
                'message': f'{format_type.upper()} hizmeti doğrudan frontendde işlenecek'
            })
        
        elif format_type == 'geotiff':
            # GeoTIFF için özel durum - COG (Cloud Optimized GeoTIFF) URL'i döndür
            return jsonify({
                'success': True,
                'type': 'geotiff',
                'url': url,
                'message': 'GeoTIFF dosyası doğrudan frontendde işlenecek'
            })
        
        elif format_type in ['shp', 'zip']:
            proxy_url = f"{site_url}/dataset/{result.package_name}/resource/{resource_id}/download"
            return jsonify({
                'success': True,
                'type': 'shp',
                'url': proxy_url,
                'message': 'SHP dosyası doğrudan frontendde işlenecek'
            })
        
        elif format_type == 'geojson':
            return process_geojson(url)
        elif format_type in ['csv', 'xls', 'xlsx']:
            return process_tabular_data(url, format_type, resource_id)
        elif format_type in ['json', 'api', 'rest', 'soap']:
            return process_api_data(url, format_type, resource_id)
        elif format_type in ['kml', 'gpx'] and SPATIAL_SUPPORT:
            return process_spatial_files(url, format_type)
        else:
            return jsonify({
                'error': f'Desteklenmeyen format: {format_type}',
                'suggestion': 'Desteklenen formatlar: GeoJSON, CSV, Excel, JSON/API, SHP (CKAN GeoView), KML, WMS, WFS, GeoTIFF'
            }), 400
            
    except Exception as e:
        print(f"Spatial data processing error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@spatial_api.route('/api/spatial-resources/<resource_id>/columns')
def get_resource_columns(resource_id):
    """Resource'un sütunlarını döndür (manuel seçim için)"""
    try:
        query = text("""
            SELECT r.url, r.format
            FROM resource r
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND sr.is_spatial = true
        """)
        
        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()
        
        if not result:
            return jsonify({'error': 'Resource bulunamadı'}), 404
        
        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url)
        format_type = (result.format or '').lower()
        
        if format_type not in ['csv', 'xls', 'xlsx', 'json', 'api', 'rest']:
            return jsonify({'error': 'Sadece CSV/Excel/JSON/API dosyalar için sütun listesi'}), 400
        
        if format_type == 'csv':
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            try:
                df = pd.read_csv(io.StringIO(response.text), nrows=5, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(io.StringIO(response.text), nrows=5, encoding='latin-1')
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
            SELECT r.url, r.format
            FROM resource r
            JOIN spatial_resources sr ON r.id = sr.resource_id
            WHERE r.id = :resource_id AND sr.is_spatial = true
        """)
        
        result = model.Session.execute(query, {'resource_id': resource_id}).fetchone()
        
        if not result:
            return jsonify({'error': 'Resource bulunamadı'}), 404
        
        site_url = toolkit.config.get('ckan.site_url', 'http://localhost:5000')
        url = get_absolute_url(site_url, result.url)
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
        
        response = requests.get(url, timeout=30, verify=False, headers={
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'CKAN-API-Tester/1.0'
        })
        response.raise_for_status()
        
        json_data = response.json()
        
        print(f"JSON data tipi: {type(json_data)}")
        
        if isinstance(json_data, dict):
            if 'features' in json_data and json_data.get('type') == 'FeatureCollection':
                return jsonify({
                    'success': True,
                    'type': 'geojson_api',
                    'data': json_data
                })
            elif 'data' in json_data and isinstance(json_data['data'], list):
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
                else:
                    return jsonify({
                        'error': 'JSON verisi uygun formatta değil',
                        'available_fields': list(json_data.keys()),
                        'debug_info': 'No array field found in JSON'
                    }), 400
        
        if not isinstance(json_data, list):
            return jsonify({
                'error': 'JSON verisi array formatında değil',
                'data_type': str(type(json_data)),
                'debug_info': 'Expected list but got ' + str(type(json_data))
            }), 400
        
        if len(json_data) == 0:
            return jsonify({'error': 'JSON verisi boş'}), 400
        
        df = pd.DataFrame(json_data)
        coord_result = smart_detect_coordinate_columns(df)
        
        if not coord_result['found']:
            return jsonify({
                'success': False,
                'error': 'Koordinat sütunları otomatik tespit edilemedi',
                'columns': list(df.columns),
                'sample_data': df.head(3).to_dict('records'),
                'suggestions': coord_result['suggestions'],
            }), 400
        
        geojson_data = convert_to_geojson(df, coord_result['columns'])
        
        return jsonify({
            'success': True,
            'type': 'api_json',
            'data': geojson_data,
            'detected_columns': coord_result['columns'],
            'detection_confidence': coord_result['confidence']
        })
        
    except Exception as e:
        print(f"API data işleme hatası: {str(e)}")
        return jsonify({'error': f'API verisi işlenemedi: {str(e)}'}), 500

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
        delimiters = [',', ';', '\t', '|']
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1254']
        
        for delimiter in delimiters:
            for encoding in encodings:
                try:
                    df = pd.read_csv(io.StringIO(response.text), delimiter=delimiter, encoding=encoding)
                    if len(df.columns) > 1:
                        print(f"CSV başarıyla parse edildi: delimiter='{delimiter}', encoding='{encoding}', sütun sayısı={len(df.columns)}")
                        break
                except Exception as e:
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
        return jsonify({
            'success': False,
            'error': 'Koordinat sütunları otomatik tespit edilemedi',
            'columns': list(df.columns),
            'sample_data': df.head(3).to_dict('records'),
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
    """Sütunun ne kadarının sayısal olduğunu kontrol et"""
    try:
        if series.dtype == 'object':
            cleaned_series = series.astype(str).str.replace(',', '.').str.strip()
            numeric_series = pd.to_numeric(cleaned_series, errors='coerce')
        else:
            numeric_series = pd.to_numeric(series, errors='coerce')
        
        ratio = numeric_series.notna().sum() / len(series)
        return ratio
    except Exception as e:
        return 0

def check_coordinate_range(series, coord_type):
    """Koordinat değer aralığını kontrol et"""
    try:
        if series.dtype == 'object':
            cleaned_series = series.astype(str).str.replace(',', '.').str.strip()
            numeric_series = pd.to_numeric(cleaned_series, errors='coerce').dropna()
        else:
            numeric_series = pd.to_numeric(series, errors='coerce').dropna()
        
        if len(numeric_series) == 0:
            return False
        
        min_val = numeric_series.min()
        max_val = numeric_series.max()
        
        if coord_type == 'lat':
            result = -95 <= min_val <= 95 and -95 <= max_val <= 95
        else:
            result = -185 <= min_val <= 185 and -185 <= max_val <= 185
        
        return result
    except Exception as e:
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
        sample_data = df.head(2).to_dict('records')
        suggestions.append(f"Örnek veri: {sample_data}")
    except:
        pass
    
    return suggestions

def convert_to_geojson(df, coord_columns):
    """DataFrame'i GeoJSON'a çevir"""
    features = []
    lat_col = coord_columns['lat']
    lon_col = coord_columns['lon']
    
    print(f"GeoJSON'a çeviriliyor: lat='{lat_col}', lon='{lon_col}'")
    
    for idx, row in df.iterrows():
        try:
            lat_val = str(row[lat_col]).replace(',', '.').strip() if pd.notna(row[lat_col]) else None
            lon_val = str(row[lon_col]).replace(',', '.').strip() if pd.notna(row[lon_col]) else None
            
            if not lat_val or not lon_val or lat_val.lower() == 'nan' or lon_val.lower() == 'nan':
                continue
                
            lat = float(lat_val)
            lon = float(lon_val)
            
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                print(f"Geçersiz koordinat atlandı: lat={lat}, lon={lon}")
                continue
            
            properties = {}
            for col in df.columns:
                if col not in [lat_col, lon_col]:
                    value = row[col]
                    properties[col] = value if pd.notna(value) else None
            
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