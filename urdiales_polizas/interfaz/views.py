from django.contrib import messages
from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from urdiales_polizas.aplicacion import servicios
from .forms import (AseguradoForm, AseguradoraForm, CobrosFormSet, ContactosFormSet,
                    FiltrosForm, PolizaForm, VehiculosFormSet)


def _obtener(funcion, identificador):
    try:
        return funcion(identificador)
    except ValueError as error:
        raise Http404(str(error)) from error


def _error(formulario, error):
    mensaje = str(error) if isinstance(error, ValueError) else 'No se pudieron guardar los cambios. Revisa los datos e intenta otra vez.'
    formulario.add_error(None, mensaje)


def _filas(formset):
    return [dict(f.cleaned_data) for f in formset
            if f.cleaned_data and not f.cleaned_data.get('DELETE')]


def _cartera(request, es_historico=False):
    error_lectura = False
    try:
        asegurados = servicios.listar_asegurados()
        aseguradoras = servicios.listar_aseguradoras()
    except (ValueError, DatabaseError):
        asegurados, aseguradoras = [], []
        error_lectura = True
    formulario = FiltrosForm(request.GET or None, asegurados=asegurados, aseguradoras=aseguradoras)
    registros = []
    if not error_lectura and (not request.GET or formulario.is_valid()):
        filtros = formulario.cleaned_data if formulario.is_bound else {}
        try:
            registros = servicios.consultar_cartera(filtros, historico=es_historico)
        except (ValueError, DatabaseError):
            error_lectura = True
    return render(request, 'interfaz/cartera.html', {
        'form': formulario, 'registros': registros, 'es_historico': es_historico,
        'error_lectura': error_lectura, 'seccion': 'historico' if es_historico else 'cartera',
        'titulo': 'Histórico' if es_historico else 'Cartera de pólizas',
    })


def cartera(request):
    return _cartera(request)


def historico(request):
    return _cartera(request, es_historico=True)


def asegurados(request):
    busqueda = request.GET.get('q', '').strip()
    return render(request, 'interfaz/asegurados.html', {
        'titulo': 'Asegurados', 'seccion': 'asegurados', 'busqueda': busqueda,
        'asegurados': servicios.listar_asegurados(busqueda),
    })


def asegurado_detalle(request, asegurado_id):
    asegurado = _obtener(servicios.obtener_asegurado, asegurado_id)
    filtros = {'asegurado_id': asegurado_id}
    return render(request, 'interfaz/asegurado_detalle.html', {
        'titulo': asegurado['nombre'], 'seccion': 'asegurados', 'asegurado': asegurado,
        'actuales': servicios.consultar_cartera(filtros),
        'anteriores': servicios.consultar_cartera(filtros, historico=True),
    })


@require_http_methods(['GET', 'POST'])
def asegurado_formulario(request, asegurado_id=None):
    asegurado = _obtener(servicios.obtener_asegurado, asegurado_id) if asegurado_id else None
    iniciales = []
    if asegurado:
        for contacto in asegurado['contactos']:
            iniciales.append({**contacto, 'telefonos': '\n'.join(contacto['telefonos']),
                             'correos': '\n'.join(contacto['correos'])})
    form = AseguradoForm(request.POST if request.method == 'POST' else None, initial=asegurado)
    contactos = ContactosFormSet(request.POST if request.method == 'POST' else None,
                                 initial=iniciales, prefix='contactos')
    if request.method == 'POST' and form.is_valid() and contactos.is_valid():
        try:
            identificador = servicios.guardar_asegurado(form.cleaned_data['nombre'], _filas(contactos), asegurado_id)
        except (ValueError, DatabaseError) as error:
            _error(form, error)
        else:
            messages.success(request, 'Los datos del asegurado se guardaron correctamente.')
            return redirect('interfaz:asegurado_detalle', asegurado_id=identificador)
    return render(request, 'interfaz/asegurado_formulario.html', {
        'titulo': 'Editar asegurado' if asegurado else 'Nuevo asegurado', 'seccion': 'asegurados',
        'form': form, 'contactos': contactos, 'asegurado': asegurado,
    })


@require_http_methods(['GET', 'POST'])
def aseguradoras(request):
    form = AseguradoraForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            servicios.guardar_aseguradora(form.cleaned_data['nombre'])
        except (ValueError, DatabaseError) as error:
            _error(form, error)
        else:
            messages.success(request, 'La aseguradora está disponible en el catálogo.')
            return redirect('interfaz:aseguradoras')
    return render(request, 'interfaz/aseguradoras.html', {
        'titulo': 'Aseguradoras', 'seccion': 'aseguradoras', 'form': form,
        'aseguradoras': servicios.listar_aseguradoras(),
    })


def _vigencia_trabajo(poliza):
    return next((v for v in poliza['vigencias'] if not v['archivada']), None)


