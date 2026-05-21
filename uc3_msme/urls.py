from django.urls import path
from . import views

app_name = 'uc3'

urlpatterns = [
    path('',                              views.dashboard,      name='dashboard'),
    path('tenant/<int:tenant_id>/',       views.select_tenant,  name='select_tenant'),
    path('projects/',                     views.project_list,   name='project_list'),
    path('projects/new/',                 views.project_create, name='project_create'),
    path('projects/<int:pk>/',            views.project_detail, name='project_detail'),
    path('actions/',                      views.action_list,    name='action_list'),
    path('risks/',                        views.risk_list,      name='risk_list'),
    path('budget/',                       views.budget_view,    name='budget'),
    path('ai-chat/',                      views.ai_chat,        name='ai_chat'),
    path('exec-log/',                     views.exec_log,       name='exec_log'),
]
