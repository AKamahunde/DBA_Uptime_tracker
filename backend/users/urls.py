from django.urls import path
from .views import RegisterView, login_view, user_profile

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', login_view, name='login'),
    path('profile/', user_profile, name='profile'),
]