def poliza_detalle(request, poliza_id):
    poliza = _obtener(servicios.obtener_poliza, poliza_id)
    actual = _vigencia_trabajo(poliza)
    vigencia = actual or (poliza['vigencias'][0] if poliza['vigencias'] else None)
    if request.GET.get('vigencia'):
        try:
            identificador = int(request.GET['vigencia'])
        except ValueError as error:
            raise Http404('Vigencia no encontrada.') from error
        vigencia = next((v for v in poliza['vigencias'] if v['id'] == identificador), None)
        if vigencia is None:
            raise Http404('Esta vigencia no pertenece a la póliza.')
    editable = bool(vigencia and not vigencia['archivada'] and poliza['estado'] != 'cancelada')
    cobros_editables = bool(vigencia and not vigencia['archivada'] and poliza['estado'] != 'cancelada')
    return render(request, 'interfaz/poliza_detalle.html', {
        'titulo': vigencia['numero_poliza'] if vigencia else 'Póliza', 'seccion': 'cartera',
        'poliza': poliza, 'vigencia': vigencia, 'actual': actual, 'editable': editable,
        'cobros_editables': cobros_editables,
    })


@require_http_methods(['GET', 'POST'])
def poliza_formulario(request, poliza_id=None, modo='crear'):
    poliza = _obtener(servicios.obtener_poliza, poliza_id) if poliza_id else None
    actual = _vigencia_trabajo(poliza) if poliza else None
    if poliza and (poliza['estado'] == 'cancelada' or actual is None):
        messages.error(request, 'Esta póliza no admite cambios ni renovaciones.')
        return redirect('interfaz:poliza_detalle', poliza_id=poliza_id)
    inicial = {'asegurado_id': request.GET.get('asegurado', ''), 'aplica_anticipacion': True}
    vehiculos_iniciales, cobros_iniciales = [], []
    if poliza:
        inicial = {**poliza, **actual, 'vigencia_id': actual['id']}
        inicial['descripcion_riesgo'] = poliza['descripcion_riesgo']
        inicial['responsable'] = poliza['responsable']
        inicial['aplica_anticipacion'] = poliza['aplica_anticipacion']
        vehiculos_iniciales = actual['vehiculos']
        cobros_iniciales = actual['cobros'] if modo == 'editar' else []
        if modo == 'renovar':
            inicial['fecha_inicio'] = actual['fecha_fin']
            inicial['fecha_fin'] = None
    post = request.POST if request.method == 'POST' else None
    form = PolizaForm(post, initial=inicial, asegurados=servicios.listar_asegurados(),
                      aseguradoras=servicios.listar_aseguradoras(), modo=modo)
    vehiculos = VehiculosFormSet(post, initial=vehiculos_iniciales, prefix='vehiculos')
    cobros = CobrosFormSet(post, initial=cobros_iniciales, prefix='cobros')
    if request.method == 'POST':
        formulario_valido = form.is_valid()
        autos = form.cleaned_data.get('tipo') == 'autos'
        vehiculos_validos = vehiculos.is_valid() if autos else True
        cobros_validos = cobros.is_valid()
        if formulario_valido and vehiculos_validos and cobros_validos:
            datos = dict(form.cleaned_data)
            unidades = _filas(vehiculos) if autos else []
            pagos = _filas(cobros)
            try:
                if modo == 'renovar':
                    nueva_id = servicios.renovar_poliza(poliza_id, datos['vigencia_id'], datos, unidades, pagos)
                    messages.success(request, 'La renovación se registró y el periodo anterior quedó en el histórico.')
                    return redirect(reverse('interfaz:poliza_detalle', args=[poliza_id]) + f'?vigencia={nueva_id}')
                identificador = servicios.guardar_poliza(datos, unidades, pagos, poliza_id, datos.get('vigencia_id'))
            except (ValueError, DatabaseError) as error:
                _error(form, error)
            else:
                messages.success(request, 'La póliza se guardó correctamente.')
                return redirect('interfaz:poliza_detalle', poliza_id=identificador)
    titulos = {'crear': 'Nueva póliza', 'editar': 'Editar póliza', 'renovar': 'Registrar renovación'}
    return render(request, 'interfaz/poliza_formulario.html', {
        'titulo': titulos[modo], 'seccion': 'cartera', 'form': form, 'vehiculos': vehiculos,
        'cobros': cobros, 'poliza': poliza, 'modo': modo,
    })


@require_http_methods(['GET', 'POST'])
def poliza_cancelar(request, poliza_id):
    poliza = _obtener(servicios.obtener_poliza, poliza_id)
    if request.method == 'POST' and request.POST.get('confirmar') == 'si':
        try:
            servicios.cancelar_poliza(poliza_id)
        except (ValueError, DatabaseError) as error:
            messages.error(request, str(error) if isinstance(error, ValueError) else 'No se pudo cancelar. Intenta otra vez.')
        else:
            messages.success(request, 'La póliza se canceló. Sus periodos se conservan en el histórico.')
        return redirect('interfaz:poliza_detalle', poliza_id=poliza_id)
    return render(request, 'interfaz/cancelar.html', {
        'titulo': 'Cancelar póliza', 'seccion': 'cartera', 'poliza': poliza,
    })


@require_POST
def cobro_estado(request, cobro_id):
    try:
        poliza_id = servicios.actualizar_cobro(cobro_id, request.POST.get('estado', ''))
    except (ValueError, DatabaseError) as error:
        messages.error(request, str(error) if isinstance(error, ValueError) else 'No se pudo actualizar el cobro. Intenta otra vez.')
        return redirect('interfaz:cartera')
    messages.success(request, 'El estado del cobro se actualizó.')
    return redirect('interfaz:poliza_detalle', poliza_id=poliza_id)
