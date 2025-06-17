# ckan/model/theme.py
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Table
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

class ThemeCategory(DomainObject):
    def __init__(self, slug=None, name=None, description=None, color='#333333', icon=None):
        self.slug = slug
        self.name = name  
        self.description = description
        self.color = color
        self.icon = icon

class DatasetThemeAssignment(DomainObject):
    def __init__(self, dataset_id=None, theme_slug=None, assigned_by=None):
        self.dataset_id = dataset_id
        self.theme_slug = theme_slug
        self.assigned_by = assigned_by

# ORM mapping
mapper(ThemeCategory, theme_category_table)
mapper(DatasetThemeAssignment, dataset_theme_table)