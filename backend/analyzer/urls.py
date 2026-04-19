from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AnalysisSessionViewSet

router = DefaultRouter()
router.register(r'sessions', AnalysisSessionViewSet, basename='session')

urlpatterns = [
    path('', include(router.urls)),
]