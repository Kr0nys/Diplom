from django.contrib import admin
from .models import AnalysisSession, TestGenerationTask, UploadedFile

@admin.register(AnalysisSession)
class AnalysisSessionAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'status', 'created_at']
    list_filter = ['status', 'created_at']

@admin.register(TestGenerationTask)
class TestGenerationTaskAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'status', 'created_at']
    list_filter = ['status', 'created_at']

@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ['original_name', 'session', 'uploaded_at']
    list_filter = ['uploaded_at']