"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.urls import include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Hooks up /auth/, /auth/login/, /auth/github/callback/, /auth/logout/
    path('auth/', include('accounts.urls')),
    
    path("api/webhooks/", include("webhooks.urls")),
    
    # Hooks up your dashboard URLs (/dashboard/, etc.)
    path('', include('reviews.urls')),
    
    # Catch-all rule: If an unauthenticated user hits http://127.0.0.1:8000/ directly, 
    # bounce them smoothly over to your secure authentication login wall view
    path('', RedirectView.as_view(url='/auth/', permanent=False)),
]
