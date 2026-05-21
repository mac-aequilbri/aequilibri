from django.urls import path
from . import views

app_name = 'uc2'

urlpatterns = [
    path('',                    views.dashboard,         name='dashboard'),
    path('chat/',               views.chat,              name='chat'),
    path('chat/send/',          views.chat_send,         name='chat_send'),
    path('chat/confirm/',       views.chat_confirm,      name='chat_confirm'),
    path('chat/reset/',         views.chat_reset,        name='chat_reset'),
    path('actions/',            views.action_hub,        name='action_hub'),
    path('budget/',             views.budget_view,       name='budget'),
    path('phases/',             views.phases_view,       name='phases'),
    path('decisions/',          views.decisions_view,    name='decisions'),
    path('vendors/',            views.vendors_view,      name='vendors'),
    path('learning-rules/',     views.learning_rules_view, name='learning_rules'),
    path('learning-rules/<int:pk>/promote/', views.hypothesis_promote, name='hypothesis_promote'),
    path('change-log/',         views.change_log_view,   name='change_log'),
    path('procurement/',        views.procurement_view,  name='procurement'),
]
