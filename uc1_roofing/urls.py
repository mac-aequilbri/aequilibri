from django.urls import path
from . import api_views, views

app_name = 'uc1'

urlpatterns = [
    path('',                    views.dashboard,         name='dashboard'),
    path('roof-inspector/',     views.roof_inspector,    name='roof_inspector'),
    path('quotes/',             views.quote_list,        name='quote_list'),
    path('quotes/new/',         views.quote_create,      name='quote_create'),
    path('quotes/<int:pk>/',    views.quote_detail,      name='quote_detail'),
    path('quotes/<int:pk>/print/', views.quote_print,   name='quote_print'),
    path('rate-cards/',         views.rate_card_list,    name='rate_cards'),
    path('rate-cards/create/',  views.rate_card_create,  name='rate_card_create'),
    path('rate-cards/<int:pk>/delete/', views.rate_card_delete, name='rate_card_delete'),
    path('rate-cards/<int:pk>/toggle/', views.rate_card_toggle, name='rate_card_toggle'),
    path('contacts/',           views.contact_list,      name='contacts'),
    path('exec-log/',           views.exec_log,          name='exec_log'),
    path('api/area-preview/',   api_views.area_preview,      name='area_preview'),
    path('api/building/',       api_views.building_lookup,   name='building_lookup'),
    path('api/lidar-analyze/',  api_views.lidar_analyze,     name='lidar_analyze'),
    path('api/solar-analyze/',  views.solar_analyze,      name='solar_analyze'),
    path('api/roof-drawing/',   views.roof_drawing_analyze, name='roof_drawing'),
    path('api/roof-correction/', api_views.roof_correction_save, name='roof_correction_save'),
    path('api/manual-ground-truth/', api_views.manual_ground_truth_save, name='manual_ground_truth_save'),
    # Purchase Orders
    path('quotes/<int:pk>/purchase/',        views.purchase_compare,       name='purchase_compare'),
    path('quotes/<int:pk>/purchase/create/', views.purchase_order_create,  name='purchase_order_create'),
    path('purchase-orders/',                 views.purchase_order_list,    name='purchase_order_list'),
    path('purchase-orders/<int:po_pk>/',     views.purchase_order_detail,  name='purchase_order_detail'),
    path('purchase-orders/<int:po_pk>/print/', views.purchase_order_print, name='purchase_order_print'),
    # Price check log
    path('price-check-log/', views.price_check_log, name='price_check_log'),
    path('api/vendor-prices/', api_views.api_vendor_prices, name='api_vendor_prices'),
]
