"""æquilibri root URL configuration."""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.shortcuts import render


def home(request):
    return render(request, 'home.html')


urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('uc1/', include('uc1_roofing.urls', namespace='uc1')),
    path('uc2/', include('uc2_didi.urls', namespace='uc2')),
    path('uc3/', include('uc3_msme.urls', namespace='uc3')),
]
