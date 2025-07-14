# -*- coding: utf-8 -*-
# ckan/model/theme.py
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Table, Numeric # Numeric eklendi
from sqlalchemy.orm import relationship
from ckan.model.meta import metadata, mapper, Session
from ckan.model.domain_object import DomainObject
import datetime

# Tema kategorileri tablosu
theme_category_table = Table(
    'theme_categories', metadata,
    Column('id', Integer, primary_key=True),
    Column('slug', String(100), unique=True, nullable=False),
    Column('name', String(200), nullable=False),
    Column('description', Text),
    Column('color', String(7), default='#333333'),
    Column('icon', String(100)),
    Column('background_image', Text),
    Column('opacity', Numeric(3, 2), default=1.0), # YENİ EKLENDİ
    Column('created_at', DateTime, default=datetime.datetime.utcnow)
)

# Dataset-tema ilişki tablosu  
dataset_theme_table = Table(
    'dataset_theme_assignments', metadata,
    Column('dataset_id', String(100), primary_key=True),
    Column('theme_slug', String(100), ForeignKey('theme_categories.slug'), nullable=False),
    Column('assigned_by', String(100)),
    Column('assigned_at', DateTime, default=datetime.datetime.utcnow)
)

# Kullanıcı-tema ilişki tablosu
user_theme_table = Table(
    'user_theme_assignments', metadata,
    Column('user_id', String(100), primary_key=True),
    Column('theme_slug', String(100), ForeignKey('theme_categories.slug'), primary_key=True),
    Column('role', String(50), nullable=False),
    Column('assigned_at', DateTime, default=datetime.datetime.utcnow),
    Column('assigned_by', String(100))
)

class ThemeCategory(DomainObject):
    def __init__(self, slug=None, name=None, description=None, color='#333333', icon=None, background_image=None, opacity=1.0): # opacity eklendi
        self.slug = slug
        self.name = name  
        self.description = description
        self.color = color
        self.icon = icon
        self.background_image = background_image
        self.opacity = opacity # opacity ataması eklendi

class DatasetThemeAssignment(DomainObject):
    def __init__(self, dataset_id=None, theme_slug=None, assigned_by=None):
        self.dataset_id = dataset_id
        self.theme_slug = theme_slug
        self.assigned_by = assigned_by

class UserThemeAssignment(DomainObject):
    def __init__(self, user_id=None, theme_slug=None, role='member', assigned_by=None):
        self.user_id = user_id
        self.theme_slug = theme_slug
        self.role = role
        self.assigned_by = assigned_by

# ORM mapping
mapper(ThemeCategory, theme_category_table)
mapper(DatasetThemeAssignment, dataset_theme_table)
mapper(UserThemeAssignment, user_theme_table)