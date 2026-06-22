from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AnalysisSessionViewSet, TestGenerationTaskViewSet

router = DefaultRouter()
router.register(r'sessions', AnalysisSessionViewSet, basename='session')
router.register(r'test-tasks', TestGenerationTaskViewSet, basename='test-task')

urlpatterns = [
    path('', include(router.urls)),
]