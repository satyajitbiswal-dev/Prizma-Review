# reviews/urls.py
from django.urls import path
from reviews import views

urlpatterns = [
    path('dashboard/', views.dashboard_home, name='dashboard_home'),
    path('dashboard/repo/<uuid:repo_id>/', views.repo_detail, name='repo_detail'),
    path('dashboard/pr/<uuid:pr_id>/status/', views.pr_review_status_element, name='pr_status_element'),
    path('dashboard/review/<uuid:review_id>/', views.review_detail, name='review_detail'),

]