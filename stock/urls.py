from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),

    # Equipment API (JSON)
    path('api/equipment/create/', views.equipment_create, name='equipment_create'),
    path('api/equipment/<int:pk>/', views.equipment_detail, name='equipment_detail'),
    path('api/equipment/<int:pk>/update/', views.equipment_update, name='equipment_update'),
    path('api/equipment/<int:pk>/delete/', views.equipment_delete, name='equipment_delete'),

    # Chart data API
    path('api/stock-chart/', views.stock_chart_data, name='stock_chart_data'),

    # Notifications API
    path('api/notifications/', views.get_notifications, name='get_notifications'),
    path('api/notifications/<int:equipment_id>/dismiss/', views.dismiss_notification, name='dismiss_notification'),

    # Hub alert
    path('api/hub-alert/', views.send_hub_alert_view, name='send_hub_alert'),

    # Discharge pages
    path('discharges/', views.discharge_list, name='discharge_list'),
    path('discharges/create/', views.discharge_create, name='discharge_create'),
    path('discharges/<int:pk>/', views.discharge_detail, name='discharge_detail'),

    # Field reports
    path('discharges/<int:discharge_pk>/report/create/', views.field_report_create, name='field_report_create'),
    path('reports/', views.field_report_list, name='field_report_list'),
    path('reports/<int:pk>/', views.field_report_detail, name='field_report_detail'),

    # AI — rapport & PDF
    path('api/ai/analyse/', views.ai_trigger_summary, name='ai_trigger_summary'),
    path('api/ai/status/<str:cache_key>/', views.ai_report_status, name='ai_report_status'),
    path('api/ai/pdf/<str:cache_key>/', views.ai_generate_pdf, name='ai_generate_pdf'),

    # AI — suggestions
    path('api/ai/suggestions/', views.ai_trigger_suggestions, name='ai_trigger_suggestions'),
    path('api/ai/suggestions/status/<str:cache_key>/', views.ai_suggestions_status, name='ai_suggestions_status'),

    # Assistant vocal IA — décharge
    path('api/voice/discharge/', views.voice_process_discharge, name='voice_process_discharge'),
]
