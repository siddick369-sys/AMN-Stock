from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from stock import views as stock_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # ── Connexion sécurisée (anti brute-force) ──
    path('login/', stock_views.secure_login, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # ── Inscription + vérification email ──
    path('register/',        stock_views.register,             name='register'),
    path('verify-email/',    stock_views.verify_email,         name='verify_email'),
    path('resend-code/',     stock_views.resend_verification,  name='resend_verification'),

    path('', include('stock.urls')),
]
