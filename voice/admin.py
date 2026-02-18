from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import LovedOne


@admin.register(LovedOne)
class LovedOneAdmin(ModelAdmin):
    list_display = ('name', 'relationship')
    search_fields = ('name', 'relationship')
    list_filter = ('relationship',)
    list_filter_submit = True
    
