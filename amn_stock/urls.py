from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from stock import views as stock_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # ── PWA — Service Worker (scope racine obligatoire) + page offline ──
    path('sw.js',    stock_views.service_worker, name='service_worker'),
    path('offline/', stock_views.offline_page,   name='offline_page'),


    # ── Connexion sécurisée (anti brute-force) ──
    path('login/', stock_views.secure_login, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # ── Inscription + vérification email ──
    path('register/',        stock_views.register,             name='register'),
    path('verify-email/',    stock_views.verify_email,         name='verify_email'),
    path('resend-code/',     stock_views.resend_verification,  name='resend_verification'),

    # ── Mot de passe oublié ──
    path('forgot-password/',  stock_views.forgot_password,   name='forgot_password'),
    path('reset-verify/',     stock_views.reset_verify,       name='reset_verify'),
    path('resend-reset/',     stock_views.resend_reset_code,  name='resend_reset_code'),
    path('reset-password/',   stock_views.reset_password,     name='reset_password'),

    path('', include('stock.urls')),
]
