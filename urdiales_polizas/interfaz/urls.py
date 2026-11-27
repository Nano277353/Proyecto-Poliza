from django.urls import path

from . import views

app_name = 'interfaz'
urlpatterns = [
    path('', views.cartera, name='cartera'),
    path('historico/', views.historico, name='historico'),
    path('asegurados/', views.asegurados, name='asegurados'),
    path('asegurados/nuevo/', views.asegurado_formulario, name='asegurado_nuevo'),
    path('asegurados/<int:asegurado_id>/', views.asegurado_detalle, name='asegurado_detalle'),
    path('asegurados/<int:asegurado_id>/editar/', views.asegurado_formulario, name='asegurado_editar'),
    path('aseguradoras/', views.aseguradoras, name='aseguradoras'),
    path('polizas/nueva/', views.poliza_formulario, name='poliza_nueva'),
    path('polizas/<int:poliza_id>/', views.poliza_detalle, name='poliza_detalle'),
    path('polizas/<int:poliza_id>/editar/', views.poliza_formulario, {'modo': 'editar'}, name='poliza_editar'),
    path('polizas/<int:poliza_id>/renovar/', views.poliza_formulario, {'modo': 'renovar'}, name='poliza_renovar'),
    path('polizas/<int:poliza_id>/cancelar/', views.poliza_cancelar, name='poliza_cancelar'),
    path('cobros/<int:cobro_id>/', views.cobro_estado, name='cobro_estado'),
]
