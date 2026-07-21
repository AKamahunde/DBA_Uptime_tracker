from django.urls import path
from . import views

urlpatterns = [
    path('replication/', views.ingest_replication_status, name='ingest_replication_status'),
]
