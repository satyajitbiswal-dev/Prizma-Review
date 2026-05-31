# accounts/urls.py
from django.urls import path
from accounts import views

urlpatterns = [
    # Static view to serve up your templates/registration/login.html file
    path('', views.login_page, name='login_page'), 
    
    path('login/', views.github_login, name='github_login'),
    path('github/callback/', views.github_callback, name='github_callback'),
    path('logout/', views.logout, name='logout'),
]